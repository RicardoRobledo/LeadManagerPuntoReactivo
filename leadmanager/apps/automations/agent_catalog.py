"""Seed del catálogo y lectura de los prompts vigentes."""

from pathlib import Path

from django.apps import apps as django_apps
from .models import AgentModel

from .agent_configuration import (
    DEFAULT_BUSINESS_UNIT_DESCRIPTION, DEFAULT_BUSINESS_UNIT_NAME,
    DEFAULT_ROLE_INFORMATION, default_prompt_config,
    render_response_prompt, sofia_defaults, with_prompt_defaults,
)


PROMPTS_DIR = Path(__file__).resolve().parent / 'agents' / 'prospector_agent' / 'prompts'
ANALYZER_PROMPT = Path(__file__).resolve().parent / 'agents' / 'conversation_analyzer_agent' / 'prompts' / 'analyze_conversation_prompt.txt'


def analyzer_prompt_defaults():
    """Configuración compatible con el modelo actual, sin reglas de venta."""
    config = default_prompt_config()
    config.update({
        'name': 'María',
        'enterprise_name': 'Uso interno',
        'saludo': 'Análisis interno de conversaciones.',
        'role_information': ANALYZER_PROMPT.read_text(encoding='utf-8'),
        'objective_conversation': 'Proponer aprendizajes reutilizables para revisión humana.',
        'cuando_atender': '',
        'cuando_descartar': '',
        'escalation_rules': [],
        'reminder_schedule': [],
    })
    return config


def seed_agents(apps=None, using='default'):
    """Crea los agentes iniciales; conserva toda configuración ya guardada."""
    Agent = (apps or django_apps).get_model('automations', 'AgentModel')
    current = 'prompt' in {field.name for field in Agent._meta.fields}
    prompt_defaults = {'prompt': sofia_defaults()['prompt']} if current else {
        'position': 0,
        'prompt_web': (PROMPTS_DIR / 'generate_prospect_response_web_prompt.txt').read_text(encoding='utf-8'),
        'prompt_general': (PROMPTS_DIR / 'generate_prospect_response_prompt.txt').read_text(encoding='utf-8'),
    }
    agent, created = Agent.objects.using(using).get_or_create(
        key='prospector',
        defaults={
            'name': 'Sofía',
            'role': 'Agente de prospección comercial',
            'description': (
                'Atiende consultas, identifica necesidades y califica prospectos '
                'para canalizarlos con el especialista adecuado.'
            ),
            'category': AgentModel.CategoryEnum.PROSPECTING,
            'avatar': '',
            'channels': ['chatweb', 'Correo', 'Formulario'],
            **prompt_defaults,
        },
    )
    if current:
        if created and apps is None:
            Assignment = django_apps.get_model('automations', 'AgentChannelAssignment')
            for channel in ('web', 'email', 'form'):
                Assignment.objects.using(using).create(agent=agent, channel=channel, active=agent.is_active)
        Unit = (apps or django_apps).get_model('automations', 'AgentBusinessUnitModel')
        if created:
            for unit in sofia_defaults()['business_units']:
                Unit.objects.using(using).get_or_create(agent_id=agent.pk, name=unit['name'], defaults={'description': unit['description']})
        for record in Agent.objects.using(using).filter(key='prospector').iterator():
            Unit.objects.using(using).get_or_create(
                agent_id=record.pk, name=DEFAULT_BUSINESS_UNIT_NAME,
                defaults={'description': DEFAULT_BUSINESS_UNIT_DESCRIPTION},
            )
            config = with_prompt_defaults(record.prompt)
            config.setdefault('role_information', DEFAULT_ROLE_INFORMATION)
            config['business_units'] = list(Unit.objects.using(using).filter(agent_id=record.pk).values_list('pk', flat=True))
            if config != record.prompt:
                Agent.objects.using(using).filter(pk=record.pk).update(prompt=config)
        agent.refresh_from_db(using=using)
    results = [(agent, created)]
    if current:
        analyzer, analyzer_created = Agent.objects.using(using).get_or_create(
            key='conversation_analyzer',
            defaults={
                'name': 'María',
                'role': 'Agente de análisis y aprendizaje de conversaciones',
                'description': (
                    'Revisa conversaciones y propone anotaciones de aprendizaje '
                    'para que sean revisadas antes de utilizarlas.'
                ),
                'category': AgentModel.CategoryEnum.ANALYSIS,
                'avatar': '',
                'channels': [],
                'is_active': True,
                'prompt': analyzer_prompt_defaults(),
            },
        )
        results.append((analyzer, analyzer_created))
    return results


def get_agent_configuration(agent_key):
    """Devuelve la configuración y las unidades vigentes de un agente."""
    Agent = django_apps.get_model('automations', 'AgentModel')
    agent = Agent.objects.filter(key=agent_key).first()
    if agent is None:
        defaults = sofia_defaults()
        return defaults['prompt'], defaults['business_units']
    return with_prompt_defaults(agent.prompt), list(agent.business_units.values('id', 'name', 'description'))


def get_response_prompt(agent_key, source, template, **conversation_values):
    """Forma el prompt desde el archivo base y los valores actuales de la tabla."""
    configuration, units = get_agent_configuration(agent_key)
    return render_response_prompt(template, configuration, units, **conversation_values)
