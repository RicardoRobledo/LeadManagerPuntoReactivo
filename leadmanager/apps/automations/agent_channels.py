"""Configuración de canales reservados para la atención real de los agentes."""
from django.db import IntegrityError, transaction

from .models import AgentChannelAssignment, AgentModel
from leadmanager.apps.prospects.models import ConversationSourceEnum


class ChannelConflict(Exception):
    def __init__(self, conflicts):
        self.conflicts = conflicts
        super().__init__('Canales ocupados' if conflicts else 'La asignación cambió mientras guardabas. Inténtalo de nuevo.')


class ChannelRequired(Exception):
    pass


def _conflicts(agent_id, channels, prospecting_only=False):
    labels = dict(ConversationSourceEnum.choices)
    occupied = AgentChannelAssignment.objects.filter(
        active=True, channel__in=channels,
    ).exclude(agent_id=agent_id).select_related('agent').order_by('channel')
    if prospecting_only:
        occupied = occupied.filter(agent__category=AgentModel.CategoryEnum.PROSPECTING)
    return [{'channel': item.channel, 'label': labels.get(item.channel, item.channel), 'agent': item.agent.name} for item in occupied]


def save_agent_channels(agent_id, *, channels=None, active=None):
    attempted = []
    try:
        with transaction.atomic():
            agent = AgentModel.objects.select_for_update().get(pk=agent_id)
            selected = list(agent.channel_assignments.values_list('channel', flat=True)) if channels is None else list(channels)
            attempted = selected
            new_active = agent.is_active if active is None else active
            if new_active and not selected and agent.key != 'conversation_analyzer':
                raise ChannelRequired('Selecciona al menos un canal para activar este agente.')
            prospecting_only = active is True
            occupied = AgentChannelAssignment.objects.filter(
                channel__in=selected, active=True,
            ).exclude(agent=agent)
            if prospecting_only:
                occupied = occupied.filter(agent__category=AgentModel.CategoryEnum.PROSPECTING)
            if new_active and occupied.exists():
                raise ChannelConflict(_conflicts(agent.pk, selected, prospecting_only))
            agent.channel_assignments.all().delete()
            AgentChannelAssignment.objects.bulk_create([
                AgentChannelAssignment(agent=agent, channel=channel, active=new_active)
                for channel in selected
            ])
            if agent.is_active != new_active:
                agent.is_active = new_active
                agent.save(update_fields=['is_active', 'updated_at'])
    except IntegrityError as exc:
        raise ChannelConflict(_conflicts(agent_id, attempted, active is True)) from exc
