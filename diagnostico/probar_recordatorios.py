"""Prueba el lote ficticio con rollback; el envío real requiere --enviar-a explícito."""

import argparse
import json
import logging
import os
import re
import sys
from contextlib import ExitStack, contextmanager
from functools import partial
from pathlib import Path
from unittest import mock


@contextmanager
def _escenario_con_rollback(ids, enviar_a, intentos_guardados):
    """Revierte los datos ficticios y conserva la evidencia de los envíos reales."""
    from django.db import transaction
    from leadmanager.apps.automations.models import EmailDeliveryLogModel

    registros = []
    try:
        with transaction.atomic():
            ultimo = (EmailDeliveryLogModel.objects.order_by('-pk').values_list('pk', flat=True).first() or 0) if enviar_a else 0
            try:
                yield
            finally:
                if enviar_a:
                    registros = list(EmailDeliveryLogModel.objects.filter(
                        pk__gt=ultimo, conversation_id__in=ids,
                        recipient=enviar_a, kind='reminder', subject__startswith='[PRUEBA]',
                    ).values(
                        'id', 'conversation_id', 'source', 'recipient', 'subject', 'kind',
                        'status', 'message_id', 'task_id', 'error_type', 'error_detail',
                        'created_at', 'finished_at',
                    ))
                transaction.set_rollback(True)
    finally:
        # Son los resultados del servicio SMTP, incluidos fallos; no se simulan estados.
        # El rollback protege prospectos y semáforos, pero no debe borrar la bitácora.
        if registros:
            with transaction.atomic():
                for datos in registros:
                    inicio = datos.pop('created_at')
                    intento = EmailDeliveryLogModel.objects.create(**datos)
                    EmailDeliveryLogModel.objects.filter(pk=intento.pk).update(created_at=inicio)
                    intentos_guardados.append(intento.pk)


def simular(lote, todos_en_proceso=False, enviar_a=None):
    from django.core.validators import validate_email
    from django.utils import timezone
    from diagnostico.preparar_recordatorios import inspeccionar
    from leadmanager.apps.automations import tasks
    from leadmanager.apps.automations.services import imap_service
    from leadmanager.apps.automations.tasks import send_reminders_to_stale_conversations
    from leadmanager.apps.prospects.models import ChatHistoryModel, ConversationModel, ProspectModel

    logger = logging.getLogger('prueba_recordatorios_ejecucion')
    if enviar_a is not None:
        enviar_a = enviar_a.strip()
        validate_email(enviar_a)
        if not todos_en_proceso:
            raise ValueError('El envío real de prueba requiere todos_en_proceso=True.')
    conversations = list(ConversationModel.objects.filter(
        utm_campaign=lote, utm_source='prueba_recordatorios',
    ).select_related('prospect').order_by('pk'))
    if len(conversations) != 6 or any(not c.prospect_id for c in conversations):
        raise RuntimeError('Se esperaban seis conversaciones ficticias con sus prospectos.')
    ids = [c.pk for c in conversations]
    prospect_ids = [c.prospect_id for c in conversations]
    for conversation in conversations:
        expected = f'{lote}-{conversation.utm_content}@example.test'
        if conversation.prospect.email not in (None, expected):
            raise RuntimeError('El lote contiene un contacto distinto al ficticio; no se modifica.')
        if conversation.prospect.conversations.exclude(utm_campaign=lote).exists():
            raise RuntimeError('Un prospecto se utiliza fuera del lote ficticio; no se modifica.')

    def snapshot():
        return {
            'conversaciones': list(ConversationModel.objects.filter(pk__in=ids).order_by('pk').values(
                'pk', 'state', 'created_at', 'email_reminder_phase', 'session_id', 'prospect_id',
            )),
            'contactos': list(ProspectModel.objects.filter(pk__in=prospect_ids).order_by('pk').values('pk', 'email')),
            'mensajes': list(ChatHistoryModel.objects.filter(conversation_id__in=ids).order_by('pk').values_list('pk', flat=True)),
        }

    before = snapshot()
    scenarios = [('Estados actuales', False)]
    if todos_en_proceso:
        scenarios.append(('Una amarilla por canal, solo dentro de esta transacción', True))
    if enviar_a:
        # Solo un envío por canal; no se repite antes el escenario de un correo.
        scenarios = [('ENVÍO REAL DE PRUEBA: una amarilla por canal', True)]
    results = []
    accepted = []
    failed = []
    audit_ids = []
    with ExitStack() as stack:
        if enviar_a:
            real_recipient = tasks.reminder_recipient
            real_sender = imap_service.send_reminder_email
            allowed = {c.pk: c for c in conversations if c.utm_content in (
                'form-rojo', 'web-sin-semaforo', 'email-amarillo',
            )}
            if len(allowed) != 3 or {c.source for c in allowed.values()} != {'form', 'web', 'email'}:
                raise RuntimeError('Se esperaba exactamente una prueba por canal; no se envió nada.')
            current = {}
            attempted = set()

            def redirect_recipient(conversation, schema_email=None):
                address, origin = real_recipient(conversation, schema_email)
                expected = f'{lote}-{conversation.utm_content}@example.test'
                if conversation.pk not in allowed or address != expected:
                    raise RuntimeError('Se bloqueó un destinatario fuera de los tres casos ficticios.')
                current['conversation'] = conversation
                return enviar_a, f'{origin} (redirigido a correo autorizado de prueba)'

            def send_to_test_address(to, subject, body, **metadata):
                conversation = current.get('conversation')
                if to != enviar_a or not conversation or conversation.pk not in allowed:
                    raise RuntimeError('Se bloqueó un envío fuera del destinatario de prueba.')
                if conversation.pk in attempted or len(attempted) >= 3:
                    raise RuntimeError('Se bloqueó un envío duplicado o superior a tres correos.')
                attempted.add(conversation.pk)
                channel = {'form': 'Formulario', 'web': 'Chatweb', 'email': 'Correo'}[conversation.source]
                logger.info('ENVÍO REAL DE PRUEBA: id=%s | canal=%s | único destinatario=%s',
                            conversation.pk, channel, enviar_a)
                sent = real_sender(
                    enviar_a,
                    f'[PRUEBA][{channel}] {subject}',
                    f'Prueba de recordatorio\nCanal: {channel}\nConversación: {conversation.pk}\n\n{body}',
                    **metadata,
                )
                (accepted if sent else failed).append(conversation.pk)
                return sent

            stack.enter_context(mock.patch.object(tasks, 'reminder_recipient', side_effect=redirect_recipient))
            stack.enter_context(mock.patch.object(tasks, 'send_reminder_email', side_effect=send_to_test_address))
            stack.enter_context(mock.patch.object(
                imap_service.smtplib, 'SMTP', partial(imap_service.smtplib.SMTP, timeout=25),
            ))
        else:
            smtp = stack.enter_context(mock.patch(
                'leadmanager.apps.automations.tasks.send_reminder_email',
                return_value=True,
            ))
            real_smtp_guard = stack.enter_context(mock.patch(
                'leadmanager.apps.automations.services.imap_service.send_reminder_email',
                side_effect=AssertionError('La simulación no debe intentar SMTP real.'),
            ))
        for label, yellow in scenarios:
            logger.info('ESCENARIO: %s | IDs=%s', label, ids)
            with _escenario_con_rollback(ids, enviar_a, audit_ids):
                # Los workers ven los contactos originales sin habilitar.
                # Estos destinatarios ficticios solo existen en esta transacción.
                for conversation in conversations:
                    expected = f'{lote}-{conversation.utm_content}@example.test'
                    ProspectModel.objects.filter(pk=conversation.prospect_id).update(email=expected)
                    if yellow and conversation.utm_content in ('form-rojo', 'web-sin-semaforo'):
                        ChatHistoryModel.objects.create(conversation=conversation, message={
                            'role': 'assistant', 'content': {'crm_update': {'semaforo': 'amarillo'}},
                        })
                rows = inspeccionar(lote, timezone.now())
                for row in rows:
                    logger.info(
                        'Evaluación: id=%s | canal=%s | semaforo=%s | cumple_filtros=%s | %s',
                        row['id'], row['canal'], row['semaforo'] or 'sin semáforo',
                        row['cumple_filtros'], '; '.join(row['motivos']) or 'Listo para simulación',
                    )
                # El alcance y la simulación pertenecen a este script manual,
                # no a los parámetros ni al código de la tarea de producción.
                scope = ConversationModel.objects.filter(pk__in=ids)
                previous_calls = smtp.call_count if not enviar_a else 0
                with mock.patch.object(ConversationModel.objects, 'all', return_value=scope):
                    result = send_reminders_to_stale_conversations.run()
                if not enviar_a:
                    result = {
                        'procesados': 0, 'dry_run': True,
                        'candidatos': sum(row['cumple_filtros'] for row in rows),
                        'simulados': smtp.call_count - previous_calls,
                        'sin_correo_valido': sum(row['cumple_filtros'] and not row['destinatario_habilitado'] for row in rows),
                    }
                results.append({
                    'escenario': label, 'resultado': result,
                    'evaluacion': rows,
                    'ids_simulados' if not enviar_a else 'ids_seleccionados': [
                        row['id'] for row in rows if row['podria_intentar_envio']],
                })
            logger.info('ROLLBACK completado: contactos y semáforos originales conservados.')
        if not enviar_a:
            real_smtp_guard.assert_not_called()
    if snapshot() != before:
        raise RuntimeError('Los datos del lote cambiaron durante la prueba; revisar antes de continuar.')
    logger.info('Verificación final: fases, contactos, fechas e historiales intactos. '
                'Aceptados por SMTP=%s | fallidos=%s.', len(accepted), len(failed))
    if enviar_a:
        logger.info('Intentos conservados en la pantalla Correos: %s', audit_ids)
    return {
        'lote': lote, 'smtp_real': bool(enviar_a), 'destinatario_prueba': enviar_a,
        'datos_originales_conservados': True, 'escenarios': results,
        'ids_aceptados_smtp': accepted, 'ids_fallidos': failed,
        'ids_bitacora': audit_ids,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lote', default='prueba-recordatorios-20261022')
    parser.add_argument('--todos-en-proceso', action='store_true', help='Compara también una amarilla por cada canal.')
    parser.add_argument('--enviar-a', help='Envía tres correos reales de prueba solo a esta dirección.')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z0-9-]{1,60}', args.lote):
        parser.error('--lote no es válido.')
    if args.enviar_a and not args.todos_en_proceso:
        parser.error('--enviar-a requiere --todos-en-proceso.')
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.base')
    import django
    django.setup()
    from django.db import connection
    from django.utils import timezone
    if connection.vendor == 'postgresql':
        connection.settings_dict.setdefault('OPTIONS', {})['connect_timeout'] = 10

    suffix = 'envio-real' if args.enviar_a else 'ejecucion'
    stamp = timezone.now().strftime('%Y%m%d-%H%M%S')
    output_name = f'{args.lote}-{suffix}-{stamp}'
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(root / 'diagnostico' / f'{output_name}.log', encoding='utf-8'),
    ]
    for handler in handlers:
        prefix = '' if args.enviar_a else '[SIMULACIÓN SMTP] '
        handler.setFormatter(logging.Formatter(prefix + '%(asctime)s %(levelname)s %(message)s'))
    for name in ('prueba_recordatorios_ejecucion', 'leadmanager.apps.automations.tasks',
                 'leadmanager.apps.automations.services.imap_service'):
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        for handler in handlers:
            logger.addHandler(handler)
    try:
        result = simular(args.lote, args.todos_en_proceso, args.enviar_a)
        result_name = f'{output_name}-resultado.json'
        (root / 'diagnostico' / result_name).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8',
        )
    finally:
        connection.close()


if __name__ == '__main__':
    main()
