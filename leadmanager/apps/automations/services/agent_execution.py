"""Ejecución real y preparación de copias independientes para pruebas."""

import json
import logging

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone

from leadmanager.apps.prospects.models import ChatHistoryModel

from ..agent_catalog import get_agent_configuration
from ..models import AgentEventModel, AgentModel, AgentRunModel
from .agent_selection import get_agents_for_channel


logger = logging.getLogger(__name__)


def run_production_agent(ctx):
    from ..agents.prospector_agent.prospector import build_prospector_agent

    production, shadows = get_agents_for_channel(ctx['source'])
    ctx['_production_agent_key'] = production.key
    ctx['_shadow_agents'] = shadows
    ctx['_agent_configuration'], ctx['_agent_units'] = get_agent_configuration(production.key)
    return build_prospector_agent(production).run(ctx)


def _publish_shadow_run(run_id):
    from ..tasks import process_shadow_run

    try:
        process_shadow_run.apply_async(
            args=[run_id], queue=settings.CELERY_SHADOW_QUEUE, retry=False,
        )
    except Exception as exc:
        logger.exception('No se pudo encolar la prueba %s; producción continúa.', run_id)
        AgentRunModel.objects.filter(pk=run_id, status=AgentRunModel.StatusEnum.QUEUED).update(
            status=AgentRunModel.StatusEnum.FAILED, finished_at=timezone.now(),
            result={'error': 'No se pudo encolar la prueba.', 'detail': str(exc)},
        )
        AgentEventModel.objects.create(
            run_id=run_id, level=AgentEventModel.LevelEnum.WARNING,
            message='No se pudo encolar la prueba; la atención real continúa.',
        )


def capture_shadow_input_step(agent, ctx):
    """Copia el turno antes de la respuesta real; no espera las pruebas."""
    ctx.setdefault('_shadow_run_ids', [])
    if not ctx.get('_shadow_agents'):
        return
    try:
        source = ctx['source']
        parent = ctx['_agent_run']
        conversation = ctx.get('conversation')
        snapshot = {
            'source': source,
            source: ctx.get(source, {}),
            'person': ctx.get('person', {}),
            'history': ctx.get('history', []),
            'source_identifier': ctx.get('source_identifier'),
            'conversation_id': conversation.pk if conversation else None,
            # El mensaje real del prospecto en este turno: liga la respuesta
            # de pruebas con la de producción para compararlas.
            'source_message_id': (
                ChatHistoryModel.objects
                .filter(conversation=conversation, message__role='user')
                .order_by('-id').values_list('id', flat=True).first()
            ) if conversation else None,
            '_production_run_id': parent.pk,
        }
        for shadow in ctx['_shadow_agents']:
            try:
                configuration, units = get_agent_configuration(shadow.key)
                frozen = json.loads(json.dumps({
                    **snapshot, '_agent_configuration': configuration, '_agent_units': units,
                }, cls=DjangoJSONEncoder))
                shadow_run, created = AgentRunModel.objects.get_or_create(
                    production_run=parent, agent_name=shadow.key,
                    mode=AgentModel.ModeEnum.SHADOW_TESTING,
                    defaults={
                        'source': source, 'conversation': conversation,
                        'status': AgentRunModel.StatusEnum.QUEUED,
                        'input_snapshot': frozen,
                    },
                )
                ctx['_shadow_run_ids'].append(shadow_run.pk)
                if created:
                    transaction.on_commit(
                        lambda run_id=shadow_run.pk: _publish_shadow_run(run_id), robust=True,
                    )
            except Exception:
                logger.exception('No se pudo preparar la prueba de %s; producción continúa.', shadow.key)
    except Exception:
        logger.exception('No se pudieron preparar las pruebas; producción continúa.')
