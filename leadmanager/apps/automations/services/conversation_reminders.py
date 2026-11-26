"""Resuelve el correo de seguimiento sin depender del canal de la conversación."""

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from leadmanager.apps.prospects.crm_data import merge_crm_payload, parse_json_object
from leadmanager.apps.prospects.models import ChatHistoryModel, ConversationSourceEnum
from leadmanager.apps.automations.models import AgentChannelAssignment, AgentModel


REMINDER_SUBJECT = '¿Te ayudamos a dar seguimiento a tu consulta?'


def reminder_agent_names():
    """Nombres por canal configurado y respaldo para conversaciones sin asignación."""
    names = dict(AgentChannelAssignment.objects.filter(
        active=True, agent__is_active=True,
        agent__category=AgentModel.CategoryEnum.PROSPECTING,
        agent__mode=AgentModel.ModeEnum.PRODUCTION,
    ).values_list('channel', 'agent__name'))
    default = AgentModel.objects.filter(key='prospector').values_list('name', flat=True).first()
    return names, default or 'Asistente comercial'


def reminder_email_content(sender_name):
    """Asunto y cuerpo usados por el envío real y la vista previa de silencio."""
    body = (
        f"Hola, ¿Qué tal?, soy {sender_name}.\n\n"
        'Hace unos días te contactaste con nosotros y nos quedamos pendientes de darle seguimiento a tu consulta.\n\n'
        '¿Necesitas que te contactemos con uno de nuestros especialistas para adquirir alguno de nuestros servicios? Con gusto te ayudamos.\n\n'
        '— Equipo Estrasol'
    )
    return REMINDER_SUBJECT, body


def latest_schema_emails(conversations):
    """Lee el correo del último análisis CRM de cada conversación en una consulta."""
    emails = {}
    messages = (
        ChatHistoryModel.objects
        .filter(conversation__in=conversations, message__role='assistant')
        .order_by('conversation_id', '-created_at', '-id')
        .values_list('conversation_id', 'message')
        .iterator(chunk_size=500)
    )
    for conversation_id, message in messages:
        if conversation_id in emails or not isinstance(message, dict):
            continue
        content = parse_json_object(message.get('content'))
        if not isinstance(content.get('crm_update'), dict) and 'semaforo' not in content:
            continue
        contact = merge_crm_payload(content).get('contacto')
        emails[conversation_id] = contact.get('correo') if isinstance(contact, dict) else None
    return emails


def reminder_recipient(conversation, schema_email=None):
    """Prioriza el correo del análisis CRM y conserva respaldos para datos antiguos."""
    candidates = [
        (schema_email, 'crm_update.contacto.correo'),
        (getattr(conversation.prospect, 'email', None), 'prospect.email'),
    ]
    if conversation.source in (ConversationSourceEnum.EMAIL, ConversationSourceEnum.FORM):
        candidates.append((conversation.session_id, 'session_id'))

    for value, origin in candidates:
        address = value.strip() if isinstance(value, str) else ''
        if not address:
            continue
        try:
            validate_email(address)
        except ValidationError:
            continue
        return address, origin
    return None, None
