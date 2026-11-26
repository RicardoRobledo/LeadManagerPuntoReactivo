"""
Exportación a Excel del panel de Sofía.

Construye el libro .xlsx (hoja Resumen + hoja Prospectos) a partir de
las conversaciones reales que devuelve la base, no de la página visible
en la tabla. Los escalados se identifican por el último semáforo verde,
con el mismo criterio del dashboard, Estadísticas y Conversaciones.

El parseo del JSON de la IA vive en crm_data.py, compartido con
ConversationView, para que el panel y el archivo lean igual.
"""

from datetime import datetime
from tempfile import SpooledTemporaryFile

from django.db.models import Count
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font, PatternFill

from .crm_data import extract_chat_analysis, merge_crm_payload
from .date_ranges import mx_zi, resolve_range
from .qualification import (
    NO_ESCALADOS, conversation_semaforos, filter_by_semaforo,
    normalize_semaforo_filter, qualified_conversation_ids,
)
from .models import (
    ChatHistoryModel,
    ConversationEscalationModel,
    ConversationModel,
    ConversationSourceEnum,
    ConversationStateEnum,
    ProspectModel,
)


__author__ = 'Ricardo'
__version__ = '0.1'


ALL = 'all'

KINDS = ('qualified', 'all', 'full')

KIND_LABELS = {
    'qualified': 'Prospectos calificados',
    'all': 'Todos los prospectos',
    'full': 'Reporte completo',
}

SIN_CANAL = 'Sin canal'
TODOS_LOS_CANALES = 'Todos'

ESTADO_CALIFICADO = 'Calificado'
ESTADO_EN_PROCESO = 'En proceso'

PREVIEW_ROWS = 5

MAX_CELL_LENGTH = 32767

HEADER_FILL = PatternFill(patternType='solid', fgColor='1F2937')
HEADER_FONT = Font(bold=True, color='FFFFFF')
TITLE_FONT = Font(bold=True, size=16, color='1F2937')
SECTION_FONT = Font(bold=True, size=12, color='365314')
LABEL_FONT = Font(bold=True)


class ExportFilters:
    """Los filtros ya resueltos de una exportación."""

    def __init__(self, kind, source, interval, date_from, date_to,
                 range_label, utm_source='', utm_campaign='', semaforo='all'):
        self.kind = kind
        self.source = source
        self.interval = interval
        self.date_from = date_from
        self.date_to = date_to
        self.range_label = range_label
        self.utm_source = utm_source
        self.utm_campaign = utm_campaign
        self.semaforo = semaforo
        self._colors = None

    def conversations_base(self):
        """Conversaciones del periodo/canal, sin filtrar por estado."""
        qs = ConversationModel.objects.all()

        if self.date_from is not None:
            qs = qs.filter(
                created_at__gte=self.date_from,
                created_at__lt=self.date_to,
            )

        if self.source != 'all':
            qs = qs.filter(source=self.source)

        if self.utm_source:
            qs = qs.filter(utm_source=self.utm_source)

        if self.utm_campaign:
            qs = qs.filter(utm_campaign=self.utm_campaign)

        if self.semaforo != 'all' and self._colors is None:
            self._colors = conversation_semaforos(qs)
        return filter_by_semaforo(qs, self.semaforo, self._colors)

    def has_any_filter(self):
        return (
            self.date_from is not None
            or self.source != 'all'
            or bool(self.utm_source)
            or bool(self.utm_campaign)
            or self.semaforo != 'all'
        )


def build_export_filters(request):
    """
    Lee los parámetros del request y los normaliza. 'all' no existe en
    date_ranges (el dashboard no lo usa), así que se maneja aquí para no
    tocar el comportamiento compartido del panel.
    """
    params = request.GET

    kind = (params.get('kind') or 'all').strip()
    if kind not in KINDS:
        kind = 'all'

    source = (params.get('source') or 'all').strip()
    valid_sources = {'all'} | {s.value for s in ConversationSourceEnum}
    if source not in valid_sources:
        source = 'all'

    utm_source = (params.get('utm_source') or '').strip()
    utm_campaign = (params.get('utm_campaign') or '').strip()

    interval = (params.get('interval') or ALL).strip()

    if interval == ALL:
        date_from = None
        date_to = None
        range_label = 'Todo el historial'
    else:
        rango = resolve_range(request, default='today')
        interval = rango.interval
        date_from = rango.date_from
        date_to = rango.date_to
        range_label = rango.label

    return ExportFilters(
        kind=kind,
        source=source,
        interval=interval,
        date_from=date_from,
        date_to=date_to,
        range_label=range_label,
        utm_source=utm_source,
        utm_campaign=utm_campaign,
        semaforo=normalize_semaforo_filter(params.get('semaforo')) or 'all',
    )


def export_filename(filters):
    """Nombre ASCII y seguro para el archivo descargado."""
    nombres = {
        'qualified': 'sofia_calificados',
        'all': 'sofia_prospectos',
        'full': 'sofia_reporte_completo',
    }

    marca = timezone.localtime(timezone.now(), mx_zi).strftime('%Y%m%d_%H%M')

    return f'{nombres[filters.kind]}_{marca}.xlsx'


def _canal_label(value):
    if not value:
        return SIN_CANAL

    return dict(ConversationSourceEnum.choices).get(value, value)


def _fecha(value):
    if not value:
        return ''

    return timezone.localtime(value, mx_zi).strftime('%d/%m/%Y %H:%M')


def _cell_value(value):
    """Deja números y fechas tal cual; limpia texto para Excel."""
    if value is None or isinstance(value, (int, float, datetime)):
        return value

    text = ILLEGAL_CHARACTERS_RE.sub('', str(value))

    if len(text) > MAX_CELL_LENGTH:
        text = text[:MAX_CELL_LENGTH - 3] + '...'

    return text


def _append(hoja, values, font=None, fill=None):
    cells = []

    for value in values:
        cell = WriteOnlyCell(hoja, value=_cell_value(value))

        if font:
            cell.font = font

        if fill:
            cell.fill = fill

        cells.append(cell)

    hoja.append(cells)


def _crm_small(crm):
    """
    Solo lo que ocupa el archivo (resumen y contacto). El JSON completo
    puede ser grande; guardarlo entero multiplicaría la memoria.
    """
    if not isinstance(crm, dict):
        return {}

    contacto = crm.get('contacto')

    if not isinstance(contacto, dict):
        contacto = {}

    return {
        'resumen': (
            crm.get('resumen_conversacion')
            or crm.get('resumen_empresa')
            or ''
        ),
        'nombre': contacto.get('nombre'),
        'telefono': contacto.get('telefono'),
        'correo': contacto.get('correo'),
        'empresa': contacto.get('empresa'),
    }


def _crm_summaries(conversations):
    """
    Resumen y contacto por conversación. El resumen sale del escalamiento
    más reciente (flujo viejo) o, si no hay, del último mensaje del
    asistente que traiga crm_update.

    Se lee por lotes y se guardan solo cadenas cortas, nunca el JSON
    completo, para que un histórico grande no llene la memoria.
    """
    ids = conversations.values('id')

    summaries = {}

    escalaciones = (
        ConversationEscalationModel.objects
        .filter(session__in=ids)
        .order_by('session_id', '-created_at', '-id')
        .values_list('session_id', 'conversation_content')
        .iterator(chunk_size=200)
    )

    for session_id, content in escalaciones:
        if session_id in summaries:
            # Ya se guardó la más reciente: el orden lo garantiza.
            continue

        summaries[session_id] = _crm_small(merge_crm_payload(content))

    chats = (
        ChatHistoryModel.objects
        .filter(
            conversation__in=ids,
            message__role='assistant',
            message__content__icontains='crm_update',
        )
        .order_by('conversation_id', '-created_at', '-id')
        .values_list('conversation_id', 'message')
        .iterator(chunk_size=200)
    )

    for conversation_id, message in chats:
        if conversation_id in summaries:
            # El escalamiento manda para el resumen, igual que en el
            # modal.
            continue

        crm_update, _ = extract_chat_analysis(message)

        if not crm_update:
            continue

        summaries[conversation_id] = _crm_small(crm_update)

    return summaries


def _fila_vacia(prospecto):
    return {
        'prospecto': prospecto,
        'conversaciones': 0,
        'primera': None,
        'ultima': None,
        'canales': [],
        'canal_principal': None,
        'calificado': False,
        'escalado': False,
        'resumen': '',
        'contacto_nombre': None,
        'contacto_telefono': None,
        'contacto_correo': None,
        'empresa': None,
        'utm_source': '',
        'utm_campaign': '',
    }


def _aggregate_prospects(filters, qualified_ids, summaries):
    """
    Una fila por prospecto, sin duplicados aunque tenga varias
    conversaciones. El orden de lectura es del más reciente al más
    antiguo, así que lo primero que se encuentra de cada prospecto es su
    conversación más nueva.

    En modo calificados solo cuentan las conversaciones con semáforo verde.
    """
    conversaciones = (
        filters.conversations_base()
        .select_related('prospect')
        .order_by('-created_at', '-id')
    )

    filas = {}

    for conversacion in conversaciones.iterator(chunk_size=500):
        calificada = conversacion.id in qualified_ids

        if filters.kind == 'qualified' and not calificada:
            continue

        prospecto = conversacion.prospect

        if prospecto is None:
            continue

        fila = filas.get(prospecto.id)

        if fila is None:
            fila = _fila_vacia(prospecto)
            fila['canal_principal'] = conversacion.source
            fila['utm_source'] = conversacion.utm_source
            fila['utm_campaign'] = conversacion.utm_campaign
            filas[prospecto.id] = fila

        fila['conversaciones'] += 1

        if fila['primera'] is None or conversacion.created_at < fila['primera']:
            fila['primera'] = conversacion.created_at

        if fila['ultima'] is None or conversacion.created_at > fila['ultima']:
            fila['ultima'] = conversacion.created_at

        if conversacion.source not in fila['canales']:
            fila['canales'].append(conversacion.source)

        if calificada:
            fila['calificado'] = True
            fila['escalado'] = True

        resumen = summaries.get(conversacion.id)

        if resumen:
            if not fila['resumen'] and resumen.get('resumen'):
                fila['resumen'] = resumen['resumen']

            if not fila['contacto_nombre'] and resumen.get('nombre'):
                fila['contacto_nombre'] = resumen['nombre']

            if not fila['contacto_telefono'] and resumen.get('telefono'):
                fila['contacto_telefono'] = resumen['telefono']

            if not fila['contacto_correo'] and resumen.get('correo'):
                fila['contacto_correo'] = resumen['correo']

            if not fila['empresa'] and resumen.get('empresa'):
                fila['empresa'] = resumen['empresa']

    # "Todos los prospectos" sin ningún filtro es literal: también los
    # que todavía no tienen conversaciones (por ejemplo, un visitante web
    # que dejó solo el correo y nunca escaló).
    if filters.kind in ('all', 'full') and not filters.has_any_filter():
        for prospecto in ProspectModel.objects.all().iterator(chunk_size=500):
            if prospecto.id not in filas:
                filas[prospecto.id] = _fila_vacia(prospecto)

    return sorted(
        filas.values(),
        key=lambda fila: (
            fila['ultima'] or fila['prospecto'].created_at,
            fila['prospecto'].id,
        ),
        reverse=True,
    )


def _totals(filters, qualified_ids):
    """
    Totales del rango por semáforo. El cierre operativo se conserva como
    dato informativo y no interviene en la clasificación comercial.
    """
    base = filters.conversations_base()

    total = base.count()
    cerradas = base.filter(state=ConversationStateEnum.CLOSED).count()
    colors = conversation_semaforos(base)
    not_escalated = sum(color in NO_ESCALADOS for color in colors.values())

    if qualified_ids:
        cerradas_sin_escalar = (
            base.filter(state=ConversationStateEnum.CLOSED)
            .exclude(id__in=list(qualified_ids))
            .count()
        )
    else:
        cerradas_sin_escalar = cerradas

    return {
        'total': total,
        'calificadas': len(qualified_ids),
        'no_escaladas': not_escalated,
        'sin_evaluar': total - len(qualified_ids) - not_escalated,
        'cerradas': cerradas,
        'cerradas_sin_escalar': cerradas_sin_escalar,
    }


def _prospect_counts(filters, qualified_ids):
    """
    Contadores con consultas de base, no recorriendo el queryset
    completo: la vista previa se refresca cada vez que cambia un filtro.
    """
    base = filters.conversations_base().filter(prospect__isnull=False)

    calificados = 0

    if qualified_ids:
        calificados = (
            base.filter(id__in=list(qualified_ids))
            .values('prospect_id')
            .distinct()
            .count()
        )

    if filters.kind == 'qualified':
        total = calificados
    elif filters.kind in ('all', 'full') and not filters.has_any_filter():
        total = ProspectModel.objects.count()
    else:
        total = base.values('prospect_id').distinct().count()

    return {
        'total': total,
        'calificados': calificados,
    }


def _distribucion(filters, qualified_ids):
    filas = (
        filters.conversations_base()
        .values('source')
        .annotate(total=Count('id'))
        .order_by('source')
    )

    por_canal = {
        fila['source']: {
            'canal': _canal_label(fila['source']),
            'total': fila['total'],
            'calificadas': 0,
        }
        for fila in filas
    }

    if qualified_ids:
        calificadas_por_canal = (
            filters.conversations_base()
            .filter(id__in=list(qualified_ids))
            .values('source')
            .annotate(total=Count('id'))
        )

        for fila in calificadas_por_canal:
            if fila['source'] in por_canal:
                por_canal[fila['source']]['calificadas'] = fila['total']
            else:
                por_canal[fila['source']] = {
                    'canal': _canal_label(fila['source']),
                    'total': 0,
                    'calificadas': fila['total'],
                }

    ordenados = sorted(
        por_canal,
        key=lambda source: (source is None, source or ''),
    )

    return [por_canal[source] for source in ordenados]


def _cantidad_prospectos_por_canal(filas):
    cantidades = {}

    for fila in filas:
        canal = _canal_label(fila['canal_principal'])
        cantidades[canal] = cantidades.get(canal, 0) + 1

    return cantidades


def _escribir_resumen(hoja, filters, totales, filas, user, qualified_ids):
    titulo = getattr(user, 'get_username', lambda: '')() if user else ''

    _append(hoja, ['Sofía — Lead Manager'], font=TITLE_FONT)
    _append(hoja, ['Exportación de datos'])
    _append(hoja, [])
    _append(hoja, ['Fecha de generación',
                   _fecha(timezone.now())])
    _append(hoja, ['Generado por', titulo])
    _append(hoja, ['Tipo de exportación', KIND_LABELS[filters.kind]])
    _append(hoja, ['Rango de fechas', filters.range_label])
    _append(hoja, ['Canal de origen', (
        TODOS_LOS_CANALES if filters.source == 'all'
        else _canal_label(filters.source)
    )])

    if filters.utm_source:
        _append(hoja, ['UTM source', filters.utm_source])

    if filters.utm_campaign:
        _append(hoja, ['UTM campaign', filters.utm_campaign])

    if filters.semaforo != 'all':
        _append(hoja, ['Estado', filters.semaforo])

    prospectos_calificados = sum(
        1 for fila in filas if fila['calificado'])

    _append(hoja, [])
    _append(hoja, ['Totales'], font=SECTION_FONT)
    _append(hoja, ['Total de conversaciones', totales['total']])
    _append(hoja, ['Conversaciones escaladas (verde)',
                   totales['calificadas']])
    _append(hoja, ['Conversaciones no escaladas (amarillo, gris y rojo)', totales['no_escaladas']])
    _append(hoja, ['Conversaciones cerradas (state=closed)',
                   totales['cerradas']])
    _append(hoja, ['Conversaciones cerradas sin semáforo verde',
                   totales['cerradas_sin_escalar']])
    _append(hoja, ['Total de prospectos', len(filas)])
    _append(hoja, ['Prospectos calificados', prospectos_calificados])

    _append(hoja, [])
    _append(hoja, ['Distribución por canal de origen'], font=SECTION_FONT)
    _append(
        hoja,
        ['Canal', 'Conversaciones', 'Calificadas', 'Prospectos'],
        font=HEADER_FONT,
        fill=HEADER_FILL,
    )

    cantidades = _cantidad_prospectos_por_canal(filas)

    for fila in _distribucion(filters, qualified_ids):
        _append(hoja, [
            fila['canal'],
            fila['total'],
            fila['calificadas'],
            cantidades.get(fila['canal'], 0),
        ])

    _append(hoja, [])
    _append(hoja, ['Nota'], font=LABEL_FONT)
    _append(hoja, [
        'Escalado = último semáforo verde. No escalado = amarillo, gris o rojo. '
        'Solo se cuentan conversaciones con semáforo válido. La clasificación no depende de '
        'abierto/cerrado ni del campo escalar_a_humano.'
    ])

    if hasattr(hoja, 'column_dimensions'):
        hoja.column_dimensions['A'].width = 48
        hoja.column_dimensions['B'].width = 32


def _escribir_prospectos(hoja, filters, filas):
    columnas = [
        'ID del prospecto',
        'Nombre',
        'Teléfono',
        'Correo electrónico',
        'Canal de captación',
        'Canales',
        'Estado de calificación',
        'Resumen de la conversación',
    ]

    if filters.kind == 'full':
        columnas += [
            'Empresa',
            'Puesto',
            'Nº de conversaciones',
            'Primera conversación',
            'Última conversación',
            'Escalado (verde)',
            'ID de contacto en Odoo (partner)',
            'utm_source',
            'utm_campaign',
        ]

    _append(hoja, columnas, font=HEADER_FONT, fill=HEADER_FILL)

    for fila in filas:
        prospecto = fila['prospecto']

        values = [
            prospecto.id,
            prospecto.name or fila['contacto_nombre'] or '',
            prospecto.phone or fila['contacto_telefono'] or '',
            prospecto.email or fila['contacto_correo'] or '',
            _canal_label(fila['canal_principal']),
            ', '.join(
                _canal_label(canal) for canal in fila['canales']
            ) or SIN_CANAL,
            (
                ESTADO_CALIFICADO
                if fila['calificado']
                else ESTADO_EN_PROCESO
            ),
            fila['resumen'],
        ]

        if filters.kind == 'full':
            values += [
                prospecto.enterprise or fila['empresa'] or '',
                prospecto.function or '',
                fila['conversaciones'],
                _fecha(fila['primera']),
                _fecha(fila['ultima']),
                'Sí' if fila['escalado'] else 'No',
                prospecto.person_id or '',
                fila['utm_source'],
                fila['utm_campaign'],
            ]

        _append(hoja, values)

    if hasattr(hoja, 'column_dimensions'):
        hoja.column_dimensions['A'].width = 16
        hoja.column_dimensions['B'].width = 28
        hoja.column_dimensions['H'].width = 60


def build_export_workbook(filters, user=None):
    """
    Construye el libro completo y lo devuelve en un archivo temporal.
    ``SpooledTemporaryFile`` mantiene en memoria solo el encabezado y
    rueda a disco si el libro crece; openpyxl va en modo write_only,
    que no guarda las filas ya escritas.
    """
    conversaciones = filters.conversations_base()
    summaries = _crm_summaries(conversaciones)
    qualified_ids = qualified_conversation_ids(conversaciones)
    filas = _aggregate_prospects(filters, qualified_ids, summaries)
    totales = _totals(filters, qualified_ids)

    libro = Workbook(write_only=True)
    hoja_resumen = libro.create_sheet('Resumen')
    hoja_prospectos = libro.create_sheet('Prospectos')

    _escribir_resumen(
        hoja_resumen, filters, totales, filas, user, qualified_ids)
    _escribir_prospectos(hoja_prospectos, filters, filas)

    salida = SpooledTemporaryFile(max_size=10 * 1024 * 1024, mode='w+b')
    libro.save(salida)
    salida.seek(0)

    return salida


def build_export_preview(filters):
    """
    Contadores y muestra que alimentan el modal antes de descargar. Los
    conteos salen de la base; la muestra son los primeros prospectos de
    la misma consulta que exporta la descarga.
    """
    conversaciones = filters.conversations_base()
    summaries = _crm_summaries(conversaciones)
    qualified_ids = qualified_conversation_ids(conversaciones)

    conteos = _prospect_counts(filters, qualified_ids)
    totales = _totals(filters, qualified_ids)

    muestra = []
    vistos = set()

    for conversacion in (
        conversaciones
        .select_related('prospect')
        .order_by('-created_at', '-id')
        .iterator(chunk_size=200)
    ):
        if (
            filters.kind == 'qualified'
            and conversacion.id not in qualified_ids
        ):
            continue

        if (
            conversacion.prospect_id is None
            or conversacion.prospect_id in vistos
        ):
            continue

        vistos.add(conversacion.prospect_id)

        resumen = summaries.get(conversacion.id) or {}
        prospecto = conversacion.prospect

        muestra.append({
            'id': prospecto.id,
            'nombre': (
                prospecto.name or resumen.get('nombre') or ''
            ),
            'correo': (
                prospecto.email or resumen.get('correo') or ''
            ),
            'telefono': (
                prospecto.phone or resumen.get('telefono') or ''
            ),
            'canal': _canal_label(conversacion.source),
            'estado': (
                ESTADO_CALIFICADO
                if conversacion.id in qualified_ids
                else ESTADO_EN_PROCESO
            ),
            'fecha': _fecha(conversacion.created_at),
        })

        if len(muestra) >= PREVIEW_ROWS:
            break

    return {
        'kind': filters.kind,
        'kind_label': KIND_LABELS[filters.kind],
        'range_label': filters.range_label,
        'source': filters.source,
        'source_label': (
            TODOS_LOS_CANALES
            if filters.source == 'all'
            else _canal_label(filters.source)
        ),
        'totales': {
            'conversaciones': totales['total'],
            'calificadas': totales['calificadas'],
            'cerradas': totales['cerradas'],
            'cerradas_sin_escalar': totales['cerradas_sin_escalar'],
            'no_escaladas': totales['no_escaladas'],
            'sin_evaluar': totales['sin_evaluar'],
            'prospectos': conteos['total'],
            'prospectos_calificados': conteos['calificados'],
        },
        'por_canal': _distribucion(filters, qualified_ids),
        'muestra': muestra,
        'filename': export_filename(filters),
    }
