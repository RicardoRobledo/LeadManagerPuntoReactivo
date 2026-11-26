from django.apps import AppConfig

from leadmanager.apps.prospects.prompt_handlers import PromptHandler


class ProspectsConfig(AppConfig):

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'leadmanager.apps.prospects'

    def ready(self):

        PromptHandler.set_system_prompt(
            'leadmanager/apps/prospects/prompts/system_prompt.txt'
        )
