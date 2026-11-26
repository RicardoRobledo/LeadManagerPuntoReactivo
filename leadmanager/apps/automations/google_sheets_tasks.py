"""Tarea de Google Sheets independiente de los pasos de los agentes."""

import logging
from datetime import datetime

from celery import shared_task
from django.db import transaction

from leadmanager.apps.prospects.models import ConversationModel

from .services.google_sheets_service import GoogleSheetsError, register_google_sheet_lead


logger = logging.getLogger(__name__)


@shared_task(
    name='leadmanager.apps.automations.tasks.register_google_sheet_lead_task',
    bind=True, autoretry_for=(GoogleSheetsError,), retry_backoff=True,
    retry_jitter=True, retry_kwargs={'max_retries': 3},
    acks_late=True, reject_on_worker_lost=True,
)
def register_google_sheet_lead_task(self, payload):
    """Envía a Sheets sin repetir IA/Odoo y serializa altas del mismo ID."""
    data = dict(payload)
    conversation_id = data['conversation_id']
    data['received_at'] = datetime.fromisoformat(data['received_at'])
    with transaction.atomic():
        # Dos mensajes de la misma conversación pueden encolar dos tareas.
        # El bloqueo serializa su consulta/alta en los workers de producción.
        conversation = ConversationModel.objects.select_for_update().filter(pk=conversation_id).first()
        if conversation is None:
            logger.info('Google Sheets: conversación %s eliminada; se omite.', conversation_id)
            return {'conversation_id': conversation_id, 'skipped': True}
        record, created = register_google_sheet_lead(**data)
    result = {
        'conversation_id': conversation_id, 'sheet_name': record['sheet_name'],
        'row_number': record['row_number'], 'created': created,
    }
    logger.info('Google Sheets: %s', result)
    return result
