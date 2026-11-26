import json

from django.db import transaction

from leadmanager.apps.automations.agent_catalog import get_agent_configuration
from leadmanager.apps.automations.agent_configuration import render_response_prompt
from leadmanager.apps.automations.models import (
    AgentModel, ConversationAnalysisModel, LearningAnnotationModel, WeeklyAnalysisBatchModel,
)
from leadmanager.apps.prospects.models import ConversationModel
from ..prospector_agent.prospect_resource_handler import ProspectResourceHandler
from .conversation_analyzer_resource_handler import ConversationAnalyzerResourceHandler
from .schemas import ConversationAnalysisSchema


def load_conversation_step(agent, ctx):
    """La relación se acredita con las ejecuciones del agente destinatario."""
    conversation = ConversationModel.objects.get(pk=ctx['conversation_id'])
    target = AgentModel.objects.get(pk=ctx['agent_id'])
    if not conversation.agent_runs.filter(
        agent_name=target.key, mode=AgentModel.ModeEnum.PRODUCTION,
    ).exists():
        raise ValueError('El agente destinatario no tiene ejecuciones en esta conversación.')
    ctx['conversation'] = conversation
    ctx['learning_agent'] = target
    ctx['source'] = conversation.source or ''
    ctx['learning_annotations'] = []
    # No reutilizar resultados si quien llama vuelve a usar un contexto anterior.
    ctx.pop('conversation_analysis', None)
    ctx.pop('analysis_skip_reason', None)
    ctx.pop('_analysis_saved', None)


def check_learning_enabled_step(agent, ctx):
    target = ctx['learning_agent']
    if not target.is_active or not target.learning_enabled:
        ctx['analysis_skip_reason'] = 'learning_disabled'
        agent.log_event(ctx, 'Análisis omitido: aprendizaje deshabilitado para el agente.')


def build_analysis_evidence(conversation, target):
    """Forma una foto canónica del historial y de la configuración vigente."""
    messages = list(conversation.messages.order_by('created_at', 'pk').values_list('message', flat=True))
    if not messages:
        return None
    source = conversation.source or ''
    configuration, units = get_agent_configuration(target.key)
    template = ProspectResourceHandler.get(
        'generate_prospect_response_web_prompt' if source == 'web'
        else 'generate_prospect_response_prompt'
    )
    # Se reconstruyen las reglas vigentes, sin atribuirles una versión
    # histórica ni inventar datos de Odoo o resultados de herramientas.
    formatted_prompt = render_response_prompt(
        template, configuration, units,
        prefilled_data=(
            'Datos históricos precargados de Odoo no disponibles para reconstrucción; '
            'consultar únicamente el historial.'
        ),
        search_results=(
            'Resultados históricos de herramientas no reconstruidos; '
            'consultar únicamente el historial.'
        ),
    )
    # Historial y prompt evaluado son datos, no instrucciones del analizador.
    return json.dumps({
        'messages': messages,
        'prospector_context': {
            'agent_key': target.key,
            'source': source,
            'configuration_version': 'current',
            'historical_prompt_available': False,
            'formatted_prompt': formatted_prompt,
        },
    }, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def analyze_conversation_step(agent, ctx):
    if ctx.get('analysis_skip_reason'):
        return
    # La inferencia usa exactamente la evidencia cuya huella se reservó.
    evidence = (
        ctx['_analysis_evidence'] if '_analysis_evidence' in ctx
        else build_analysis_evidence(ctx['conversation'], ctx['learning_agent'])
    )
    if evidence is None:
        ctx['analysis_skip_reason'] = 'empty_conversation'
        agent.log_event(ctx, 'Análisis omitido: conversación sin mensajes.')
        return
    if len(evidence) > 120000:
        raise ValueError('La conversación y su prompt superan el límite de análisis de 120000 caracteres.')
    response = agent.response_model(
        system_prompt=(
            ctx['_analysis_system_prompt'] if '_analysis_system_prompt' in ctx
            else ConversationAnalyzerResourceHandler.get('analyze_conversation_prompt')
        ),
        input=[{'role': 'user', 'content': evidence}],
        type='parsed',
        text_format=ConversationAnalysisSchema,
    )
    if response.output_parsed is None:
        raise ValueError('El analizador no devolvió un resultado estructurado.')
    ctx['conversation_analysis'] = ConversationAnalysisSchema.model_validate(response.output_parsed)


@transaction.atomic
def save_learning_annotations_step(agent, ctx):
    if ctx.get('analysis_skip_reason'):
        return
    batch = None
    if ctx.get('_analysis_batch_token') is not None:
        # El lote se bloquea antes que sus resultados para conservar el
        # mismo orden de locks que la reserva y el cierre del servicio.
        batch = WeeklyAnalysisBatchModel.objects.select_for_update().get(
            pk=ctx['_analysis_batch_id'],
        )
        if (batch.claim_token != ctx['_analysis_batch_token']
                or batch.status != WeeklyAnalysisBatchModel.StatusEnum.RUNNING):
            ctx['analysis_skip_reason'] = 'analysis_batch_claim_lost'
            agent.log_event(ctx, 'Resultado omitido: la reserva del lote ya no pertenece a esta ejecución.')
            return
    state = None
    if ctx.get('_analysis_state_id') is not None:
        # El estado siempre se bloquea antes que el agente. Un worker cuyo
        # claim fue reemplazado no puede guardar propuestas ni finalizarlo.
        state = ConversationAnalysisModel.objects.select_for_update().get(
            pk=ctx['_analysis_state_id'],
        )
        if (state.claim_token != ctx['_analysis_claim_token']
                or state.status != ConversationAnalysisModel.StatusEnum.PROCESSING):
            ctx['analysis_skip_reason'] = 'analysis_claim_lost'
            agent.log_event(ctx, 'Anotaciones omitidas: la reserva del análisis ya no pertenece a esta ejecución.')
            return
    if ((batch is not None and (
            state is None or state.batch_id != ctx['_analysis_batch_id']))
            or (state is not None and state.batch_id is not None and batch is None)):
        ctx['analysis_skip_reason'] = 'analysis_batch_claim_lost'
        agent.log_event(ctx, 'Resultado omitido: el análisis no tiene una reserva válida de su lote.')
        return
    # Serializa guardados por agente y comprueba cambios durante la inferencia.
    target = AgentModel.objects.select_for_update().get(pk=ctx['learning_agent'].pk)
    if (not target.is_active or not target.learning_enabled
            or target.category != AgentModel.CategoryEnum.PROSPECTING
            or target.mode != AgentModel.ModeEnum.PRODUCTION):
        ctx['analysis_skip_reason'] = 'learning_disabled'
        agent.log_event(ctx, 'Anotaciones omitidas: se deshabilitó el aprendizaje durante el análisis.')
        return
    if state is not None:
        # La atribución puede cambiar mientras se realiza la inferencia.
        # El import local evita un ciclo con el servicio que usa estos pasos.
        from ...services.conversation_analysis import get_analysis_target

        current = get_analysis_target(ctx['conversation'])
        if current is None or current.pk != target.pk:
            ctx['analysis_skip_reason'] = 'analysis_not_eligible'
            agent.log_event(ctx, 'Anotaciones omitidas: la conversación ya no pertenece a un único agente elegible.')
            return
    created = []
    if batch is None:
        for annotation in ctx['conversation_analysis'].annotations:
            # La respuesta de la IA conserva sus llaves en español
            # (observaciones / sugerencias); el modelo las guarda en inglés.
            record, is_new = LearningAnnotationModel.objects.get_or_create(
                agent=target, observation=annotation.observaciones,
                suggestion=annotation.sugerencias,
                defaults={'is_active': False},
            )
            if is_new:
                created.append(record)
    ctx['learning_annotations'] = created
    if state is not None:
        # En un lote se conserva el resultado interno; las propuestas de
        # aprendizaje se guardan después, al confirmar el consolidado.
        state.completed_fingerprint = ctx['_analysis_fingerprint']
        state.status = ConversationAnalysisModel.StatusEnum.SUCCESS
        state.claim_token = None
        state.lease_until = None
        state.next_attempt_at = None
        state.last_error = ''
        state.last_run = ctx.get('_agent_run')
        state.annotations_created = len(created)
        fields = [
            'completed_fingerprint', 'status', 'claim_token', 'lease_until',
            'next_attempt_at', 'last_error', 'last_run', 'annotations_created',
            'updated_at',
        ]
        if batch is not None:
            state.result = ctx['conversation_analysis'].model_dump(mode='json')
            fields.append('result')
        state.save(update_fields=fields)
        ctx['_analysis_saved'] = True
    agent.log_event(
        ctx,
        'Resultado intermedio guardado para consolidación.' if batch is not None
        else 'Anotaciones guardadas para revisión.',
        data={'created': len(created)},
    )
