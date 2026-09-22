#!/usr/bin/env sh
# Container startup: apply migrations, then serve. Migrating is idempotent,
# so restarting the container is safe.
set -e

python manage.py migrate --noinput

# This is the Django dev server, not a production setup. --insecure makes it
# serve the committed React bundle even with DEBUG off; compose interpolates
# .env, so whether DEBUG is off is the operator's setting, and without the flag
# every page would render blank whenever it is.
exec python manage.py runserver 0.0.0.0:8000 --insecure
