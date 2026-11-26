"""Datos del panel de conversaciones, análisis conjunto y correo de cierre diario."""

from collections import Counter, defaultdict
from datetime import date

from django.conf import settings
from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from django.utils.formats import date_format
from django.utils.translation import override

from leadmanager.apps.prospects.crm_data import (
    es_escalar, fill_empty_crm, merge_crm_payload, parse_json_object,
)
from leadmanager.apps.prospects.models import (
    ChatHistoryModel, ConversationEscalationModel,
)
from leadmanager.apps.prospects.qualification import conversation_semaforos

from ..agents.daily_summary_agent import analyze_daily_report
from . import imap_service
from .report_redaction import redact_contact_data


ESTADOS = {
    'verde': 'Escalado', 'amarillo': 'En proceso',
    'rojo': 'Descartado', 'gris': 'Descartado',
}


def _text(value):
    return str(value).strip() if value is not None else ''


def _prospecting_situation(crm):
    """Misma situación y motivos que getProspectingSummary en el modal web."""
    color = _text(crm.get('semaforo')).lower()
    etapa = _text(crm.get('conversation_state')).lower()
    motivo = _text(crm.get('motivo_semaforo'))
    motivo_escalamiento = _text(crm.get('motivo_escalamiento'))
    if es_escalar(crm.get('escalar_a_humano')) or crm.get('is_qualified'):
        return {
            'estado': 'Escalado',
            'motivo': motivo_escalamiento or motivo or
                'La solicitud requiere atención de un especialista para continuar la negociación.',
        }
    opciones = {
        'amarillo': ('En proceso de prospección',
                     'El prospecto sigue consultando y aún falta que comparta los datos necesarios para calificar su solicitud.'),
        'rojo': ('No escalado',
                 'Falta información para continuar la negociación o hay datos contradictorios que deben aclararse.'),
        'gris': ('No escalado', 'La conversación terminó sin derivación a un especialista.'),
        'verde': ('Pendiente de escalamiento',
                  'La evaluación comercial es favorable, pero aún no se ha confirmado la derivación a un especialista.'),
    }
    estado, explicacion = opciones.get(color, (
        'No escalado' if etapa in ('enrutamiento', 'closed', 'cerrada') else
        'En proceso de prospección' if etapa else 'Evaluación pendiente',
        'El flujo autónomo terminó sin un escalamiento registrado.' if etapa in ('enrutamiento', 'closed', 'cerrada')
        else 'Falta completar la evaluación comercial para determinar el siguiente paso.',
    ))
    return {'estado': estado, 'motivo': motivo or motivo_escalamiento or explicacion}


def _conversation_card(row, crm, color):
    """Tarjeta para el correo con datos guardados, sin generación por el LLM."""
    contact = crm.get('contacto')
    contact = contact if isinstance(contact, dict) else {}
    prospect = row.prospect

    def stored(*values):
        return next((_text(value) for value in values if _text(value)), '')

    text_color, background = {
        'verde': ('#166534', '#f0fdf4'),
        'amarillo': ('#92400e', '#fffbeb'),
        'rojo': ('#991b1b', '#fef2f2'),
    }.get(color, ('#475569', '#f1f5f9'))
    return {
        'id': row.pk,
        'canal': row.get_source_display() or 'Sin canal',
        'estado': ESTADOS.get(color, 'Sin evaluar'),
        'color_estado': text_color, 'fondo_estado': background,
        'contacto': {
            'nombre': stored(contact.get('nombre'), crm.get('contact_name'), crm.get('name'),
                             prospect.name if prospect else None),
            'empresa': stored(contact.get('empresa'), crm.get('partner_name'),
                              prospect.enterprise if prospect else None),
            'correo': stored(contact.get('correo'), crm.get('email_from'), crm.get('correo'),
                             prospect.email if prospect else None),
            'telefono': stored(contact.get('telefono'), crm.get('phone'), crm.get('telefono'),
                               prospect.phone if prospect else None),
        },
        'unidad_negocio': _text(crm.get('unidad_negocio_detectada')) or 'No determinada',
        'resumen_conversacion': _text(crm.get('resumen_conversacion')),
        'resumen_empresa': stored(crm.get('resumen_empresa'),
                                  prospect.enterprise_summary if prospect else None),
        'situacion': _prospecting_situation(crm),
    }


def _conversation_data(conversations, zona):
    """Lee en lote el análisis anonimizado y las tarjetas originales del correo."""
    rows = list(conversations.select_related('prospect'))
    if not rows:
        return [], []
    colors = conversation_semaforos(conversations)
    ids = [row.pk for row in rows]
    escalations = {}
    for cid, content in (
        ConversationEscalationModel.objects.filter(session_id__in=ids)
        .order_by('session_id', '-created_at', '-id')
        .values_list('session_id', 'conversation_content').iterator(chunk_size=500)
    ):
        if cid not in escalations:
            escalations[cid] = merge_crm_payload(content)

    analyses, contacts = {}, defaultdict(list)
    histories = defaultdict(list)
    for cid, message in (
        ChatHistoryModel.objects.filter(conversation_id__in=ids)
        .order_by('conversation_id', '-created_at', '-id')
        .values_list('conversation_id', 'message').iterator(chunk_size=500)
    ):
        if not isinstance(message, dict):
            continue
        role, content = message.get('role'), message.get('content')
        parsed = parse_json_object(content) if role == 'assistant' else {}
        if parsed:
            crm = merge_crm_payload(parsed)
            if isinstance(parsed.get('crm_update'), dict) or 'semaforo' in parsed:
                analyses.setdefault(cid, crm)
            contact = crm.get('contacto')
            if isinstance(contact, dict):
                contacts[cid].append(contact)
            content = next((parsed.get(key) for key in (
                'reply_to_customer_esp', 'response_to_customer_esp', 'reply_to_customer',
            ) if _text(parsed.get(key))), '')
        if role in ('user', 'assistant') and isinstance(content, str) and content.strip():
            histories[cid].append({'rol': role, 'texto': content})

    details, cards = [], []
    for row in rows:
        # Como el modal: el último escalamiento manda y el último análisis rellena vacíos.
        crm = dict(escalations.get(row.pk, {}))
        fill_empty_crm(crm, analyses.get(row.pk, {}))
        crm['is_qualified'] = row.pk in escalations
        prospect = row.prospect
        # Los contactos del correo se mantienen fuera de los datos para el LLM.
        local_contacts = [*contacts[row.pk], crm.get('contacto'), {
            'nombre': crm.get('contact_name') or crm.get('name') or (prospect.name if prospect else ''),
            'correo': crm.get('email_from') or crm.get('correo') or (prospect.email if prospect else ''),
            'telefono': crm.get('phone') or crm.get('telefono') or (prospect.phone if prospect else ''),
            'empresa': crm.get('partner_name') or (prospect.enterprise if prospect else ''),
        }]
        if prospect:
            local_contacts.append({
                'nombre': prospect.name, 'correo': prospect.email,
                'telefono': prospect.phone, 'empresa': prospect.enterprise,
            })
        color = colors.get(row.pk, '')
        cards.append(_conversation_card(row, crm, color))
        summary = _text(crm.get('resumen_conversacion'))
        detail = {
            'id': row.pk,
            'creada': row.created_at.astimezone(zona).isoformat(),
            'canal': row.source or 'sin_canal',
            'nombre_canal': row.get_source_display() or 'Sin canal',
            'estado_tecnico': row.state,
            'estado': ESTADOS.get(color, 'Sin evaluar'), 'semaforo': color,
            'situacion_registrada': _prospecting_situation(crm),
            'unidad_negocio': _text(crm.get('unidad_negocio_detectada')) or 'No determinada',
            'resumen_registrado': summary,
            'motivo_estado': _text(analyses.get(row.pk, {}).get('motivo_semaforo') or crm.get('motivo_semaforo')),
            'motivo_escalamiento': _text(crm.get('motivo_escalamiento')),
            'score_comercial': crm.get('score_comercial'),
            'bant': crm.get('bant') if isinstance(crm.get('bant'), dict) else {},
            'utm_source': row.utm_source, 'utm_medium': row.utm_medium,
            'utm_campaign': row.utm_campaign, 'traffic_origin': row.traffic_origin,
            # Con resumen guardado no hace falta reenviar el historial completo al LLM.
            'historial': [] if summary else list(reversed(histories[row.pk])),
        }
        for key in (
            'situacion_registrada', 'resumen_registrado', 'motivo_estado',
            'motivo_escalamiento', 'bant', 'historial',
        ):
            detail[key] = redact_contact_data(detail[key], local_contacts)
        details.append(detail)
    return details, cards


def build_daily_report(conversations, inicio, fin, zona):
    details, cards = _conversation_data(conversations, zona)
    states = Counter(row['estado'] for row in details)
    stats = {
        'total_conversaciones': len(details),
        'escalados': states['Escalado'], 'descartados': states['Descartado'],
        'en_proceso': states['En proceso'], 'sin_evaluar': states['Sin evaluar'],
        'no_escalados': states['Descartado'] + states['En proceso'],
    }
    for name, key, empty in (
        ('por_canal', 'nombre_canal', 'Sin canal'),
        ('por_fuente', 'utm_source', 'Sin fuente UTM'),
        ('por_origen', 'traffic_origin', 'Sin origen registrado'),
        ('por_campana', 'utm_campaign', 'Sin campaña'),
        ('por_unidad_negocio', 'unidad_negocio', 'No determinada'),
    ):
        stats[name] = dict(sorted(Counter(_text(row[key]) or empty for row in details).items()))
    stats['por_canal_y_estado'] = []
    for canal in stats['por_canal']:
        counts = Counter(row['estado'] for row in details if row['nombre_canal'] == canal)
        stats['por_canal_y_estado'].append({
            'canal': canal, 'total': sum(counts.values()),
            'escalados': counts['Escalado'], 'en_proceso': counts['En proceso'],
            'descartados': counts['Descartado'], 'sin_evaluar': counts['Sin evaluar'],
        })
    return {
        'fecha': inicio.date().isoformat(), 'zona_horaria': zona.key,
        'inicio': inicio.isoformat(), 'fin': fin.isoformat(),
        'estadisticas': stats, 'conversaciones': details,
        'tarjetas_conversaciones': cards,
    }


def build_daily_charts(stats):
    """Barras HTML con cantidades reales y porcentajes sobre el total del día."""
    total = stats['total_conversaciones']

    def bar(label, count, color):
        percentage = count * 100 / total if total else 0
        return {
            'nombre': label, 'cantidad': count, 'color': color,
            'ancho': '{:.1f}'.format(percentage),
            'porcentaje': '{:.1f}'.format(percentage).replace('.', ','),
        }

    charts = [{
        'titulo': 'Por estado comercial',
        'filas': [
            bar('Escalados', stats['escalados'], '#16a34a'),
            bar('En proceso', stats['en_proceso'], '#f59e0b'),
            bar('Descartados', stats['descartados'], '#ef4444'),
        ],
    }]
    palette = ('#2563eb', '#7c3aed', '#0891b2', '#0d9488', '#db2777', '#ea580c', '#64748b')
    for chart_index, (title, key) in enumerate((
        ('Por canal', 'por_canal'),
        ('Por fuente UTM', 'por_fuente'),
        ('Por origen de tráfico', 'por_origen'),
        ('Por campaña', 'por_campana'),
        ('Por unidad de negocio', 'por_unidad_negocio'),
    )):
        charts.append({
            'titulo': title,
            'filas': [
                bar(name, count, palette[(chart_index + row_index) % len(palette)])
                for row_index, (name, count) in enumerate(
                    sorted(stats[key].items(), key=lambda item: (-item[1], item[0]))
                )
            ],
        })
    return charts


def send_daily_report(report):
    logo_path = finders.find('imgs/logo.png')
    if not logo_path:
        raise RuntimeError('No se encontró el logo de Smarketing para el resumen diario.')
    has_conversations = bool(report['conversaciones'])
    # Sin conversaciones se envía un aviso directo, sin pasar por el análisis.
    analysis = analyze_daily_report(report).model_dump() if has_conversations else None
    with override('es'):
        fecha_texto = date_format(date.fromisoformat(report['fecha']), r'j \d\e F \d\e Y')
    context = {
        'fecha': fecha_texto, 'zona_horaria': report['zona_horaria'],
        'logo_src': 'cid:smarketing-logo',
        'destinatario_nombre': settings.DAILY_SUMMARY_RECIPIENT_NAME,
        'tiene_conversaciones': has_conversations,
        'estadisticas': report['estadisticas'], 'analisis': analysis,
        'tarjetas_conversaciones': report['tarjetas_conversaciones'],
        'graficas': build_daily_charts(report['estadisticas']) if has_conversations else [],
    }
    subject = '{} - {}'.format(
        'Resumen diario de conversaciones' if has_conversations else 'Sin nuevas conversaciones',
        fecha_texto,
    )
    sent = imap_service.send_email(
        to=settings.DAILY_SUMMARY_RECIPIENT,
        subject=subject,
        body=render_to_string('automations/emails/daily_summary.txt', context),
        body_html=render_to_string('automations/emails/daily_summary.html', context),
        inline_images={'smarketing-logo': logo_path},
        kind='daily_summary',
    )
    if not sent:
        raise RuntimeError('No se pudo enviar el correo del resumen diario.')
    return settings.DAILY_SUMMARY_RECIPIENT
