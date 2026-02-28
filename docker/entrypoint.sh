#!/bin/sh

python manage.py migrate --noinput
python manage.py collectstatic --noinput

daphne -b 0.0.0.0 -p 8000 config.asgi:application
