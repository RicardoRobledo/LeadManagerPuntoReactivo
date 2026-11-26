#!/bin/sh
set -eu

# Codificar la contrasena evita romper las URLs con caracteres como @, / o #.
if [ -n "${REDIS_PASSWORD:-}" ]; then
    redis_password_encoded="$(python -c 'import os; from urllib.parse import quote; print(quote(os.environ["REDIS_PASSWORD"], safe=""))')"
    export CELERY_BROKER_URL="redis://:${redis_password_encoded}@redis:6379/10"
    export CELERY_RESULT_BACKEND="redis://:${redis_password_encoded}@redis:6379/11"
fi

# El proceso principal recibe las senales de parada para cerrar correctamente.
exec "$@"
