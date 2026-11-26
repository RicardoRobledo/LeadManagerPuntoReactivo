from django.core.management.base import BaseCommand
from django.db import transaction

from leadmanager.apps.automations.agent_catalog import seed_agents


class Command(BaseCommand):
    help = 'Registra el prospector y el analizador conservando sus configuraciones guardadas.'

    @transaction.atomic
    def handle(self, *args, **options):
        for agent, created in seed_agents():
            action = 'creado' if created else 'ya registrado; se conserva su configuración'
            self.stdout.write(self.style.SUCCESS(f'{agent.name}: {action}.'))
        self.stdout.write(self.style.SUCCESS('Unidad predeterminada NO_DETERMINADO verificada para el prospector.'))
