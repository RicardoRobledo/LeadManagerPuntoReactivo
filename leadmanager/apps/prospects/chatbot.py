import asyncio
import json
from asgiref.sync import sync_to_async
from datetime import timedelta
from string import Template
from zoneinfo import ZoneInfo

from django.utils import timezone
from openai import AsyncOpenAI

from .prompt_handlers import PromptHandler
from .models import ChatHistoryModel, ConversationStateEnum


client = AsyncOpenAI()

mx_zi = ZoneInfo('America/Mexico_City')

MODEL = 'gpt-5.6-luna'
MAX_DATA_LENGTH = 645638
HISTORY_LIMIT = 15


def _extract_prospect(message):
    """
    Extrae los datos del prospecto desde el mensaje del asistente que trae la
    respuesta estructurada (``crm_update``).

    Devuelve un dict con ``correo``, ``telefono``, ``resumen_conversacion`` y
    ``unidad_negocio``, o ``None`` si el mensaje no es la respuesta final de la
    IA (por ejemplo, un resultado de búsqueda intermedio en texto plano).
    """
    if not isinstance(message, dict) or message.get('role') != 'assistant':
        return None

    content = message.get('content')

    # La respuesta final viaja como string JSON dentro del contenido.
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return None

    if not isinstance(content, dict):
        return None

    crm_update = content.get('crm_update')
    if not isinstance(crm_update, dict):
        return None

    contacto = crm_update.get('contacto')
    if not isinstance(contacto, dict):
        contacto = {}

    return {
        'correo': contacto.get('correo'),
        'telefono': contacto.get('telefono'),
        'resumen_conversacion': crm_update.get('resumen_conversacion'),
        'unidad_negocio': crm_update.get('unidad_negocio_detectada'),
    }


SUMMARIZE_PROMPT = (
    'Resume el siguiente historial de conversación en un único párrafo conciso. '
    'Conserva los datos relevantes: información del prospecto, necesidades, '
    'decisiones tomadas y preguntas ya resueltas. Devuelve únicamente el resumen, '
    'sin introducción ni comentarios.'
)


async def _summarize_context(messages) -> str:
    """Condensa el historial en un solo mensaje para no reenviarlo completo."""
    response = await client.responses.create(
        model=MODEL,
        input=[
            {'role': 'system', 'content': SUMMARIZE_PROMPT},
            {'role': 'user', 'content': json.dumps(
                messages, ensure_ascii=False, default=str)},
        ],
    )

    return response.output_text


async def create_response(messages) -> str:
    now = timezone.now().astimezone(mx_zi)
    start_date = now - timedelta(days=30)

    # ------------------------------------
    async def get_recent_prospects():
        """Por conversación, toma el último mensaje del asistente con
        ``crm_update`` y extrae los datos del prospecto."""
        def query_prospects():
            prospects = []
            seen = set()

            messages_qs = (
                ChatHistoryModel.objects
                .filter(
                    created_at__gte=start_date,
                    message__role='assistant',
                    conversation__state=ConversationStateEnum.OPEN,
                )
                .order_by('conversation_id', '-created_at')
            )

            for msg in messages_qs:
                conversation_id = msg.conversation_id

                if conversation_id is None or conversation_id in seen:
                    continue

                prospect = _extract_prospect(msg.message)
                if prospect is None:
                    continue

                seen.add(conversation_id)
                prospects.append(prospect)

            return prospects

        return await sync_to_async(query_prospects)()

    prospects = await get_recent_prospects()

    data = {
        'last_30_days_prospects': prospects,
    }

    system_prompt = Template(
        PromptHandler.system_prompt
    ).substitute(data=str(data)[:MAX_DATA_LENGTH])

    history = list(messages)

    if len(history) >= HISTORY_LIMIT:
        latest = history[-1]
        summary = await _summarize_context(history[:-1])
        history = [{'role': 'assistant', 'content': summary}, latest]

    response = await client.responses.create(
        model=MODEL,
        input=[
            {'role': 'system', 'content': system_prompt}
        ] + history,
        reasoning={'effort': 'low'},
        text={'verbosity': 'low'}
    )

    return response.output_text
