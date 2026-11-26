"""Audita metadatos de correos y pasos fallidos sin ejecutar agentes ni enviar mensajes."""

import argparse
import json
import os
import re
import sys
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--days', type=int, default=14)
    parser.add_argument('--email', help='Filtra por destinatario o remitente del caso.')
    parser.add_argument('--limit', type=int, default=20)
    args = parser.parse_args()
    if args.days < 1 or args.limit < 1:
        parser.error('--days y --limit deben ser positivos.')

    raiz = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(raiz))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.base')
    import django
    django.setup()

    from django.conf import settings
    from django.db import connection, transaction
    from django.db.models import Count, Q
    from django.utils import timezone
    from leadmanager.apps.automations.models import AgentRunModel, AgentStepModel, PendingRunModel
    from leadmanager.apps.automations.services.report_redaction import redact_contact_data
    from leadmanager.apps.prospects.models import ConversationModel, EmailMessageModel

    if connection.vendor == 'postgresql':
        connection.settings_dict.setdefault('OPTIONS', {})['connect_timeout'] = 10
    zona = ZoneInfo('America/Mexico_City')
    ahora = timezone.now()
    desde = ahora - timedelta(days=args.days)

    def grouped(qs, *fields):
        return list(qs.values(*fields).annotate(total=Count('pk')).order_by(*fields))

    def clean(value):
        text = redact_contact_data(str(value or ''))
        return re.sub(r'\bsk-[A-Za-z0-9_-]+', '[clave omitida]', text)[:400]

    scheduled = {
        entry.get('task') for entry in settings.CELERY_BEAT_SCHEDULE.values()
    }
    with transaction.atomic():
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION READ ONLY')

        conversations = ConversationModel.objects.all()
        runs = AgentRunModel.objects.filter(
            agent_name='prospector', source__in=('email', 'form'), started_at__gte=desde,
        )
        emails = EmailMessageModel.objects.filter(created_at__gte=desde)
        pending = PendingRunModel.objects.filter(source='form', created_at__gte=desde)
        if args.email:
            conversations = conversations.filter(
                Q(session_id__iexact=args.email) | Q(prospect__email__iexact=args.email)
            ).distinct()
            runs = runs.filter(conversation_id__in=conversations.values('pk'))
            emails = emails.filter(email__iexact=args.email)
            pending = pending.filter(identifier__iexact=args.email)

        steps = AgentStepModel.objects.filter(run__in=runs)
        failed = steps.filter(status='failed')
        failed_summary_ids = failed.filter(
            step_name='generate_history_summary_step',
        ).values('run_id')

        failures = []
        for step in failed.order_by('-started_at').values(
            'run_id', 'run__conversation_id', 'run__source', 'step_name',
            'started_at', 'exception_type', 'error_message',
        )[:args.limit]:
            step['started_at'] = step['started_at'].astimezone(zona).isoformat()
            step['error_message'] = clean(step['error_message'])
            failures.append(step)

        queues = {}
        for name, qs in (('correo', emails), ('formulario', pending)):
            errors = []
            for row in qs.exclude(last_error='').order_by('-updated_at').values(
                'id', 'status', 'attempts', 'next_attempt_at', 'last_error',
            )[:args.limit]:
                row['last_error'] = clean(row['last_error'])
                errors.append(row)
            queues[name] = {'por_estado': grouped(qs, 'status'), 'errores_recientes': errors}

        report = {
            'consulta_solo_lectura': True,
            'desde': desde.astimezone(zona).isoformat(),
            'hasta': ahora.astimezone(zona).isoformat(),
            'filtro_por_correo': bool(args.email),
            'limite_historial_efectivo': settings.CHAT_HISTORY_LIMIT,
            'reintento_correo_programado': any(
                task and task.endswith('.reprocess_failed_emails') for task in scheduled
            ),
            'reintento_formulario_web_programado': any(
                task and task.endswith('.reprocess_pending_runs') for task in scheduled
            ),
            'conversaciones_por_canal': grouped(conversations, 'source'),
            'ejecuciones': grouped(runs, 'source', 'status'),
            'pasos_fallidos': grouped(failed, 'step_name', 'exception_type'),
            'resumen_historial': grouped(
                steps.filter(step_name='generate_history_summary_step'), 'status',
            ),
            'envio_posterior_a_resumen_fallido': steps.filter(
                run_id__in=failed_summary_ids, step_name='send_response_to_prospect_step',
            ).count(),
            'fallos_recientes': failures,
            'colas': queues,
        }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    main()
