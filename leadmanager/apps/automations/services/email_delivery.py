"""Bitácora de correos independiente de las ejecuciones del agente."""

import logging

from celery import current_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ..models import EmailDeliveryLogModel


logger = logging.getLogger(__name__)


def begin_email_delivery(recipient, subject, kind, *, conversation_id=None, source='', message_id=''):
    """Registra el intento antes de contactar SMTP; si falla, no se inicia el envío."""
    task_id = getattr(getattr(current_task, 'request', None), 'id', None)
    try:
        with transaction.atomic():
            return EmailDeliveryLogModel.objects.create(
                recipient=str(recipient or '')[:320], subject=str(subject or '')[:500],
                kind=kind, source=str(source or '')[:20], conversation_id=conversation_id,
                message_id=message_id, task_id=str(task_id or '')[:255],
            )
    except Exception:
        logger.exception('No se pudo registrar el intento de correo; no se inicia SMTP.')
        return None


def finish_email_delivery(record, status, *, error=None, detail=''):
    """Un error al guardar el resultado no cambia la aceptación real de SMTP."""
    if error is not None:
        detail = str(error)
    secret = getattr(settings, 'EMAIL_HOST_PASSWORD', '')
    if secret:
        detail = detail.replace(secret, '[credencial omitida]')
    try:
        with transaction.atomic():
            record.status = status
            record.finished_at = timezone.now()
            record.error_type = type(error).__name__ if error else ''
            record.error_detail = detail[:4000]
            record.save(update_fields=['status', 'finished_at', 'error_type', 'error_detail'])
    except Exception:
        logger.exception('No se pudo guardar el resultado del intento de correo id=%s.', record.pk)


def record_skipped_email(recipient, subject, kind, *, reason, conversation_id=None, source=''):
    record = begin_email_delivery(recipient, subject, kind, conversation_id=conversation_id, source=source)
    if record is not None:
        finish_email_delivery(record, EmailDeliveryLogModel.StatusEnum.SKIPPED, detail=reason)
    return record
