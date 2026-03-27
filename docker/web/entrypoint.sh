#!/usr/bin/env sh
set -e

run_as_appuser() {
  if [ "${RUN_AS_ROOT:-false}" = "true" ]; then
    exec "$@"
  fi
  if [ "$(id -u)" = "0" ]; then
    exec gosu appuser "$@"
  fi
  exec "$@"
}

run_as_appuser_noexec() {
  if [ "${RUN_AS_ROOT:-false}" = "true" ]; then
    "$@"
    return
  fi
  if [ "$(id -u)" = "0" ]; then
    gosu appuser "$@"
    return
  fi
  "$@"
}

if [ "$(id -u)" = "0" ]; then
  mkdir -p /app/media/uploads /app/staticfiles
  chown -R appuser:appuser /app/media /app/staticfiles
fi

python - <<'PY'
import os
import time

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django
django.setup()

from django.db import connections
from django.db.utils import OperationalError

for attempt in range(30):
    try:
        connection = connections["default"]
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        print("Database connection ready.")
        break
    except OperationalError as exc:
        print(f"Waiting for database ({attempt + 1}/30): {exc}")
        time.sleep(2)
else:
    raise SystemExit("Database did not become ready in time.")
PY

if [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
  run_as_appuser_noexec python manage.py migrate --noinput
fi

if [ "${RUN_COLLECTSTATIC:-false}" = "true" ]; then
  run_as_appuser_noexec python manage.py collectstatic --noinput
fi

run_as_appuser "$@"
