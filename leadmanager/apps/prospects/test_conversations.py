"""
Lecturas de las conversaciones de pruebas (agentes en modo shadow).

Viven en TestConversationModel / TestMessageModel, aparte de las
conversaciones reales. Este módulo les da la misma forma que usan la tabla
y el modal de Conversaciones, para mostrarlas en su propia vista sin
mezclarlas con producción.
"""

from leadmanager.apps.automations.models import TestMessageModel

from .crm_data import contact_from_crm, merge_crm_payload, parse_json_object
from .qualification import SEMAFOROS


__author__ = 'Ricardo'
__version__ = '0.1'


REPLY_KEYS = ('reply_to_customer_esp', 'response_to_customer_esp', 'reply_to_customer')


def reply_text(content):
    """Texto visible de una respuesta estructurada, o '' si no trae."""
    content = parse_json_object(content)
    return next((
        content[key] for key in REPLY_KEYS
        if isinstance(content.get(key), str) and content[key].strip()
    ), '')


def test_semaforos(test_conversations):
    """Último semáforo de cada conversación de pruebas, según su agente."""
    colors = {}
    rows = (
        TestMessageModel.objects
        .filter(test_conversation__in=test_conversations.values('pk'), message__role='assistant')
        .order_by('test_conversation_id', '-created_at', '-pk')
        .values_list('test_conversation_id', 'message')
        .iterator(chunk_size=500)
    )

    for test_conversation_id, message in rows:
        if test_conversation_id in colors:
            continue
        color = merge_crm_payload(message.get('content')).get('semaforo')
        color = color.strip().lower() if isinstance(color, str) else ''
        colors[test_conversation_id] = color if color in SEMAFOROS else ''

    return colors


def decorate_test_rows(rows, semaforos):
    """
    Agrega a cada conversación de pruebas los atributos que espera la
    tabla: último mensaje, contacto, semáforo y la atribución de la
    conversación real que acompaña.
    """
    rows = list(rows)
    last = {}
    contacts = {}

    messages = (
        TestMessageModel.objects
        .filter(test_conversation__in=[row.pk for row in rows])
        .order_by('test_conversation_id', '-created_at', '-pk')
    )

    for row in messages:
        cid = row.test_conversation_id
        role = row.message.get('role')
        content = row.message.get('content')

        if role == 'assistant':
            if cid not in contacts:
                contacto = merge_crm_payload(content).get('contacto')
                if isinstance(contacto, dict) and (contacto.get('nombre') or contacto.get('telefono')):
                    contacts[cid] = contacto
            content = reply_text(content)
            if not content:
                continue

        last.setdefault(cid, {'role': role, 'content': content})

    for row in rows:
        conversation = row.conversation
        prospect = conversation.prospect
        contact = contacts.get(row.pk, {})
        message = last.get(row.pk, {})

        row.semaforo = semaforos.get(row.pk, '')
        row.last_role = message.get('role')
        row.last_message = message.get('content', '')
        row.assistant_name = row.agent.name
        row.contact_name = contact.get('nombre') or (prospect.name if prospect else None)
        row.contact_phone = contact.get('telefono') or (prospect.phone if prospect else None)

        for field in ('source', 'utm_source', 'utm_medium', 'utm_campaign',
                      'utm_content', 'utm_term', 'traffic_origin'):
            setattr(row, field, getattr(conversation, field))

    return rows


def test_conversation_payload(test_conversation):
    """El JSON del modal para una conversación de pruebas."""
    history = []
    crm = None

    for row in test_conversation.messages.order_by('created_at', 'pk'):
        role = row.message.get('role')
        content = row.message.get('content')

        if role == 'assistant':
            # Igual que en producción: el panel CRM sale del crm_update de
            # la última respuesta del agente.
            parsed = parse_json_object(content)
            if isinstance(parsed.get('crm_update'), dict):
                crm = dict(parsed['crm_update'])
                crm['conversation_state'] = parsed.get('conversation_state')
            content = reply_text(content)
            if not content:
                continue

        history.append({
            'type': role,
            'content': content,
            'created_at': row.created_at,
            'id': row.pk,
        })

    if crm is not None:
        crm['is_qualified'] = False
    crm = contact_from_crm(crm)

    conversation = test_conversation.conversation

    return {
        'conversation': history,
        'crm': crm,
        'is_qualified': False,
        'test_agent_name': test_conversation.agent.name,
        'real_conversation_id': conversation.pk,
        'attribution': {
            'source': conversation.source or '',
            'utm_source': conversation.utm_source,
            'utm_medium': conversation.utm_medium,
            'utm_campaign': conversation.utm_campaign,
            'utm_content': conversation.utm_content,
            'utm_term': conversation.utm_term,
            'traffic_origin': conversation.traffic_origin,
        },
    }
