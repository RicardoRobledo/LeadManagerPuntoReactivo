"""Prueba un recordatorio real y único al correo extraído del esquema CRM."""

import argparse
import json
import os
import sys
from datetime import timedelta
from pathlib import Path
from unittest import mock
from uuid import uuid4


MARKER = 'prueba-recordatorio-schema-nextwave-unico'
SUBJECT = '[PRUEBA] Recordatorio desde el esquema CRM'


def probar(email, *, enviar=False):
    from django.conf import settings
    from django.core.validators import validate_email
    from django.db import transaction
    from django.utils import timezone

    from leadmanager.apps.automations import tasks
    from leadmanager.apps.automations.models import EmailDeliveryLogModel
    from leadmanager.apps.automations.services.conversation_reminders import (
        latest_schema_emails, reminder_recipient,
    )
    from leadmanager.apps.prospects.models import (
        ChatHistoryModel, ConversationModel, ProspectModel,
    )
    from leadmanager.apps.prospects.qualification import conversation_semaforos

    email = email.strip()
    validate_email(email)
    existing = list(ConversationModel.objects.filter(utm_campaign=MARKER)[:2])
    if len(existing) > 1:
        raise RuntimeError('Hay más de una conversación con el marcador de esta prueba.')
    if existing:
        conversation = existing[0]
        if enviar:
            # Una prueba ya ejecutada puede conservar la fecha de hace 49 horas.
            # Se muestra hoy sin generar un segundo intento.
            ConversationModel.objects.filter(pk=conversation.pk).update(created_at=timezone.now())
        attempt = EmailDeliveryLogModel.objects.filter(
            conversation=conversation, kind='reminder',
        ).order_by('-id').first()
        return {
            'accion': 'prueba_existente; no se reenvió',
            'conversacion_id': conversation.pk,
            'abrir_conversacion': f'/leadmanager-admin/conversations/?interval=today&semaforo=amarillo&open_conversation={conversation.pk}',
            'semaforo': conversation_semaforos(ConversationModel.objects.filter(pk=conversation.pk)).get(conversation.pk),
            'intento_id': attempt.pk if attempt else None,
            'abrir_en_correos': f'/leadmanager-admin/automations/emails/{attempt.pk}/' if attempt else None,
            'estado': attempt.status if attempt else None,
            'destinatario': attempt.recipient if attempt else None,
            'detalle': attempt.error_detail if attempt else '',
        }
    if not enviar:
        return {'accion': 'vista_previa; no se creó ni envió nada', 'destinatario': email,
                'canal': 'form', 'semaforo': 'amarillo'}
    if not all((settings.EMAIL_HOST, settings.EMAIL_HOST_USER, settings.EMAIL_HOST_PASSWORD)):
        raise RuntimeError('Falta configuración SMTP; no se creó la conversación de prueba.')

    now = timezone.now()
    with transaction.atomic():
        prospect = ProspectModel.objects.create(
            name=f'[PRUEBA] {email}'[:255], email=None,
        )
        conversation = ConversationModel.objects.create(
            source='form', session_id=f'prueba-schema-{uuid4().hex}',
            prospect=prospect, state='open',
            utm_source='prueba_recordatorio_schema', utm_campaign=MARKER,
            utm_content='form-amarillo',
        )
        ChatHistoryModel.objects.create(conversation=conversation, message={
            'role': 'user', 'content': '[PRUEBA] Conversación ficticia para comprobar un recordatorio.',
        })
        payload = {
            'reply_to_customer_esp': '[PRUEBA] Sin respuesta comercial.',
            'reply_to_customer': '[PRUEBA] Sin respuesta comercial.',
            'conversation_state': 'descubrimiento',
            'crm_update': {
                'unidad_negocio_detectada': 'no_determinado',
                'bant': {'authority': '', 'need': 'Prueba de recordatorio', 'timing': ''},
                'contacto': {'nombre': 'Contacto de prueba', 'telefono': None,
                             'correo': email, 'empresa': None},
                'resumen_empresa': '', 'orientacion_decision': 'no_determinado',
                'necesidad_certidumbre': 'media', 'sesgos_detectados': [],
                'score_comercial': 0, 'semaforo': 'amarillo',
                'motivo_semaforo': 'Prueba controlada del recordatorio.',
                'perfil_interaccion': 'no_identificado', 'enviar_adjunto': False,
                'etiqueta_perfil_psicologico': '', 'guia_negociacion_sugerida': '',
                'resumen_conversacion': 'Conversación ficticia de prueba.',
                'escalar_a_humano': False, 'motivo_escalamiento': None,
            },
        }
        from leadmanager.apps.automations.agents.prospector_agent.schemas.answer_schema import AnswerSchema
        AnswerSchema.model_validate(payload)
        ChatHistoryModel.objects.create(conversation=conversation, message={
            'role': 'assistant', 'content': json.dumps(payload, ensure_ascii=False),
        })
        schema_email = latest_schema_emails(ConversationModel.objects.filter(pk=conversation.pk)).get(conversation.pk)
        recipient, origin = reminder_recipient(conversation, schema_email)
        if recipient != email or origin != 'crm_update.contacto.correo':
            raise RuntimeError('El correo del esquema no fue seleccionado; no se creó la prueba.')
        if conversation_semaforos(ConversationModel.objects.filter(pk=conversation.pk)).get(conversation.pk) != 'amarillo':
            raise RuntimeError('La conversación no quedó amarilla; no se creó la prueba.')

    ConversationModel.objects.filter(pk=conversation.pk).update(created_at=now - timedelta(hours=49))
    result = None
    try:
        scope = ConversationModel.objects.filter(pk=conversation.pk)
        with (
            mock.patch.object(ConversationModel.objects, 'all', return_value=scope),
            mock.patch.object(tasks, 'REMINDER_SUBJECT', SUBJECT),
            mock.patch.object(tasks, 'REMINDER_BODY',
                              f'Prueba controlada del recordatorio para la conversación #{conversation.pk}.'),
        ):
            result = tasks.send_reminders_to_stale_conversations.run()
    finally:
        # La tarea lo vio con 49 horas; la interfaz abre filtrada en "Hoy".
        # También evita un reintento inmediato si SMTP falló.
        ConversationModel.objects.filter(pk=conversation.pk).update(created_at=timezone.now())

    attempt = EmailDeliveryLogModel.objects.filter(
        conversation=conversation, kind='reminder',
    ).order_by('-id').first()
    return {
        'accion': 'un intento real', 'conversacion_id': conversation.pk,
        'abrir_conversacion': f'/leadmanager-admin/conversations/?interval=today&semaforo=amarillo&open_conversation={conversation.pk}',
        'semaforo': 'amarillo', 'origen_destinatario': origin,
        'destinatario': recipient, 'resultado_tarea': result,
        'intento_id': attempt.pk if attempt else None,
        'abrir_en_correos': f'/leadmanager-admin/automations/emails/{attempt.pk}/' if attempt else None,
        'estado': attempt.status if attempt else None,
        'detalle': attempt.error_detail if attempt else '',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--email', required=True, help='Buzón real que recibirá la prueba.')
    parser.add_argument('--enviar', action='store_true', help='Crea una conversación y envía una prueba real.')
    parser.add_argument('--smtp-env', type=Path,
                        help='Archivo local de configuración SMTP; solo se leen las variables EMAIL_*.')
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    if args.smtp_env:
        from dotenv import dotenv_values
        smtp_config = dotenv_values(args.smtp_env)
        for key in ('EMAIL_HOST', 'EMAIL_PORT', 'EMAIL_HOST_USER',
                    'EMAIL_HOST_PASSWORD', 'EMAIL_USE_TLS', 'EMAIL_FROM_NAME'):
            if smtp_config.get(key):
                os.environ[key] = smtp_config[key]
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.base')
    import django
    django.setup()
    print(json.dumps(probar(args.email, enviar=args.enviar), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
