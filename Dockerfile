# syntax=docker/dockerfile:1

FROM python:3.13-slim-bookworm AS dependencies

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY requirements.txt ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install -r requirements.txt

FROM python:3.13-slim-bookworm AS runtime

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=config.settings.base

WORKDIR /app

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /app/media /app/celerybeat \
    && chown -R app:app /app

COPY --from=dependencies /opt/venv /opt/venv
COPY --chown=app:app config/ ./config/
COPY --chown=app:app leadmanager/ ./leadmanager/
COPY --chown=app:app manage.py ./
COPY --chmod=755 docker/entrypoint.sh /usr/local/bin/leadmanager-entrypoint

USER app

# Solo se recopilan archivos locales; estas claves de ejemplo no acceden a servicios.
# Las migraciones se ejecutan al arrancar Compose, nunca durante la construccion.
RUN SECRET_KEY=build-only-not-for-runtime \
    OPENAI_API_KEY=build-only-not-for-runtime \
    python manage.py collectstatic --noinput

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/leadmanager-entrypoint"]
CMD ["uvicorn", "config.asgi:application", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "90"]
