"""Selección de agentes de prospección según el canal y su participación."""

from leadmanager.apps.automations.models import AgentModel


class ProductionAgentUnavailable(ValueError):
    pass


def get_agents_for_channel(source):

    production = AgentModel.objects.filter(
        category=AgentModel.CategoryEnum.PROSPECTING,
        mode=AgentModel.ModeEnum.PRODUCTION,
        is_active=True,
        channel_assignments__channel=source,
        channel_assignments__active=True,
    ).first()

    if production is None:
        raise ProductionAgentUnavailable(
            f'No hay un agente de producción activo para el canal "{source}".'
        )

    shadows = AgentModel.objects.filter(
        category=AgentModel.CategoryEnum.PROSPECTING,
        mode=AgentModel.ModeEnum.SHADOW_TESTING,
        channel_assignments__channel=source,
    ).distinct()

    return production, list(shadows)
