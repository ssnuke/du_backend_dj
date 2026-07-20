#!/bin/sh
# `set -e` so a failed migrate/collectstatic aborts the container instead of
# silently falling through to start daphne anyway. Without this, a failed
# collectstatic run (partial/stale staticfiles.json manifest, missing even
# Django admin's own static files) would still result in the app starting
# and serving traffic — surfacing later as a hard 500 on /admin/ instead of
# a visible, loud deploy failure at the point something actually broke.
set -e

python manage.py migrate --noinput
python manage.py collectstatic --noinput

daphne -b 0.0.0.0 -p "${PORT:-8000}" config.asgi:application
