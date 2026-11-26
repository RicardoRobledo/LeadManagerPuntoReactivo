from django.apps import AppConfig


class AgentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = 'leadmanager.apps.api.agents'
    label = 'api_agents'
