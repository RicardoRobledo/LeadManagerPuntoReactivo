import uuid
from pathlib import Path

from django.core.files import File
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError

from leadmanager.apps.automations.models import AgentModel


class Command(BaseCommand):
    help = 'Copia las imágenes originales al almacenamiento de medios y las asigna a los agentes integrados.'

    def handle(self, *args, **options):
        image_dir = Path(__file__).resolve().parents[3] / 'pages' / 'static' / 'imgs'
        for key, filename in (('prospector', 'prospector.png'), ('conversation_analyzer', 'analizer.jpg')):
            agent = AgentModel.objects.filter(key=key).first()
            if not agent:
                raise CommandError(f'No existe el agente {key}.')
            if agent.avatar.startswith('agent-avatars/'):
                self.stdout.write(f'{key}: ya tiene una imagen subida; se conserva {agent.avatar}')
                continue
            source = image_dir / filename
            if not source.is_file():
                raise CommandError(f'No se encontró {source}.')
            with source.open('rb') as image:
                stored = default_storage.save(f'agent-avatars/{uuid.uuid4().hex}{source.suffix}', File(image))
            try:
                agent.avatar = stored
                agent.save(update_fields=['avatar', 'updated_at'])
            except Exception:
                default_storage.delete(stored)
                raise
            self.stdout.write(self.style.SUCCESS(f'{key}: {stored}'))
