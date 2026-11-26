"""Prepara seis conversaciones ficticias y explica su selección sin enviar mensajes."""

import argparse
import json
import logging
import os
import re
import sys
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


CASOS = (
    ('form', 'verde'), ('form', 'rojo'),
    ('web', 'verde'), ('web', ''),
    ('email', 'verde'), ('email', 'amarillo'),
)
ZONA = ZoneInfo('America/Mexico_City')


def configurar(lote, ahora):
    """Crea solo registros nuevos; repetir un lote completo no altera sus datos."""
    from django.db import transaction
    from leadmanager.apps.prospects.models import (
        ChatHistoryModel, ConversationEscalationModel, ConversationModel, ProspectModel,
    )

    with transaction.atomic():
        existentes = ConversationModel.objects.filter(utm_campaign=lote)
        if existentes.exists():
            esperados = {f'{canal}-{color or "sin-semaforo"}' for canal, color in CASOS}
            if existentes.count() != 6 or set(existentes.values_list('utm_content', flat=True)) != esperados:
                raise RuntimeError('El lote existente no coincide con los seis casos; no se modificó.')
            return False

        inicio = ahora - timedelta(hours=49)
        for indice, (canal, color) in enumerate(CASOS):
            caso = f'{canal}-{color or "sin-semaforo"}'
            escalada = color == 'verde'
            prospecto = ProspectModel.objects.create(
                name=f'[PRUEBA RECORDATORIOS] {caso}',
                email=None,
                enterprise='Empresa ficticia de pruebas',
                enterprise_summary='Registro sintético para probar los filtros de recordatorios.',
            )
            conversacion = ConversationModel.objects.create(
                # El correo del prospecto queda sin habilitar. session_id es
                # un identificador, no un destinatario para los workers.
                session_id=f'{lote}:{caso}',
                source=canal, prospect=prospecto,
                state='closed' if escalada else 'open',
                email_reminder_phase=None,
                web_message_count=1 if canal == 'web' else 0,
                utm_source='prueba_recordatorios', utm_campaign=lote,
                utm_content=caso, traffic_origin='prueba',
            )
            creada = inicio - timedelta(minutes=indice)
            ConversationModel.objects.filter(pk=conversacion.pk).update(created_at=creada)
            crm = {
                'escalar_a_humano': escalada,
                'contacto': {'nombre': prospecto.name},
                'unidad_negocio_detectada': 'NO_DETERMINADO',
                'resumen_conversacion': f'Conversación ficticia {caso}. No corresponde a un cliente real.',
            }
            if color:
                crm['semaforo'] = color
            entrada = ChatHistoryModel.objects.create(conversation=conversacion, message={
                'role': 'user', 'content': f'[PRUEBA] Solicito información. Caso {caso}.',
            })
            respuesta = ChatHistoryModel.objects.create(conversation=conversacion, message={
                'role': 'assistant', 'content': {
                    'reply_to_customer': '[PRUEBA] Solicitud registrada para la prueba de recordatorios.',
                    'crm_update': crm,
                },
            })
            for mensaje, segundos in ((entrada, 1), (respuesta, 2)):
                ChatHistoryModel.objects.filter(pk=mensaje.pk).update(
                    created_at=creada + timedelta(seconds=segundos),
                )
            if escalada:
                derivacion = ConversationEscalationModel.objects.create(
                    session=conversacion, conversation_content={'crm_update': crm},
                )
                ConversationEscalationModel.objects.filter(pk=derivacion.pk).update(
                    created_at=creada + timedelta(seconds=3),
                )
        return True


def proteger_destinatarios(lote):
    """Deshabilita únicamente correos ficticios del lote creado por este script."""
    from django.db import transaction
    from leadmanager.apps.prospects.models import ConversationModel, ProspectModel

    with transaction.atomic():
        conversaciones = list(ConversationModel.objects.filter(
            utm_campaign=lote, utm_source='prueba_recordatorios',
        ).select_related('prospect'))
        if len(conversaciones) != 6:
            raise RuntimeError('Se esperaban seis conversaciones de prueba; no se modificó nada.')
        for conversacion in conversaciones:
            prospecto = conversacion.prospect
            esperado = f'{lote}-{conversacion.utm_content}@example.test'
            if not prospecto or prospecto.email not in (None, esperado):
                raise RuntimeError('El prospecto tiene datos distintos a los ficticios; no se modifica.')
            if prospecto.conversations.exclude(utm_campaign=lote).exists():
                raise RuntimeError('El prospecto se usa fuera de este lote; no se modifica.')
            ProspectModel.objects.filter(pk=prospecto.pk, email=esperado).update(email=None)


def inspeccionar(lote, ahora):
    """Lee el semáforo compartido con Conversations y reproduce los filtros de la tarea."""
    from leadmanager.apps.prospects.models import ConversationModel
    from leadmanager.apps.prospects.qualification import conversation_semaforos, filter_by_semaforo
    from leadmanager.apps.automations.services.conversation_reminders import (
        latest_schema_emails, reminder_recipient,
    )

    conversaciones = ConversationModel.objects.filter(utm_campaign=lote).select_related('prospect').order_by('id')
    colores = conversation_semaforos(conversaciones)
    schema_emails = latest_schema_emails(conversaciones)
    limite = ahora - timedelta(hours=48)
    candidatos = set(filter_by_semaforo(conversaciones.filter(
        created_at__lte=limite, email_reminder_phase__isnull=True,
    ), 'amarillo', colores).values_list('id', flat=True))
    filas = []
    for conversacion in conversaciones:
        color = colores.get(conversacion.pk, '')
        motivos = []
        if color != 'amarillo':
            motivos.append(f'Semáforo {color or "ausente"}; requiere amarillo')
        if conversacion.created_at > limite:
            motivos.append('No cumple las 48 horas')
        if conversacion.email_reminder_phase is not None:
            motivos.append('Ya tiene fase de recordatorio')
        correo, origen = reminder_recipient(conversacion, schema_emails.get(conversacion.pk))
        destinatario = bool(correo)
        if not destinatario:
            motivos.append('Destinatario de envío sin habilitar para esta prueba')
        filas.append({
            'id': conversacion.pk, 'prospect_id': conversacion.prospect_id,
            'canal': conversacion.source, 'semaforo': color or None,
            'escalada': color == 'verde', 'estado_tecnico': conversacion.state,
            'creada': conversacion.created_at.astimezone(ZONA).strftime('%d/%m/%Y %H:%M:%S'),
            'antiguedad_horas': round((ahora - conversacion.created_at).total_seconds() / 3600, 2),
            'fase_recordatorio': conversacion.email_reminder_phase,
            'cumple_filtros': conversacion.pk in candidatos,
            'destinatario_habilitado': destinatario,
            'origen_destinatario': origen,
            'podria_intentar_envio': conversacion.pk in candidatos and destinatario,
            'motivos': motivos,
        })
    return filas


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--crear', action='store_true', help='Crea el lote si todavía no existe.')
    parser.add_argument('--proteger', action='store_true', help='Deshabilita los contactos ficticios del lote.')
    parser.add_argument('--lote', default='prueba-recordatorios-20261022')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z0-9-]{1,60}', args.lote):
        parser.error('--lote solo admite letras minúsculas, números y guiones, hasta 60 caracteres.')
    raiz = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(raiz))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.base')
    import django
    django.setup()
    from django.db import connection, transaction
    from django.utils import timezone

    if connection.vendor == 'postgresql':
        connection.settings_dict.setdefault('OPTIONS', {})['connect_timeout'] = 10
    ruta_log = raiz / 'diagnostico' / f'{args.lote}.log'
    logger = logging.getLogger('prueba_recordatorios')
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in (logging.StreamHandler(sys.stdout), logging.FileHandler(ruta_log, encoding='utf-8')):
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        logger.addHandler(handler)
    try:
        ahora = timezone.now()
        if args.crear:
            creado = configurar(args.lote, ahora)
            logger.info('Lote %s: %s', args.lote, 'creado' if creado else 'ya existía; se conserva')
        if args.proteger:
            proteger_destinatarios(args.lote)
            logger.info('Contactos ficticios del lote sin envío habilitado.')
        with transaction.atomic():
            if connection.vendor == 'postgresql':
                with connection.cursor() as cursor:
                    cursor.execute('SET TRANSACTION READ ONLY')
            filas = inspeccionar(args.lote, ahora)
        for fila in filas:
            logger.info(
                'id=%s | canal=%s | semaforo=%s | escalada=%s | antiguedad=%sh | '
                'fase=%s | cumple_filtros=%s | envio_habilitado=%s | %s',
                fila['id'], fila['canal'], fila['semaforo'] or 'sin semáforo', fila['escalada'],
                fila['antiguedad_horas'], fila['fase_recordatorio'], fila['cumple_filtros'],
                fila['podria_intentar_envio'], '; '.join(fila['motivos']),
            )
        informe = {'lote': args.lote, 'total': len(filas), 'conversaciones': filas}
        ruta_json = raiz / 'diagnostico' / f'{args.lote}.json'
        ruta_json.write_text(json.dumps(informe, ensure_ascii=False, indent=2), encoding='utf-8')
        logger.info('Total=%s | candidatos por filtros=%s | con envío habilitado=%s. SMTP no invocado.',
                    len(filas), sum(f['cumple_filtros'] for f in filas),
                    sum(f['podria_intentar_envio'] for f in filas))
    finally:
        connection.close()


if __name__ == '__main__':
    main()
