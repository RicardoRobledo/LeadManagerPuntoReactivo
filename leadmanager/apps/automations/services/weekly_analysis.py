"""Lotes semanales secuenciales con resultados internos y consolidación final."""

import json
import logging
import uuid
from collections import defaultdict

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from leadmanager.apps.prospects.models import ConversationModel

from ..agents.agent_base import Agent
from ..agents.conversation_analyzer_agent.conversation_analyzer_resource_handler import ConversationAnalyzerResourceHandler
from ..agents.conversation_analyzer_agent.schemas import FinalAnalysisSchema
from ..models import AgentModel, ConversationAnalysisModel, LearningAnnotationModel, WeeklyAnalysisBatchModel
from .conversation_analysis import (
    ANALYSIS_LEASE, ANALYSIS_RETRY_DELAY, MAX_ANALYSIS_ATTEMPTS,
    analyzer_enabled, get_analysis_target, run_tracked_analysis,
)


logger = logging.getLogger(__name__)
FINAL_CHUNK_LIMIT = 100000


def prepare_weekly_batches(start, end):
    """Una lista fija por agente y semana; repetir la tarea no la amplía."""
    grouped = defaultdict(list)
    conversations = ConversationModel.objects.filter(
        Q(messages__created_at__gte=start, messages__created_at__lt=end)
        | Q(messages__updated_at__gte=start, messages__updated_at__lt=end),
    ).order_by('pk').distinct()
    for conversation in conversations.iterator():
        target = get_analysis_target(conversation)
        if target is not None:
            grouped[target.pk].append(conversation.pk)
    batches = []
    # Cada lote y todos sus elementos se confirman antes de llamar a la IA.
    with transaction.atomic():
        for agent_id, ids in grouped.items():
            batch, created = WeeklyAnalysisBatchModel.objects.get_or_create(
                agent_id=agent_id, week_start=start.date(),
                defaults={'week_end': end.date(), 'conversation_ids': ids},
            )
            if created:
                ConversationAnalysisModel.objects.bulk_create([
                    ConversationAnalysisModel(
                        batch=batch, agent_id=agent_id, conversation_id=conversation_id, position=position,
                    ) for position, conversation_id in enumerate(ids)
                ])
            batches.append(batch)
        # Incluye lotes ya creados aunque su agente o sus mensajes hayan cambiado.
        known = {batch.pk for batch in batches}
        batches.extend(WeeklyAnalysisBatchModel.objects.filter(
            week_start=start.date(), week_end=end.date(),
        ).exclude(pk__in=known).order_by('pk'))
    return batches


@transaction.atomic
def _claim_batch(batch_id):
    batch = WeeklyAnalysisBatchModel.objects.select_for_update().get(pk=batch_id)
    now = timezone.now()
    if batch.status in (batch.StatusEnum.SUCCESS, batch.StatusEnum.FAILED):
        return batch, None
    if batch.status == batch.StatusEnum.RUNNING and batch.lease_until and batch.lease_until > now:
        return batch, None
    if batch.next_attempt_at and batch.next_attempt_at > now:
        return batch, None
    batch.status = batch.StatusEnum.RUNNING
    batch.claim_token = uuid.uuid4()
    batch.lease_until = now + ANALYSIS_LEASE
    batch.next_attempt_at = None
    batch.save(update_fields=['status', 'claim_token', 'lease_until', 'next_attempt_at', 'updated_at'])
    return batch, batch.claim_token


def _owned(batch_id, token):
    return WeeklyAnalysisBatchModel.objects.filter(
        pk=batch_id, claim_token=token, status=WeeklyAnalysisBatchModel.StatusEnum.RUNNING,
    )


def _heartbeat(batch_id, token):
    if not _owned(batch_id, token).update(lease_until=timezone.now() + ANALYSIS_LEASE, updated_at=timezone.now()):
        raise RuntimeError('La ejecución ya no conserva la reserva del lote.')


def _pause(batch, token, error, *, exhausted=False, next_attempt=None):
    _owned(batch.pk, token).update(
        status=(batch.StatusEnum.FAILED if exhausted else batch.StatusEnum.WAITING_RETRY),
        claim_token=None, lease_until=None, last_error=str(error)[:2000],
        next_attempt_at=None if exhausted else (next_attempt or timezone.now() + ANALYSIS_RETRY_DELAY),
        updated_at=timezone.now(),
    )


def _eligible(batch):
    return analyzer_enabled() and AgentModel.objects.filter(
        pk=batch.agent_id, category=AgentModel.CategoryEnum.PROSPECTING,
        is_active=True, learning_enabled=True, mode=AgentModel.ModeEnum.PRODUCTION,
    ).exists()


@transaction.atomic
def _record_item_error(batch, token, item, previous_attempts, error):
    parent = WeeklyAnalysisBatchModel.objects.select_for_update().get(pk=batch.pk)
    if parent.claim_token != token or parent.status != parent.StatusEnum.RUNNING:
        return
    current = ConversationAnalysisModel.objects.select_for_update().get(pk=item.pk)
    if current.status == current.StatusEnum.SUCCESS:
        return
    # Los fallos de preparación/atribución también consumen un intento.
    if current.attempts == previous_attempts and current.attempts < MAX_ANALYSIS_ATTEMPTS:
        current.attempts += 1
    current.status = current.StatusEnum.FAILED
    current.claim_token = current.lease_until = None
    current.next_attempt_at = (
        timezone.now() + ANALYSIS_RETRY_DELAY if current.attempts < MAX_ANALYSIS_ATTEMPTS else None
    )
    current.last_error = str(error)[:2000]
    current.save()


def _chunks(values):
    chunks, current, size = [], [], 0
    for value in values:
        length = len(json.dumps(value, ensure_ascii=False)) + 2
        if length > FINAL_CHUNK_LIMIT:
            raise ValueError('Un resultado intermedio supera el límite de consolidación.')
        if current and size + length > FINAL_CHUNK_LIMIT:
            chunks.append(current)
            current, size = [], 0
        current.append(value)
        size += length
    if current or not chunks:
        chunks.append(current)
    return chunks


def _consolidate_step(agent, ctx):
    """Reduce por bloques si es necesario y guarda un único resultado final."""
    batch = WeeklyAnalysisBatchModel.objects.get(pk=ctx['batch_id'])
    token = ctx['batch_token']
    progress = batch.final_input
    while True:
        chunks = _chunks(progress['values'])
        terminal = len(chunks) == 1
        index = progress['index']
        _heartbeat(batch.pk, token)
        evidence = json.dumps({
            'agent_key': batch.agent.key,
            'week_start': batch.week_start.isoformat(),
            'week_end_exclusive': batch.week_end.isoformat(),
            'total_conversations': len(batch.conversation_ids),
            'stage': 'final' if terminal else 'partial_consolidation',
            'results': chunks[index],
        }, ensure_ascii=False)
        response = agent.response_model(
            system_prompt=ConversationAnalyzerResourceHandler.get('consolidate_weekly_analysis_prompt'),
            input=[{'role': 'user', 'content': evidence}], type='parsed', text_format=FinalAnalysisSchema,
        )
        if response.output_parsed is None:
            raise ValueError('La consolidación no devolvió un resultado estructurado.')
        value = FinalAnalysisSchema.model_validate(response.output_parsed).model_dump(mode='json')
        if terminal:
            _publish_final(batch, token, value, ctx)
            return
        # Conserva los bloques ya consolidados para no repetirlos al reintentar.
        progress['reduced'].append(value)
        progress['index'] += 1
        if progress['index'] == len(chunks):
            progress = {'values': progress['reduced'], 'reduced': [], 'index': 0}
        if not _owned(batch.pk, token).update(final_input=progress, updated_at=timezone.now()):
            raise RuntimeError('Se perdió la reserva durante la consolidación.')


@transaction.atomic
def _publish_final(batch, token, value, ctx):
    locked = WeeklyAnalysisBatchModel.objects.select_for_update().get(pk=batch.pk)
    if locked.claim_token != token or locked.status != locked.StatusEnum.RUNNING:
        raise RuntimeError('La consolidación ya no conserva la reserva del lote.')
    target = AgentModel.objects.select_for_update().get(pk=locked.agent_id)
    if not _eligible(locked):
        raise ValueError('Se deshabilitó el aprendizaje antes de publicar la consolidación.')
    # Una sola anotación por lote, pendiente de aprobación. value es la
    # respuesta de la IA, con llaves en español.
    annotation, created = LearningAnnotationModel.objects.get_or_create(
        agent=target, observation=value['observaciones'],
        suggestion=value['sugerencias'], defaults={'is_active': False},
    )
    locked.status = locked.StatusEnum.SUCCESS
    locked.final_result = value
    locked.annotation = annotation
    locked.last_run = ctx['_agent_run']
    locked.last_error = ''
    locked.claim_token = locked.lease_until = locked.next_attempt_at = None
    locked.save()
    ctx['_agent_run'].result = value
    ctx['_agent_run'].save(update_fields=['result'])
    ctx['anotaciones_creadas'] = int(created)


@transaction.atomic
def _begin_final(batch, token):
    locked = WeeklyAnalysisBatchModel.objects.select_for_update().get(pk=batch.pk)
    if locked.claim_token != token or locked.status != locked.StatusEnum.RUNNING:
        raise RuntimeError('El lote ya pertenece a otra ejecución.')
    if locked.final_attempts >= MAX_ANALYSIS_ATTEMPTS:
        raise ValueError('Se agotaron los intentos de consolidación.')
    if locked.final_input is None:
        items = list(locked.items.order_by('position', 'pk'))
        if ([item.conversation_id for item in items] != locked.conversation_ids
                or any(item.status != item.StatusEnum.SUCCESS or item.result is None for item in items)):
            raise ValueError('El lote no tiene todos sus resultados individuales completos.')
        values = []
        for item in items:
            value = {'conversation_id': item.conversation_id, 'analysis': item.result}
            if len(json.dumps(value, ensure_ascii=False)) + 2 <= FINAL_CHUNK_LIMIT:
                values.append(value)
            else:
                values.extend(
                    {'conversation_id': item.conversation_id, 'analysis': {'annotations': [annotation]}}
                    for annotation in item.result['annotations']
                )
        locked.final_input = {'values': values, 'reduced': [], 'index': 0}
    locked.phase = 'final'
    locked.final_attempts += 1
    locked.save(update_fields=['phase', 'final_attempts', 'final_input', 'updated_at'])
    return locked


def run_weekly_batch(batch_id):
    """Retoma el primer elemento incompleto; un fallo detiene solo este lote."""
    result = {'analizadas': 0, 'anotaciones_creadas': 0, 'fallidas': 0, 'omitidas': 0}
    batch, token = _claim_batch(batch_id)
    if token is None:
        return {**result, 'status': batch.status, 'omitidas': 1}
    try:
        if not _eligible(batch):
            _pause(batch, token, 'Agente o aprendizaje deshabilitado.', exhausted=True)
            return {**result, 'status': batch.StatusEnum.FAILED, 'omitidas': 1}
        items = list(batch.items.order_by('position', 'pk'))
        if [item.conversation_id for item in items] != batch.conversation_ids:
            raise ValueError('Se modificó la lista fija de conversaciones del lote.')
        for item in items:
            if item.status == item.StatusEnum.SUCCESS and item.result is not None:
                continue
            if item.attempts >= MAX_ANALYSIS_ATTEMPTS:
                error = item.last_error or 'Intentos individuales agotados.'
                _record_item_error(batch, token, item, item.attempts, error)
                _pause(batch, token, error, exhausted=True)
                return {**result, 'status': batch.StatusEnum.FAILED, 'fallidas': 1}
            if item.next_attempt_at and item.next_attempt_at > timezone.now():
                _pause(batch, token, item.last_error, next_attempt=item.next_attempt_at)
                return {**result, 'status': batch.StatusEnum.WAITING_RETRY}
            _heartbeat(batch.pk, token)
            previous_attempts = item.attempts
            try:
                ctx = run_tracked_analysis({
                    'conversation_id': item.conversation_id, 'agent_id': batch.agent_id,
                    '_analysis_state_id': item.pk,
                    '_analysis_batch_id': batch.pk, '_analysis_batch_token': token,
                })
                item.refresh_from_db()
                if item.status != item.StatusEnum.SUCCESS or item.result is None:
                    raise RuntimeError(ctx.get('analysis_skip_reason') or 'El resultado individual no se confirmó.')
                result['analizadas'] += 1
            except Exception as exc:
                _record_item_error(batch, token, item, previous_attempts, exc)
                item.refresh_from_db()
                _pause(batch, token, exc, exhausted=item.attempts >= MAX_ANALYSIS_ATTEMPTS,
                       next_attempt=item.next_attempt_at)
                return {**result, 'status': (
                    batch.StatusEnum.FAILED if item.attempts >= MAX_ANALYSIS_ATTEMPTS else batch.StatusEnum.WAITING_RETRY
                ), 'fallidas': 1}
        final_ctx = {'batch_id': batch.pk, 'batch_token': token, 'source': 'weekly_analysis'}
        try:
            batch = _begin_final(batch, token)
            ctx = Agent(name='conversation_analyzer', steps=[_consolidate_step]).run(final_ctx)
            result['anotaciones_creadas'] = ctx['anotaciones_creadas']
        except Exception as exc:
            batch.refresh_from_db()
            if batch.status == batch.StatusEnum.SUCCESS:
                return {**result, 'status': batch.StatusEnum.SUCCESS}
            if final_ctx.get('_agent_run') is not None:
                _owned(batch.pk, token).update(last_run=final_ctx['_agent_run'])
            _pause(batch, token, exc, exhausted=batch.final_attempts >= MAX_ANALYSIS_ATTEMPTS)
            return {**result, 'status': (
                batch.StatusEnum.FAILED if batch.final_attempts >= MAX_ANALYSIS_ATTEMPTS else batch.StatusEnum.WAITING_RETRY
            ), 'fallidas': 1}
        return {**result, 'status': batch.StatusEnum.SUCCESS}
    except Exception as exc:
        logger.exception('Falló el lote semanal %s.', batch_id)
        _pause(batch, token, exc, exhausted=True)
        return {**result, 'status': batch.StatusEnum.FAILED, 'fallidas': 1}


def retry_due_batches():
    """La recuperación respeta el orden del lote y su semana original."""
    result = {'procesadas': 0, 'analizadas': 0, 'fallidas': 0, 'omitidas': 0, 'anotaciones_creadas': 0}
    if not analyzer_enabled():
        return {**result, 'motivo_omision': 'analizador_inactivo'}
    now = timezone.now()
    ids = list(WeeklyAnalysisBatchModel.objects.filter(
        Q(status=WeeklyAnalysisBatchModel.StatusEnum.PENDING)
        | Q(status=WeeklyAnalysisBatchModel.StatusEnum.WAITING_RETRY, next_attempt_at__lte=now)
        | Q(status=WeeklyAnalysisBatchModel.StatusEnum.RUNNING, lease_until__lte=now),
    ).order_by('week_start', 'pk').values_list('pk', flat=True)[:50])
    for batch_id in ids:
        outcome = run_weekly_batch(batch_id)
        result['procesadas'] += 1
        for key in ('analizadas', 'fallidas', 'omitidas', 'anotaciones_creadas'):
            result[key] += outcome[key]
    return result
