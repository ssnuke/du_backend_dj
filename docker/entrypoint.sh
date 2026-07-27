#!/bin/sh
# `set -e` so a failed migrate/collectstatic aborts the container instead of
# silently falling through to start the app server anyway. Without this, a
# failed collectstatic run (partial/stale staticfiles.json manifest, missing
# even Django admin's own static files) would still result in the app
# starting and serving traffic — surfacing later as a hard 500 on /admin/
# instead of a visible, loud deploy failure at the point something actually
# broke.
set -e

python manage.py migrate --noinput
python manage.py collectstatic --noinput

# A single bare `daphne` process used to serve everything — every REST
# request and every WebSocket connection — through one process's shared
# worker pool. Under a request/notification-fanout burst, that pool would
# saturate and even the health check couldn't get serviced in time, so
# Render killed the instance (see incident: request spike -> health check
# timeout -> instance restart, visible as a memory cliff-drop in metrics).
# gunicorn with uvicorn's ASGI worker class gives real multi-process
# concurrency instead: each worker is a separate OS process (own thread
# pool, own GIL), so one worker being busy can't starve another's health
# check or requests. --preload loads the app once before forking so workers
# share the base import footprint (pandas/Pillow/firebase-admin etc.) via
# copy-on-write instead of each paying it separately.
#
# WEB_CONCURRENCY is deliberately conservative (2) given the 2GB memory
# limit observed in production — raise it via a Render env var (no code
# change needed) once actual per-worker memory headroom is confirmed from
# the Memory graph after this deploys. WebSocket state (Channels' channel
# layer, chat presence) is already Redis-backed, not per-process memory, so
# it's safe for connections to land on different workers.
exec gunicorn config.asgi:application \
    -k uvicorn.workers.UvicornWorker \
    --workers "${WEB_CONCURRENCY:-2}" \
    --bind "0.0.0.0:${PORT:-8000}" \
    --timeout "${GUNICORN_TIMEOUT:-60}" \
    --graceful-timeout 30 \
    --preload
