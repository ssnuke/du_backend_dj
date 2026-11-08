#!/usr/bin/env bash
# Step 3 of the Render+Neon -> AIC cutover: copy the Neon database into the AIC database.
#
#   bash cutover_db.sh --check    read-only: are you ready? (changes nothing)
#   bash cutover_db.sh            dump Neon -> WIPE the AIC database -> restore -> compare row counts
#
# Run it only after: (1) Render is suspended, (2) the AIC app is stopped.
# Neon is only ever read. The AIC database is erased and replaced.
set -euo pipefail
cd "$(dirname "$0")"

export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH"   # version 16 tools; the default pg_dump is too old
set -a; . ./.env.aic; set +a                              # NEON_DIRECT_URL (source) and DATABASE_URL (AIC target)

RENDER_HEALTH="https://du-backend-dj.onrender.com/api/health/"
DUMP="$HOME/du_cutover_backup.sql"
MODE="${1:-run}"
NOT_READY=0

ok()   { printf '  \033[32mOK\033[0m         %s\n' "$1"; }
bad()  { printf '  \033[31mNOT READY\033[0m  %s\n' "$1"; NOT_READY=1; }

COUNTS_SQL="SELECT table_name, (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM %I.%I', table_schema, table_name), false, true, '')))[1]::text::int FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name;"

echo "Preflight"

# 1. The two URLs must be the right way round: never wipe Neon.
case "$DATABASE_URL" in *neon.tech*) echo "STOP: DATABASE_URL in .env.aic points at Neon. Refusing to continue."; exit 2;; esac
case "$NEON_DIRECT_URL" in *neon.tech*) ;; *) echo "STOP: NEON_DIRECT_URL in .env.aic is not a Neon address."; exit 2;; esac
case "$NEON_DIRECT_URL" in *-pooler*) echo "STOP: NEON_DIRECT_URL must be the direct host (no -pooler)."; exit 2;; esac
ok "target is the AIC database, source is Neon (direct host)"

# 2. Tools.
v=$(pg_dump --version | sed -E 's/.* ([0-9]+)\..*/\1/')
if [ "$v" -ge 16 ]; then ok "pg_dump version $v"; else bad "pg_dump is version $v; need 16 or newer"; fi

# 3. Both databases reachable.
if psql "$NEON_DIRECT_URL" -Atc "select 1" >/dev/null 2>&1; then ok "Neon reachable"; else bad "cannot connect to Neon"; fi
if psql "$DATABASE_URL" -Atc "select 1" >/dev/null 2>&1; then ok "AIC database reachable"; else bad "cannot connect to the AIC database"; fi

# 4. Render must be suspended: anything it saves after the dump would be lost.
code=$(curl -s -o /dev/null -m 20 -w '%{http_code}' "$RENDER_HEALTH" || true)
if [ "$code" = "200" ]; then bad "Render is still serving requests (health = 200). Suspend it in the Render dashboard first"; else ok "Render is not serving (health = ${code:-no answer})"; fi

# 5. The AIC app must be stopped: it holds connections to the database we are about to wipe.
others=$(psql "$DATABASE_URL" -Atc "select count(*) from pg_stat_activity where datname = current_database() and pid <> pg_backend_pid()" 2>/dev/null || echo "?")
if [ "$others" = "0" ]; then ok "nothing else is connected to the AIC database"; else bad "$others other connection(s) on the AIC database. Stop the AIC app and wait about a minute"; fi

if [ "$MODE" = "--check" ]; then
  echo
  if [ "$NOT_READY" = "0" ]; then echo "Ready. Run:  bash cutover_db.sh"; else echo "Not ready yet. Fix the items above and run --check again."; fi
  exit "$NOT_READY"
fi

if [ "$NOT_READY" != "0" ]; then echo; echo "Not ready; nothing was changed."; exit 1; fi

echo
read -r -p "This ERASES the AIC database and replaces it with a copy of Neon. Type WIPE to continue: " answer
[ "$answer" = "WIPE" ] || { echo "Cancelled; nothing was changed."; exit 0; }

echo
echo "1/4 Dumping Neon (read only)..."
pg_dump "$NEON_DIRECT_URL" --no-owner --no-acl -Fp -f "$DUMP"
tail -n 5 "$DUMP" | grep -q "PostgreSQL database dump complete" || { echo "STOP: the dump did not finish. Nothing was wiped."; exit 1; }
echo "    done: $(du -h "$DUMP" | cut -f1)  $DUMP"

echo "2/4 Wiping the AIC database..."
psql "$DATABASE_URL" -q -v ON_ERROR_STOP=1 -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;'

echo "3/4 Restoring into the AIC database (all or nothing)..."
psql "$DATABASE_URL" -q -o /dev/null -v ON_ERROR_STOP=1 --single-transaction -f "$DUMP"

echo "4/4 Comparing row counts..."
psql "$NEON_DIRECT_URL" -At -F'|' -c "$COUNTS_SQL" > "$DUMP.neon_counts"
psql "$DATABASE_URL"    -At -F'|' -c "$COUNTS_SQL" > "$DUMP.aic_counts"
tables=$(wc -l < "$DUMP.aic_counts" | tr -d ' ')
if diff "$DUMP.neon_counts" "$DUMP.aic_counts" >/dev/null; then
  echo "    MATCH: all $tables tables have identical row counts"
  rm -f "$DUMP.neon_counts" "$DUMP.aic_counts"
  echo
  echo "Database copied. Next:"
  echo "  - AIC: make sure CSRF_TRUSTED_ORIGINS is set, then START the AIC app"
  echo "  - check https://du-backend-in.aiccloud.online/api/health/"
  echo "  - then: bash ../react-web-app/switch-backend.sh aic"
  echo "  - afterwards delete the dump (it holds every user's data):  rm \"$DUMP\""
else
  echo "    MISMATCH between Neon and AIC:"
  diff "$DUMP.neon_counts" "$DUMP.aic_counts" || true
  echo "Do NOT switch the frontend. Neon is untouched; resume Render to go back."
  exit 1
fi
