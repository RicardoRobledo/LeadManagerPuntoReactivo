from .agent_configuration import DEFAULT_REMINDER_SCHEDULE


def get_reminder_schedule():
    """Return the reminder intervals configured on the prospector agent."""
    from .models import AgentModel

    agent = AgentModel.objects.filter(key='prospector').only('prompt').first()
    schedule = (agent.prompt or {}).get('reminder_schedule') if agent else None
    if not isinstance(schedule, list):
        return [dict(item) for item in DEFAULT_REMINDER_SCHEDULE]
    return [
        {**item, 'enabled': item.get('enabled', True)} if isinstance(item, dict) else item
        for item in schedule
    ]


def interval_delta(item):
    from datetime import timedelta

    amount = int(item.get('amount', 48))
    unit = item.get('unit', 'hours')
    return timedelta(days=amount) if unit == 'days' else timedelta(hours=amount)
