# Third-party services & infrastructure

Reference doc for every external service this app (backend + frontend) depends on — what it's for, current plan/tier, where the config lives, and known limitations to plan around. Keep this updated when a service, plan, or key limitation changes.

Last reviewed: 2026-07-05.

---

## Hosting

### Render — backend (Django/Daphne)
- **What**: hosts the Django backend as a single web service (`du-backend-dj`), Docker-based, running Daphne (ASGI).
- **URL**: `https://du-backend-dj.onrender.com`
- **Repo**: `github.com/ssnuke/du_backend_dj` (this repo, `django-app/`).
- **Config**: `render.yaml` exists in-repo but is **not synced as a Blueprint** — the service was created manually in Render's dashboard, so `render.yaml` is documentation only right now and won't take effect until you sync it there.
- **Region**: Singapore — confirmed against the actual dashboard setting (2026-07-05), already the closest available Render region to India. No change needed; `render.yaml` now documents this explicitly via `region: singapore`.
- **Known limitation**: single instance, no horizontal scaling configured. The app is architecturally ready for it (Redis-backed Channels layer, no per-process in-memory state), but actually running more than one instance requires either bumping "Instance Count" in the dashboard or upgrading to a plan with autoscaling — that's a billing/plan decision, not something fixable in code.
- **Plan ahead**: if traffic grows, this is the first thing to revisit — check current plan's request/connection limits before it becomes a bottleneck.

### Firebase Hosting — frontend (React PWA)
- **What**: hosts the built `react-web-app` static site.
- **Firebase project**: `dreamers-united`
- **Deploy method**: manual `firebase deploy` from the local machine — **`react-web-app/` has no git repository**, so nothing about the frontend is version-controlled or pushed anywhere. Deploys happen straight from whatever's on disk.
- **Plan ahead**: if you want frontend changes to be reviewable/revertible, this needs a git repo (and ideally a CI-based deploy) at some point — currently there's no history of frontend changes at all.

---

## Data & caching

### Postgres (via Render)
- **What**: primary application database.
- **Config**: `DATABASE_URL` env var, read via `dj_database_url` in `config/settings.py`.

### Redis (Redis Cloud — `database-DU-chat`, Essentials/30MB plan)
- **What**: does double duty — (1) the Django Channels layer (WebSocket group messaging/broadcast) and (2) the Django cache framework (room-list caching, added this session).
- **Config**: `REDIS_URL` (channel layer) and `CACHE_REDIS_URL` (cache, falls back to `REDIS_URL` if unset) in `config/settings.py`.
- **Known limitation**: this Redis Cloud plan only exposes a **single logical DB** (no `db=1` etc. available on free/fixed tiers) — the channel layer and cache share the same DB, kept from colliding via a `du_cache` key prefix on the cache side. If you ever move to a Redis plan/instance with multiple DBs, the cache can be moved to its own DB by just setting `CACHE_REDIS_URL` to a different `/N` suffix.
- **Connection limit hit (2026-07-05)**: got a Redis Cloud alert that connections reached 100% of the plan's limit. Root cause: neither `channels_redis` nor `django-redis` capped its connection pool by default, so both would keep opening new connections under load instead of reusing a bounded set. Fixed by adding explicit `max_connections` caps: `REDIS_CHANNEL_MAX_CONNECTIONS` (default 15) and `REDIS_CACHE_MAX_CONNECTIONS` (default 10), both overridable via env var. This bounds *our* connection usage, but the underlying "Essentials/30MB" plan is a very small tier — if 100-200 concurrent users still exhausts the limit even with pooling capped, the plan itself needs upgrading, not just the pool config.

---

## Media storage

### Cloudflare R2 — images, videos, voice notes, stickers, avatars (active)
- **What**: stores and serves all **new** chat media (photos, videos, voice notes, sticker uploads, room/personal avatars) via `core/storage.py`'s `R2Storage`, an S3-compatible backend (`django-storages` + `boto3`) pointed at an R2 bucket.
- **Config**: `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME`, `R2_ENDPOINT_URL`, `R2_PUBLIC_URL` in `config/settings.py` — create the bucket + API token in the Cloudflare dashboard.
- **Why R2 over Cloudinary/Bunny**: R2 charges **zero egress fees**, unlike Cloudinary (storage+bandwidth bundled into the same credit pool — the actual cause of the free-tier exhaustion) or Bunny (small but nonzero per-GB bandwidth charge). For a chat app, bandwidth is the real cost driver, not storage.
- **No on-the-fly resizing**: Cloudflare's Image Resizing feature needs a paid Pro-plan zone, so `R2Storage` instead generates one fixed "-thumb" derivative (400px max side, quality 80) at upload time for image uploads; the frontend (`cldThumb()`/`cldAvatar()` in `react-web-app/src/utils/cloudinaryUrl.js`) requests it by filename convention instead of a dynamic transform. Video/audio files have no thumbnail — the original is always used.
- **Migration note**: only new uploads go to R2 — no bulk migration of existing Cloudinary-hosted assets was done. Old messages/avatars keep resolving directly against Cloudinary's CDN.

### Cloudinary — legacy (old media only, no new uploads)
- **What**: `core/storage.py`'s `AutoCloudinaryStorage` class still exists and its settings are still configured, but it is **no longer** `DEFAULT_FILE_STORAGE` — R2 (above) took over for all new uploads once the free tier ran out.
- **Config**: `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET` in `config/settings.py` — must stay set and the account must stay active, since existing `ChatMessage.attachment_url` / `Sticker.image_url` / `ChatRoom.image_url` values are absolute Cloudinary URLs stored at upload time; they'll break if the account is closed.
- **Plan ahead**: revisit only if/when it's worth writing a one-off bulk-copy script to move old assets to R2 too, so the Cloudinary account can finally be closed. Not urgent — no ongoing cost as long as nothing new is uploaded there.

### Bunny.net Stream — Learn/Dream videos
- **What**: video delivery for the `LearnVideo`/`DreamVideo` admin-curated content models (unrelated to chat media).
- **Config**: `BUNNY_STREAM_API_KEY`, `BUNNY_STREAM_LIBRARY_ID`, `BUNNY_STREAM_CDN_HOSTNAME`, `BUNNY_STREAM_TOKEN_KEY` in `config/settings.py`.
- **Plan ahead**: mentioned above as a possible consolidation target if Cloudinary costs become a problem, since there's already an account/billing relationship here.

---

## Push notifications

### Firebase Cloud Messaging (FCM) — chat push notifications
- **What**: push notifications for new chat messages (`core/utils/firebase_messaging.py`) and general app notifications (`core/utils/notifications.py`).
- **Config**: `FIREBASE_SERVICE_ACCOUNT_PATH` (server-side credential) in `config/settings.py`; frontend Firebase config is in `react-web-app/src/services/fcmService.js` (public config, not secret — standard for Firebase web apps).
- **Fixed this session**:
  - Switched from a hand-rolled sequential per-token send loop to the Admin SDK's real batch API (`send_each_for_multicast`).
  - Push sends no longer block the WebSocket connection (fire-and-forget, dedicated thread pool).
  - Dead/expired FCM tokens now get pruned from `Ir.fcm_tokens` after a failed send (previously only the generic notification path did this).
  - Frontend re-registers the FCM token on app-resume (`visibilitychange`), not just at login/mount, to catch token rotation during long-lived sessions.

---

## Chat features backed by external content

### GIPHY — GIF picker
- **What**: powers the "GIFs" tab in the chat composer's media picker (`core/views/gifs.py`, proxied server-side so the API key is never exposed to the frontend).
- **Config**: `GIPHY_API_KEY` in `config/settings.py` — **must be set** (locally and on Render) or the GIFs tab returns a graceful 502 with no results.
- **Plan**: free tier — 42 searches/hour, 1,000/day. GIPHY's 2024 policy change requires approval for many commercial use cases; real production use will likely need their paid tier (~$99/month for 5,000 requests/day).
- **Note**: Tenor (the original candidate) is not an option — its API shut down entirely in 2026 (new keys blocked Jan 2026, full shutdown June 30 2026).
- **GIF sending doesn't touch Cloudinary at all** — the GIPHY-hosted URL is stored directly as the message's `attachment_url`, so this feature has zero storage/bandwidth cost on our side.

### Sticker packs — self-hosted (Cloudinary)
- **What**: self-serve sticker packs (users create/upload their own) plus admin-curated default packs (via Django admin, `StickerPack`/`Sticker` models) with a subscribe model for a "Browse" catalog of public packs (no duplication of assets between users).
- **Sourcing recommendation** (for admin-curated default packs): open-license sets like OpenMoji/Twemoji/Noto Emoji for zero licensing risk, or IconScout/Flaticon for actual animated character stickers (check per-pack commercial license). Avoid pulling directly from Telegram/Signal sticker exports — most are unlicensed fan content.
- **Upload format**: PNG for static; MP4/WebM preferred over GIF for animated (goes through Cloudinary's video optimization pipeline, smaller than raw GIF for the same motion). Lottie (`.json`) is **not supported** — the picker has no Lottie player, only plain `<img>`/`<video>`.

---

## Quick reference: env vars by service

| Service | Env vars |
|---|---|
| Postgres | `DATABASE_URL` |
| Redis (channels) | `REDIS_URL` |
| Redis (cache) | `CACHE_REDIS_URL` (optional, falls back to `REDIS_URL`) |
| Cloudinary | `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET` |
| Bunny.net Stream | `BUNNY_STREAM_API_KEY`, `BUNNY_STREAM_LIBRARY_ID`, `BUNNY_STREAM_CDN_HOSTNAME`, `BUNNY_STREAM_TOKEN_KEY` |
| Firebase (FCM) | `FIREBASE_SERVICE_ACCOUNT_PATH` |
| Web Push (VAPID) | `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` |
| GIPHY | `GIPHY_API_KEY` |
