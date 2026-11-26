"""Vista previa aislada de una respuesta del agente de prospección."""

import json

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from .agent_catalog import PROMPTS_DIR, get_agent_configuration
from .agent_configuration import render_response_prompt
from .agents.agent_base import Agent
from .agents.prospector_agent.schemas.configured_answer import configured_answer_schema
from .services.conversation_reminders import reminder_email_content


def _silence_preview(crm):
    if crm['semaforo'] != 'amarillo':
        return 'Sin recordatorio sugerido: el semáforo no está en amarillo.'
    contact = crm.get('contacto') or {}
    email = contact.get('correo') if isinstance(contact, dict) else None
    try:
        validate_email(email.strip() if isinstance(email, str) else email)
    except (ValidationError, TypeError):
        return 'Sin recordatorio sugerido: falta un correo válido.'
    return 'Tras 48 horas sin respuesta, podría enviarse un recordatorio por correo. La prueba solo muestra el mensaje.'


def preview_agent_response(agent_key, messages):
    """Usa el prompt y esquema del canal web sin ejecutar el pipeline comercial."""
    configuration, units = get_agent_configuration(agent_key)
    template = (PROMPTS_DIR / 'generate_prospect_response_web_prompt.txt').read_text(encoding='utf-8')
    system_prompt = render_response_prompt(
        template, configuration, units,
        prefilled_data='', search_results='-consulta no hecha',
    )
    response = Agent(name=agent_key, steps=[]).response_model(
        system_prompt=system_prompt,
        input=messages,
        type='parsed',
        model='gpt-5.6-terra',
        text_format=configured_answer_schema('web', tuple(unit['name'] for unit in units)),
    )
    if response.output_parsed is None:
        raise ValueError('El agente no devolvió una respuesta estructurada.')
    answer = response.output_parsed.model_dump(mode='json')
    crm = answer['crm_update']
    semaforo = crm['semaforo']
    interest_detail = {
        'verde': 'Calificado para seguimiento comercial.',
        'amarillo': 'Interés en proceso de evaluación.',
        'rojo': 'Descartado por la clasificación del agente.',
        'gris': 'Sin interés comercial identificado.',
    }[semaforo]
    reminder_subject, reminder_body = reminder_email_content(configuration['name'])
    return {
        'reply': answer['reply_to_customer_esp'] or answer['reply_to_customer'],
        'assistant_message': json.dumps(answer, ensure_ascii=False),
        'handoff_suggested': crm['escalar_a_humano'],
        'handoff_reason': crm['motivo_escalamiento'],
        'semaforo': semaforo,
        'interest_detected': semaforo in ('verde', 'amarillo'),
        'interest_detail': interest_detail,
        'silence_preview': _silence_preview(crm),
        'reminder_subject': reminder_subject,
        'reminder_body': reminder_body,
    }
