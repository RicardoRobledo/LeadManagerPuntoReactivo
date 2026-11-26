"""Ejecuta las pruebas de integración con SQLite en memoria y conexiones de red bloqueadas."""

import argparse
import os
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest import mock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('labels', nargs='*', help='Módulos de pruebas; por defecto, automatizaciones y estados.')
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.base')
    os.environ.setdefault('SECRET_KEY', 'solo-para-pruebas-locales')
    os.environ.setdefault('OPENAI_API_KEY', 'no-usar-red-en-pruebas')

    attempts = []

    def block_network(*args, **kwargs):
        attempts.append(True)
        raise AssertionError('Conexión de red bloqueada durante las pruebas.')

    with ExitStack() as stack:
        for target in ('socket.socket.connect', 'socket.socket.connect_ex', 'socket.create_connection'):
            stack.enter_context(mock.patch(target, side_effect=block_network))

        from django.conf import settings
        settings.DATABASES = {'default': {
            'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:',
        }}
        settings.EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
        settings.CELERY_TASK_ALWAYS_EAGER = False

        import django
        django.setup()
        from django.core.management import call_command

        labels = args.labels or [
            'leadmanager.apps.automations',
            'leadmanager.apps.prospects.test_state_consistency',
            'diagnostico.reproducir_entrega_correos',
        ]
        call_command('check')
        call_command('test', *labels, verbosity=1, interactive=False)
        if attempts:
            raise AssertionError(f'Hubo {len(attempts)} intentos de conexión de red bloqueados.')


if __name__ == '__main__':
    main()
