from django.apps import AppConfig


class AutomationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "leadmanager.apps.automations"

    def ready(self):
        # Carga los prompts de todos los agentes al arrancar la app.
        # El import va dentro de ``ready()``: ``agents/__init__`` importa
        # al agente, y el agente importa modelos. Si se hiciera a nivel de
        # módulo, correría antes de que Django termine de poblarlos y
        # saltaría ``AppRegistryNotReady``.
        from leadmanager.apps.automations.agents.resource_handler import ResourceHandler

        ResourceHandler.load_all()
