"""Validación de las instrucciones editables de los agentes."""

from string import Template

from django.core.exceptions import ValidationError


MAX_AGENT_PROMPT_LENGTH = 60000
RESPONSE_PROMPT_VARIABLES = frozenset({'prefilled_data', 'search_results'})


def validate_response_prompt(value):
    if not isinstance(value, str) or not value.strip():
        raise ValidationError('El prompt no puede estar vacío.')
    if len(value) > MAX_AGENT_PROMPT_LENGTH:
        raise ValidationError(
            'El prompt admite un máximo de %(limit)s caracteres.',
            params={'limit': MAX_AGENT_PROMPT_LENGTH},
        )
    template = Template(value)
    if not template.is_valid():
        raise ValidationError(
            'Hay una variable incompleta. Usa ${prefilled_data} y ${search_results}; '
            'para escribir un signo de dólar literal usa $$.',
        )
    variables = set(template.get_identifiers())
    if variables != RESPONSE_PROMPT_VARIABLES:
        raise ValidationError(
            'Conserva ${prefilled_data} y ${search_results}, sin agregar otras variables.',
        )
