"""
Lecturas de calificación y semáforo del historial comercial.

Conversaciones, dashboard, estadísticas y exportaciones usan el último
semáforo: verde es escalado; amarillo, gris y rojo son no escalados.
Amarillo está en proceso; rojo y gris se agrupan como descartados.
Las conversaciones sin semáforo válido se excluyen de los reportes.
La clasificación es independiente de open/closed y de escalar_a_humano.
"""

from .crm_data import merge_crm_payload, parse_json_object
from .models import ChatHistoryModel, ConversationEscalationModel


__author__ = 'Ricardo'
__version__ = '0.1'


SEMAFOROS = ('verde', 'amarillo', 'rojo', 'gris')
NO_ESCALADOS = ('amarillo', 'gris', 'rojo')
DESCARTADOS = ('rojo', 'gris')


def normalize_semaforo_filter(value):
    value = str(value or 'all').strip().lower()
    value = {'closed': 'verde', 'open': 'no_escalados', 'escalados': 'verde'}.get(value, value)
    return value if value in ('all', 'no_escalados', 'descartados', *SEMAFOROS) else None


def filter_by_semaforo(conversations, selection, colors=None):
    if colors is None:
        colors = conversation_semaforos(conversations)
    selected = {
        'all': SEMAFOROS,
        'no_escalados': NO_ESCALADOS,
        'descartados': DESCARTADOS,
    }.get(selection, (selection,))
    return conversations.filter(pk__in=[pk for pk, color in colors.items() if color in selected])


def conversation_semaforos(conversations):
    """Último semáforo de cada conversación, independiente de open/closed.

    El análisis puede estar serializado como texto o como objeto JSON.
    Para registros antiguos sin análisis en el historial se consulta la
    última derivación guardada. Un análisis nuevo sin color válido queda
    sin evaluar: no se recupera un color anterior que podría estar obsoleto.
    """
    ids = conversations.values('id')
    colors = {}

    def color_from(payload):
        value = merge_crm_payload(payload).get('semaforo')
        value = value.strip().lower() if isinstance(value, str) else ''
        return value if value in SEMAFOROS else ''

    messages = (
        ChatHistoryModel.objects
        .filter(conversation__in=ids, message__role='assistant')
        .order_by('conversation_id', '-created_at', '-id')
        .values_list('conversation_id', 'message')
        .iterator(chunk_size=500)
    )
    for conversation_id, message in messages:
        if conversation_id in colors or not isinstance(message, dict):
            continue
        content = parse_json_object(message.get('content'))
        if not isinstance(content.get('crm_update'), dict) and 'semaforo' not in content:
            continue
        colors[conversation_id] = color_from(content)

    escalations = (
        ConversationEscalationModel.objects
        .filter(session_id__in=ids)
        .exclude(session_id__in=list(colors))
        .order_by('session_id', '-created_at', '-id')
        .values_list('session_id', 'conversation_content')
        .iterator(chunk_size=500)
    )
    for conversation_id, content in escalations:
        if conversation_id not in colors:
            colors[conversation_id] = color_from(content)

    return colors


def qualified_conversation_ids(conversations):
    """Ids con semáforo verde, compartidos por panel, estadísticas y reportes."""
    return {pk for pk, color in conversation_semaforos(conversations).items() if color == 'verde'}
