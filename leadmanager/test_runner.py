"""
Runner de pruebas para modelos con ``managed = False``.

Los modelos de prospects apuntan a tablas creadas fuera de Django (el
esquema vive en Supabase). Eso implica dos cosas al correr pruebas:

1. Con ``managed = False`` Django no crea esas tablas en la base de
   pruebas, y cualquier test que las toque falla con
   "relation does not exist".
2. No basta con marcar los modelos como gestionados: las migraciones
   de la app guardan ``'managed': False`` en su estado, así que al
   aplicarlas las tablas se siguen omitiendo.

Este runner resuelve ambas: marca los modelos como gestionados y anula
las migraciones de las apps afectadas, de modo que Django cree las
tablas directamente desde los modelos (``run_syncdb``). Al terminar
restaura el valor original. No afecta a migraciones ni a producción.

Para activarlo, en settings:

    TEST_RUNNER = 'leadmanager.test_runner.ManagedModelTestRunner'
"""

from django.apps import apps
from django.conf import settings
from django.test.runner import DiscoverRunner


__author__ = 'Ricardo'
__version__ = '0.1'


class ManagedModelTestRunner(DiscoverRunner):

    def setup_test_environment(self, *args, **kwargs):
        self.unmanaged_models = [
            model
            for model in apps.get_models()
            if not model._meta.managed
        ]

        for model in self.unmanaged_models:
            model._meta.managed = True

        # Las migraciones de estas apps declaran los modelos como no
        # gestionados. Al anularlas, Django deja de considerarlas
        # migradas y crea sus tablas a partir de los modelos.
        affected_apps = {
            model._meta.app_label
            for model in self.unmanaged_models
        }

        migration_modules = dict(
            getattr(settings, 'MIGRATION_MODULES', {})
        )

        for app_label in affected_apps:
            migration_modules[app_label] = None

        settings.MIGRATION_MODULES = migration_modules

        super().setup_test_environment(*args, **kwargs)

    def teardown_test_environment(self, *args, **kwargs):
        super().teardown_test_environment(*args, **kwargs)

        for model in self.unmanaged_models:
            model._meta.managed = False
