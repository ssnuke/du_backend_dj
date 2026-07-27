import io
import os

import cloudinary.uploader
from cloudinary_storage.storage import MediaCloudinaryStorage
from django.core.files.base import ContentFile
from storages.backends.s3boto3 import S3Boto3Storage
from whitenoise.storage import CompressedManifestStaticFilesStorage


class LenientManifestStaticFilesStorage(CompressedManifestStaticFilesStorage):
    """
    WhiteNoise's manifest storage is strict by default: if collectstatic
    didn't run (or ran against a different STATIC_ROOT/environment) before
    the app starts serving traffic, ANY {% static %} tag for a file missing
    from staticfiles.json raises ValueError and 500s the whole page — not
    just a broken image, the entire response. Hit this on /admin/login/,
    which is often the first page anyone loads while debugging a deploy.

    manifest_strict = False makes a missing entry fall back to the plain
    (unhashed) filename instead of raising, so a static-pipeline hiccup
    degrades to a page that renders (possibly with an uncached/unhashed
    asset) instead of a hard crash.
    """
    manifest_strict = False


class AutoCloudinaryStorage(MediaCloudinaryStorage):
    """
    Picks Cloudinary's resource_type (image/video/raw) per file extension
    instead of always uploading as "raw". Raw storage stores files as opaque
    blobs with none of Cloudinary's automatic optimization (no f_auto/q_auto
    format+quality compression, no on-the-fly resized derivatives) — which is
    why bandwidth usage can run far higher than storage usage. Routing
    images/videos through their proper resource type unlocks that pipeline.

    Voice notes (audio-only .webm/.ogg/etc.) intentionally map to "video" —
    Cloudinary has no separate "audio" resource type; audio-only files use
    the video pipeline too.

    This only affects new uploads. Existing ChatMessage.attachment_url /
    Sticker.image_url values already store a fully-resolved Cloudinary URL
    from upload time, so nothing needs to change for already-uploaded files.
    """

    IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"}
    AV_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".ogg", ".mp3", ".wav", ".m4a"}

    def _get_resource_type(self, name):
        ext = os.path.splitext(name)[1].lower()
        if ext in self.IMAGE_EXTENSIONS:
            return "image"
        if ext in self.AV_EXTENSIONS:
            return "video"
        return "raw"

    def _save(self, name, content):
        # Cloudinary strips the extension from the public_id it returns for
        # image/video uploads (format is tracked separately on their end), so
        # the name Django stores loses its extension entirely. Later calls to
        # url()/delete() re-derive resource_type from that stored name via
        # _get_resource_type() above — without the extension they always fall
        # through to "raw", producing a broken delivery URL (wrong
        # resource_type segment) for an asset Cloudinary actually stored as
        # image/video. Re-attach the original extension so type detection
        # keeps working on every later call, not just at upload time.
        ext = os.path.splitext(name)[1]
        public_id = super()._save(name, content)
        if ext and not public_id.lower().endswith(ext.lower()):
            public_id += ext
        return public_id

    def delete(self, name):
        # The extension _save() re-attaches (see above) is only there so
        # _get_resource_type() keeps working — Cloudinary's *actual* stored
        # public_id for image/video still omits it. Passing the
        # extension-bearing name straight to destroy() would silently target
        # a non-existent public_id (destroy() on a missing resource still
        # reports "ok"-shaped responses in some cases, so this failure mode
        # is easy to miss: nothing gets deleted, and storage quietly leaks).
        resource_type = self._get_resource_type(name)
        public_id = os.path.splitext(name)[0] if resource_type in ("image", "video") else name
        response = cloudinary.uploader.destroy(public_id, invalidate=True, resource_type=resource_type)
        return response.get("result") == "ok"


class R2Storage(S3Boto3Storage):
    """
    Cloudflare R2 (S3-compatible) storage backend — replaces
    AutoCloudinaryStorage as DEFAULT_FILE_STORAGE once Cloudinary's free tier
    ran out. R2 charges zero egress fees, unlike Cloudinary's bundled
    storage+bandwidth credit model, which is what made costs unpredictable.

    R2 has no built-in on-the-fly URL-based resizing (Cloudflare's "Image
    Resizing" feature needs a paid Pro-plan zone). Since every frontend call
    site only ever asks for one of a handful of fixed display sizes, a
    single "-thumb" derivative generated once at upload time — named
    predictably so the frontend can request it via plain string
    manipulation on the original URL, same as it already does for Cloudinary
    transforms — covers all of them without needing a paid resizing service.

    Only affects new uploads. Existing ChatMessage.attachment_url /
    Sticker.image_url / ChatRoom.image_url values already store a
    fully-resolved Cloudinary URL from before this switch, and keep
    resolving directly against Cloudinary's CDN unchanged.
    """

    IMAGE_EXTENSIONS = AutoCloudinaryStorage.IMAGE_EXTENSIONS
    THUMB_MAX_SIDE = 400
    THUMB_QUALITY = 80

    def _save(self, name, content):
        saved_name = super()._save(name, content)
        if os.path.splitext(saved_name)[1].lower() in self.IMAGE_EXTENSIONS:
            self._save_thumbnail(saved_name, content)
        return saved_name

    def _save_thumbnail(self, name, content):
        # Thumbnail generation is a bandwidth optimization, not essential —
        # never let a corrupt/unsupported image fail the actual upload.
        try:
            from PIL import Image, ImageOps

            content.seek(0)
            image = ImageOps.exif_transpose(Image.open(content))
            image.thumbnail((self.THUMB_MAX_SIDE, self.THUMB_MAX_SIDE))

            is_png = os.path.splitext(name)[1].lower() == ".png" and image.mode == "RGBA"
            if not is_png and image.mode not in ("RGB", "L"):
                image = image.convert("RGB")

            buffer = io.BytesIO()
            image.save(buffer, format="PNG" if is_png else "JPEG", quality=self.THUMB_QUALITY)
            buffer.seek(0)
            super()._save(self._thumb_name(name), ContentFile(buffer.read()))
        except Exception:
            pass

    @staticmethod
    def _thumb_name(name):
        root, ext = os.path.splitext(name)
        return f"{root}-thumb{ext}"
