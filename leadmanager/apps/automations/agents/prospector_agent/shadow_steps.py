"""Pasos de pruebas: calculan respuestas y registran resultados internos."""

from django.db import transaction

from leadmanager.apps.automations.models import (
    AgentModel, TestConversationModel, TestMessageModel,
)
from leadmanager.apps.prospects.models import ChatHistoryModel

from .prospector_steps import (
    _generate_prospect_response,
    _search_information,
    load_knowledge_catalog_step,
)


def search_shadow_information_step(agent, ctx):
    """Busca información sin escribir en el historial de la conversación."""
    return _search_information(agent, ctx, persist_history=False)


def generate_shadow_response_step(agent, ctx):
    """Genera una respuesta interna con la configuración del agente de pruebas."""
    return _generate_prospect_response(agent, ctx, persist_history=False)


def record_shadow_result_step(agent, ctx):
    """Conserva respuesta y clasificación únicamente en la ejecución de pruebas."""
    result = ctx['response_to_prospect']
    ctx['_shadow_result'] = result
    agent.log_event(
        ctx,
        message='Resultado de pruebas; respuesta no enviada al cliente',
        data={
            'mode': AgentModel.ModeEnum.SHADOW_TESTING,
            'production_run_id': ctx.get('_production_run_id'),
            'response_to_prospect': result,
        },
    )


def save_test_conversation_step(agent, ctx):
    """
    Guarda el turno en la conversación de pruebas del agente.

    Escribe solo en TestConversationModel / TestMessageModel: la
    conversación real, su historial, los prospectos y Odoo no se tocan.
    Es comparación por turno: el agente de pruebas recibió el historial
    real (con las respuestas de producción) y aquí queda lo que habría
    contestado con ese mismo contexto.
    """
    conversation = ctx.get('conversation')
    if conversation is None:
        raise ValueError('La prueba no tiene una conversación real asociada.')

    test_agent = AgentModel.objects.get(key=agent.name)
    user_message = next((
        message for message in reversed(ctx.get('history') or [])
        if message.get('role') == 'user'
    ), None)

    # Si producción revirtió su turno, el mensaje real ya no existe.
    source_message_id = ctx.get('source_message_id')
    if source_message_id and not ChatHistoryModel.objects.filter(pk=source_message_id).exists():
        source_message_id = None

    with transaction.atomic():
        test_conversation, _ = TestConversationModel.objects.get_or_create(
            agent=test_agent, conversation=conversation,
        )
        common = {
            'test_conversation': test_conversation,
            'run': ctx.get('_agent_run'),
            'source_message_id': source_message_id,
        }
        rows = []
        if user_message:
            rows.append(TestMessageModel(**common, message={
                'role': 'user', 'content': user_message.get('content', ''),
            }))
        rows.append(TestMessageModel(**common, message={
            'role': 'assistant', 'content': ctx['response_to_prospect'],
        }))
        TestMessageModel.objects.bulk_create(rows)
        # bulk_create no dispara auto_now en la conversación.
        test_conversation.save(update_fields=['updated_at'])

    agent.log_event(
        ctx,
        message='Turno guardado en la conversación de pruebas',
        data={'test_conversation_id': test_conversation.pk,
              'source_message_id': source_message_id},
    )


SHADOW_STEPS = (
    load_knowledge_catalog_step,
    search_shadow_information_step,
    generate_shadow_response_step,
    record_shadow_result_step,
    save_test_conversation_step,
)
