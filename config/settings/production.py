import os


DEBUG = False

# Activar solo para pruebas por IP y HTTP. Por defecto las cookies requieren HTTPS.
_allow_http = os.getenv('DJANGO_ALLOW_HTTP', 'False').strip().lower() in (
    '1', 'true', 'yes', 'on',
)
SESSION_COOKIE_SECURE = not _allow_http
CSRF_COOKIE_SECURE = not _allow_http
SECURE_HSTS_SECONDS = 0 if _allow_http else 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = not _allow_http
SECURE_HSTS_PRELOAD = not _allow_http


# Conservar localhost para las comprobaciones de salud de Docker.
ALLOWED_HOSTS = ['127.0.0.1', 'localhost'] + [
    host.strip()
    for host in os.getenv('ALLOWED_HOSTS', 'django-leadmanager.onrender.com').split(',')
    if host.strip()
]
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv('CSRF_TRUSTED_ORIGINS', '').split(',')
    if origin.strip()
]

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql_psycopg2',
        'NAME': os.getenv('DB_NAME'),
        'USER': os.getenv('DB_USER'),
        'PASSWORD': os.getenv('DB_PASSWORD'),
        'HOST': os.getenv('DB_HOST'),
        'PORT': os.getenv('DB_PORT'),
        'DISABLE_SERVER_SIDE_CURSORS': True,
    }
}
