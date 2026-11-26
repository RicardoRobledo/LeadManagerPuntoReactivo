import secrets

from django import forms
from django.db import transaction

from .agent_configuration import (
    DEFAULT_BUSINESS_UNIT_DESCRIPTION, DEFAULT_BUSINESS_UNIT_NAME,
    PROMPT_CHOICE_FIELDS, PROMPT_TEXT_FIELDS,
    PROMPT_TOGGLE_FIELDS, validate_prompt_config,
)
from .models import AgentBusinessUnitModel, AgentModel


class BusinessUnitForm(forms.Form):
    id = forms.IntegerField(required=False, min_value=1)
    name = forms.CharField(max_length=100)
    description = forms.CharField(max_length=4000)

    def clean_name(self):
        value = self.cleaned_data['name'].upper()
        if len(value) > 100:
            raise forms.ValidationError('El nombre admite hasta 100 caracteres.')
        if value.replace('_', ' ') == 'NO DETERMINADO':
            value = DEFAULT_BUSINESS_UNIT_NAME
        return value


class AgentPromptForm(forms.Form):
    mode = forms.ChoiceField(choices=AgentModel.ModeEnum.choices, required=False)
    learning_enabled = forms.BooleanField(
        label='Aprender de conversaciones', required=False,
        widget=forms.CheckboxInput(attrs={
            'data-prompt-field': 'learning_enabled',
            'class': 'mt-0.5 h-4 w-4 shrink-0 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500',
            'aria-describedby': 'error_learning_enabled',
        }),
    )
    business_units = forms.JSONField(required=False, widget=forms.HiddenInput)
    service_flows = forms.JSONField(required=False, widget=forms.HiddenInput)
    escalation_rules = forms.JSONField(required=False, widget=forms.HiddenInput)
    reminder_schedule = forms.JSONField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, agent=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.agent = agent
        for key, (label, limit) in PROMPT_TEXT_FIELDS.items():
            attrs = {
                'data-prompt-field': key,
                'class': 'block w-full rounded-xl border border-slate-300 bg-white p-3 text-sm leading-relaxed text-slate-800 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-200',
                'aria-describedby': f'error_{key}',
            }
            widget = forms.TextInput(attrs=attrs) if key in ('name', 'enterprise_name') else forms.Textarea(attrs={**attrs, 'rows': 3 if key == 'saludo' else 4 if key in ('tone', 'flujo_conversacion') else 10})
            self.fields[key] = forms.CharField(
                label=label, max_length=limit, required=key in ('name', 'enterprise_name', 'saludo'), widget=widget,
            )
        for key, (label, choices) in PROMPT_CHOICE_FIELDS.items():
            is_slider_choice = key in ('longitud_respuesta', 'calidez')
            options = list(choices.items())
            if key == 'qualification_method':
                options = [
                    ('Primer contacto', [(name, choices[name]) for name in ('basic', 'bant', 'champ', 'faint')]),
                    ('Avanzadas · activar solo lo necesario', [
                        (name, choices[name]) for name in ('anum', 'sandler', 'gpctba_ci', 'meddic', 'meddpicc')
                    ]),
                    ('custom', choices['custom']),
                ]
            self.fields[key] = forms.ChoiceField(
                label=label, choices=options,
                widget=forms.Select(attrs={
                    'data-prompt-field': key,
                    'class': 'hidden' if is_slider_choice else 'block w-full rounded-xl border border-slate-300 bg-white p-3 text-sm text-slate-800 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-200',
                    'aria-describedby': f'error_{key}',
                }),
            )
        self.fields['custom_methodology'] = forms.CharField(
            label='Describe tu metodología', max_length=10000, required=False,
            widget=forms.Textarea(attrs={
                'data-prompt-field': 'custom_methodology',
                'rows': 8,
                'placeholder': 'Describe las etapas, el orden de preguntas y cuándo continuar o escalar.',
                'class': 'block w-full rounded-xl border border-slate-300 bg-white p-3 text-sm leading-relaxed text-slate-800 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-200',
                'aria-describedby': 'error_custom_methodology',
            }),
        )
        for key, (label, _) in PROMPT_TOGGLE_FIELDS.items():
            self.fields[key] = forms.BooleanField(
                label=label, required=False, initial=True,
                widget=forms.CheckboxInput(attrs={
                    'data-prompt-field': key,
                    'class': 'mt-0.5 h-4 w-4 shrink-0 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500',
                    'aria-describedby': f'error_{key}',
                }),
            )
        self.fields['objective_conversation'] = forms.CharField(
            label='Objetivo de la conversación', max_length=4000, required=False,
            widget=forms.Textarea(attrs={
                'data-prompt-field': 'objective_conversation',
                'rows': 4,
                'class': 'block w-full rounded-xl border border-slate-300 bg-white p-3 text-sm leading-relaxed text-slate-800 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-200',
                'aria-describedby': 'error_objective_conversation',
            }),
        )
        self.fields['agrupar_preguntas'] = forms.BooleanField(
            label='Agrupar preguntas pendientes en un solo mensaje', required=False, initial=True,
            widget=forms.CheckboxInput(attrs={
                'data-prompt-field': 'agrupar_preguntas',
                'class': 'mt-0.5 h-4 w-4 shrink-0 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500',
                'aria-describedby': 'error_agrupar_preguntas',
            }),
        )
        text_fields = list(PROMPT_TEXT_FIELDS)
        tone_index = text_fields.index('tone')
        flow_index = text_fields.index('flujo_conversacion')
        self.order_fields(['business_units', 'service_flows', *text_fields[:tone_index],
                           *PROMPT_CHOICE_FIELDS, 'custom_methodology', *PROMPT_TOGGLE_FIELDS,
                           *text_fields[tone_index:flow_index],
                           'objective_conversation', 'agrupar_preguntas', 'reminder_schedule',
                           *text_fields[flow_index:]])

    def clean_service_flows(self):
        flows = self.cleaned_data.get('service_flows')
        if not isinstance(flows, list) or len(flows) > 50:
            raise forms.ValidationError('Configura hasta 50 servicios en el flujo.')
        cleaned, names = [], set()
        for index, flow in enumerate(flows, 1):
            if not isinstance(flow, dict) or set(flow) != {'service', 'questions'}:
                raise forms.ValidationError(f'El servicio {index} tiene un formato inválido.')
            service, questions = flow['service'], flow['questions']
            if not isinstance(service, str) or not service.strip() or len(service.strip()) > 100:
                raise forms.ValidationError(f'El servicio {index} necesita un nombre de hasta 100 caracteres.')
            service = service.strip()
            if service.casefold() in names:
                raise forms.ValidationError(f'El servicio {service} está repetido.')
            if not isinstance(questions, list) or not 1 <= len(questions) <= 20:
                raise forms.ValidationError(f'{service} debe tener entre 1 y 20 preguntas.')
            if any(not isinstance(question, str) or not question.strip() or len(question.strip()) > 500
                   for question in questions):
                raise forms.ValidationError(f'Las preguntas de {service} deben tener hasta 500 caracteres y no estar vacías.')
            names.add(service.casefold())
            cleaned.append({'service': service, 'questions': [question.strip() for question in questions]})
        return cleaned

    def clean_mode(self):
        mode = self.cleaned_data.get('mode') or (self.agent.mode if self.agent else AgentModel.ModeEnum.PRODUCTION)
        if mode == AgentModel.ModeEnum.SHADOW_TESTING and self.agent:
            reason = self.agent.get_shadow_mode_block_reason()
            if reason:
                raise forms.ValidationError(reason)
        return mode

    def clean_escalation_rules(self):
        rules = self.cleaned_data.get('escalation_rules')
        if not isinstance(rules, list) or len(rules) > 30:
            raise forms.ValidationError('Configura hasta 30 reglas de escalamiento.')
        cleaned = []
        for index, rule in enumerate(rules, 1):
            if not isinstance(rule, str) or not rule.strip() or len(rule.strip()) > 2000:
                raise forms.ValidationError(f'La regla {index} debe tener texto de hasta 2000 caracteres.')
            cleaned.append(rule.strip())
        return cleaned

    def clean_reminder_schedule(self):
        schedule = self.cleaned_data.get('reminder_schedule')
        expected = [('none', 'first'), ('first', 'second'), ('second', 'third')]
        if not isinstance(schedule, list) or len(schedule) > 3:
            raise forms.ValidationError('Configura hasta 3 tiempos de recordatorio.')
        cleaned = []
        disabled_stage = False
        for index, item in enumerate(schedule):
            if (not isinstance(item, dict) or set(item) != {'from', 'to', 'amount', 'unit', 'enabled'}
                    or index >= len(expected)
                    or (item.get('from'), item.get('to')) != expected[index]):
                raise forms.ValidationError('Los pasos deben avanzar de Nada a FIRST, FIRST a SECOND y SECOND a THIRD.')
            amount, unit = item.get('amount'), item.get('unit')
            if isinstance(amount, bool) or not isinstance(amount, int) or not 1 <= amount <= 365:
                raise forms.ValidationError(f'El intervalo {index + 1} debe estar entre 1 y 365.')
            if unit not in ('hours', 'days') or type(item.get('enabled')) is not bool:
                raise forms.ValidationError(f'Elige horas o días en el intervalo {index + 1}.')
            if disabled_stage and item['enabled']:
                raise forms.ValidationError('Habilita los recordatorios en orden, sin saltar etapas.')
            disabled_stage = disabled_stage or not item['enabled']
            cleaned.append({**item, 'amount': amount})
        return cleaned

    def clean_business_units(self):
        values = self.cleaned_data.get('business_units')
        if not isinstance(values, list) or len(values) > 51:
            raise forms.ValidationError('Introduce hasta 50 unidades y la unidad predeterminada.')
        cleaned, names, identifiers = [], set(), set()
        owned = {unit['pk']: unit for unit in self.agent.business_units.values('pk', 'name', 'description')} if self.agent else {}
        for index, item in enumerate(values, 1):
            if not isinstance(item, dict):
                raise forms.ValidationError(f'La unidad {index} no tiene un formato válido.')
            unit = BusinessUnitForm(item)
            if not unit.is_valid():
                errors = ' '.join(str(error) for errors in unit.errors.values() for error in errors)
                raise forms.ValidationError(f'Unidad {index}: {errors}')
            value = unit.cleaned_data
            if value['name'] in names:
                raise forms.ValidationError(f'La unidad {value["name"]} está repetida.')
            if value['id'] is not None and (value['id'] not in owned or value['id'] in identifiers):
                raise forms.ValidationError('Una unidad no pertenece a este agente o está repetida.')
            existing = owned.get(value['id'], {})
            if existing.get('name') == DEFAULT_BUSINESS_UNIT_NAME and (
                    value['name'] != DEFAULT_BUSINESS_UNIT_NAME or value['description'] != existing['description']):
                raise forms.ValidationError('La unidad predeterminada NO_DETERMINADO no se puede modificar.')
            if value['name'] == DEFAULT_BUSINESS_UNIT_NAME and existing.get('name') != DEFAULT_BUSINESS_UNIT_NAME:
                raise forms.ValidationError('NO_DETERMINADO ya existe como unidad predeterminada.')
            names.add(value['name'])
            if value['id'] is not None:
                identifiers.add(value['id'])
            cleaned.append(value)
        if len(names - {DEFAULT_BUSINESS_UNIT_NAME}) > 50:
            raise forms.ValidationError('Puedes agregar hasta 50 unidades de negocio.')
        return cleaned

    def clean(self):
        values = super().clean()
        if values.get('qualification_method') == 'custom' and not values.get('custom_methodology', '').strip():
            self.add_error('custom_methodology', 'Describe tu metodología antes de guardarla.')
        if set(self.fields) - set(self.data) - set(PROMPT_TOGGLE_FIELDS) - {'agrupar_preguntas', 'custom_methodology', 'learning_enabled', 'mode'}:
            raise forms.ValidationError('Envía todos los campos de la configuración antes de guardar.')
        return values

    @transaction.atomic
    def save(self):
        agent = self.agent
        units = self.cleaned_data['business_units']
        AgentBusinessUnitModel.objects.get_or_create(
            agent=agent, name=DEFAULT_BUSINESS_UNIT_NAME,
            defaults={'description': DEFAULT_BUSINESS_UNIT_DESCRIPTION},
        )
        retained = [unit['id'] for unit in units if unit['id'] is not None]
        agent.business_units.exclude(name=DEFAULT_BUSINESS_UNIT_NAME).exclude(pk__in=retained).delete()
        # Permite intercambiar nombres entre dos filas sin colisiones transitorias.
        for unit in units:
            if unit['id'] is not None and unit['name'] != DEFAULT_BUSINESS_UNIT_NAME:
                agent.business_units.filter(pk=unit['id']).update(name='__EDIT_' + secrets.token_hex(16).upper())
        for unit in units:
            if unit['name'] == DEFAULT_BUSINESS_UNIT_NAME:
                continue
            if unit['id'] is None:
                AgentBusinessUnitModel.objects.create(agent=agent, name=unit['name'], description=unit['description'])
            else:
                record = agent.business_units.get(pk=unit['id'])
                record.name, record.description = unit['name'], unit['description']
                record.save()
        config = {key: self.cleaned_data[key] for key in (
            *PROMPT_TEXT_FIELDS, *PROMPT_CHOICE_FIELDS, *PROMPT_TOGGLE_FIELDS,
            'objective_conversation', 'agrupar_preguntas', 'service_flows', 'escalation_rules', 'reminder_schedule',
        )}
        config['custom_methodology'] = self.cleaned_data['custom_methodology']
        config['business_units'] = list(agent.business_units.values_list('pk', flat=True))
        validate_prompt_config(config)
        agent.prompt = config
        agent.name = config['name']
        agent.learning_enabled = self.cleaned_data['learning_enabled']
        agent.mode = self.cleaned_data['mode']
        agent.save(update_fields=['prompt', 'name', 'learning_enabled', 'mode', 'updated_at'])
        return agent
