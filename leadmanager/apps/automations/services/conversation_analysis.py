"""Análisis con seguimiento durable, exclusión entre workers y reintentos."""

import hashlib
import json
import logging
import uuid
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from leadmanager.apps.prospects.models import ConversationModel

from ..agents.conversation_analyzer_agent.conversation_analyzer import conversation_analyzer_agent
from ..agents.conversation_analyzer_agent.conversation_analyzer_resource_handler import ConversationAnalyzerResourceHandler
from ..agents.conversation_analyzer_agent.conversation_analyzer_steps import build_analysis_evidence
from ..agents.conversation_analyzer_agent.schemas import ConversationAnalysisSchema
from ..models import AgentModel, ConversationAnalysisModel, WeeklyAnalysisBatchModel


logger = logging.getLogger(__name__)
MAX_ANALYSIS_ATTEMPTS = 3
ANALYSIS_RETRY_DELAY = timedelta(minutes=15)
ANALYSIS_LEASE = timedelta(minutes=30)


def analyzer_enabled():
    return AgentModel.objects.filter(
        key='conversation_analyzer', category=AgentModel.CategoryEnum.ANALYSIS,
        is_active=True, mode=AgentModel.ModeEnum.PRODUCTION,
    ).exists()


def get_analysis_target(conversation):
    """Resuelve el único autor de producción, incluyendo autores eliminados."""
    analysts = AgentModel.objects.filter(category=AgentModel.CategoryEnum.ANALYSIS).values('key')
    owners = set(conversation.agent_runs.filter(
        mode=AgentModel.ModeEnum.PRODUCTION,
    ).exclude(agent_name__in=analysts).exclude(agent_name='conversation_analyzer')
        .values_list('agent_name', flat=True))
    if len(owners) != 1:
        return None
    return AgentModel.objects.filter(
        key=owners.pop(), category=AgentModel.CategoryEnum.PROSPECTING,
        mode=AgentModel.ModeEnum.PRODUCTION, is_active=True, learning_enabled=True,
    ).first()


@transaction.atomic
def _claim_analysis(conversation, target, fingerprint, ctx):
    if ctx.get('_analysis_batch_id') is not None:
        batch = WeeklyAnalysisBatchModel.objects.select_for_update().get(pk=ctx['_analysis_batch_id'])
        if (batch.claim_token != ctx.get('_analysis_batch_token')
                or batch.status != batch.StatusEnum.RUNNING):
            return None, None, 'analysis_batch_lost'
        state = ConversationAnalysisModel.objects.get(
            pk=ctx['_analysis_state_id'], batch=batch, conversation=conversation, agent=target,
        )
    else:
        state, _ = ConversationAnalysisModel.objects.get_or_create(
            conversation=conversation, agent=target, batch=None,
        )
    state = ConversationAnalysisModel.objects.select_for_update().get(pk=state.pk)
    now = timezone.now()
    if state.batch_id and state.status == state.StatusEnum.SUCCESS and state.result is not None:
        return state, None, 'unchanged'
    # En un lote, la reserva del padre determina quién puede retomar el elemento.
    if (not state.batch_id and state.status == state.StatusEnum.PROCESSING
            and state.lease_until is not None and state.lease_until > now):
        return state, None, 'analysis_in_progress'
    if state.completed_fingerprint == fingerprint:
        # Puede haberse vuelto a la última configuración ya analizada.
        state.status = state.StatusEnum.SUCCESS
        state.fingerprint = fingerprint
        state.claim_token = state.lease_until = state.next_attempt_at = None
        state.last_error = ''
        state.save(update_fields=[
            'status', 'fingerprint', 'claim_token', 'lease_until', 'next_attempt_at', 'last_error', 'updated_at',
        ])
        return state, None, 'unchanged'
    if (state.batch_id or state.fingerprint == fingerprint) and state.status in (
        state.StatusEnum.FAILED, state.StatusEnum.PROCESSING,
    ):
        if state.attempts >= MAX_ANALYSIS_ATTEMPTS:
            state.status = state.StatusEnum.FAILED
            if not state.last_error:
                state.last_error = 'Se agotaron los intentos; la ejecución anterior no terminó.'
            state.claim_token = state.lease_until = state.next_attempt_at = None
            state.save(update_fields=[
                'status', 'claim_token', 'lease_until', 'next_attempt_at', 'last_error', 'updated_at',
            ])
            return state, None, 'analysis_attempts_exhausted'
        if state.next_attempt_at is not None and state.next_attempt_at > now:
            return state, None, 'analysis_retry_pending'
    elif not state.batch_id:
        state.attempts = 0
    state.fingerprint = fingerprint
    state.status = state.StatusEnum.PROCESSING
    state.attempts += 1
    state.claim_token = uuid.uuid4()
    state.lease_until = now + ANALYSIS_LEASE
    state.next_attempt_at = None
    state.last_error = ''
    state.annotations_created = 0
    state.save()
    if state.batch_id:
        batch.lease_until = state.lease_until
        batch.save(update_fields=['lease_until', 'updated_at'])
    return state, state.claim_token, None


@transaction.atomic
def _release_analysis(state, token, *, ctx, error=None):
    """Solo quien conserva el token puede modificar el intento actual."""
    failed = error is not None
    if state.batch_id:
        batch = WeeklyAnalysisBatchModel.objects.select_for_update().get(pk=state.batch_id)
        if batch.claim_token != ctx.get('_analysis_batch_token') or batch.status != batch.StatusEnum.RUNNING:
            return 0
    return ConversationAnalysisModel.objects.filter(
        pk=state.pk, claim_token=token, status=ConversationAnalysisModel.StatusEnum.PROCESSING,
    ).update(
        status=(ConversationAnalysisModel.StatusEnum.FAILED if failed
                else ConversationAnalysisModel.StatusEnum.SKIPPED),
        claim_token=None, lease_until=None,
        next_attempt_at=(timezone.now() + ANALYSIS_RETRY_DELAY
                         if failed and state.attempts < MAX_ANALYSIS_ATTEMPTS else None),
        last_error=(str(error) if failed else ctx.get('analysis_skip_reason', ''))[:2000],
        last_run=ctx.get('_agent_run'), updated_at=timezone.now(),
    )


def run_tracked_analysis(ctx):
    """Congela la entrada una vez; las notas y el éxito se confirman juntos."""
    conversation = ConversationModel.objects.get(pk=ctx['conversation_id'])
    target = get_analysis_target(conversation)
    if not analyzer_enabled() or target is None or target.pk != ctx['agent_id']:
        return {**ctx, 'analysis_skip_reason': 'analysis_not_eligible', 'learning_annotations': []}
    preparation_error = None
    try:
        evidence = build_analysis_evidence(conversation, target)
        system_prompt = ConversationAnalyzerResourceHandler.get('analyze_conversation_prompt')
        signature = json.dumps({
            'evidence': evidence, 'system_prompt': system_prompt,
            'schema': ConversationAnalysisSchema.model_json_schema(),
        }, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    except Exception as exc:
        # También quedan registrados los fallos anteriores a la inferencia.
        preparation_error = exc
        signature = f'preparation:{type(exc).__name__}:{exc}'
    fingerprint = hashlib.sha256(signature.encode('utf-8')).hexdigest()
    state, token, skip_reason = _claim_analysis(conversation, target, fingerprint, ctx)
    if skip_reason:
        return {**ctx, 'analysis_skip_reason': skip_reason, 'learning_annotations': []}
    ctx = {
        **ctx, '_analysis_state_id': state.pk, '_analysis_claim_token': token,
        '_analysis_fingerprint': fingerprint,
    }
    try:
        if preparation_error is not None:
            raise preparation_error
        ctx['_analysis_evidence'] = evidence
        ctx['_analysis_system_prompt'] = system_prompt
        ctx = conversation_analyzer_agent.run(ctx)
        if ctx.get('analysis_skip_reason'):
            _release_analysis(state, token, ctx=ctx)
        elif not ctx.get('_analysis_saved'):
            raise RuntimeError('El analizador no confirmó el guardado de su seguimiento.')
        return ctx
    except Exception as exc:
        _release_analysis(state, token, ctx=ctx, error=exc)
        raise


def retry_due_analyses():
    """Recupera fallos y workers detenidos, aunque su semana ya haya pasado."""
    result = {'procesadas': 0, 'analizadas': 0, 'omitidas': 0, 'fallidas': 0, 'anotaciones_creadas': 0}
    if not analyzer_enabled():
        result['motivo_omision'] = 'analizador_inactivo'
        return result
    now = timezone.now()
    due = (
        Q(status=ConversationAnalysisModel.StatusEnum.PENDING)
        | Q(status=ConversationAnalysisModel.StatusEnum.FAILED, next_attempt_at__lte=now)
        | Q(status=ConversationAnalysisModel.StatusEnum.PROCESSING, lease_until__lte=now)
    )
    # Los elementos de un lote solo pueden retomarse mediante su coordinador.
    states = list(ConversationAnalysisModel.objects.filter(due, batch__isnull=True)
                  .select_related('conversation').order_by('updated_at', 'pk')[:50])
    for state in states:
        result['procesadas'] += 1
        try:
            ctx = run_tracked_analysis({
                'conversation_id': state.conversation_id, 'agent_id': state.agent_id,
                'source': state.conversation.source or '',
            })
            if ctx.get('analysis_skip_reason'):
                result['omitidas'] += 1
                if ctx['analysis_skip_reason'] == 'analysis_not_eligible':
                    # No conserva una reserva nueva de otro worker concurrente.
                    ConversationAnalysisModel.objects.filter(pk=state.pk).filter(due).update(
                        status=ConversationAnalysisModel.StatusEnum.SKIPPED,
                        claim_token=None, lease_until=None, next_attempt_at=None,
                        last_error='analysis_not_eligible', updated_at=timezone.now(),
                    )
            else:
                result['analizadas'] += 1
                result['anotaciones_creadas'] += len(ctx.get('learning_annotations', []))
        except Exception:
            result['fallidas'] += 1
            logger.exception('Falló el reintento del análisis %s.', state.pk)
    return result
