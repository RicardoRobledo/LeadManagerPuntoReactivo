"""Valores editables que completan las plantillas de los agentes."""

import json
import hashlib
import re
from pathlib import Path
from string import Template

from django.core.exceptions import ValidationError


DEFAULT_BUSINESS_UNIT_NAME = 'NO_DETERMINADO'
DEFAULT_BUSINESS_UNIT_DESCRIPTION = 'Se utiliza cuando aún no se ha identificado una unidad de negocio para el prospecto.'
DEFAULT_ROLE_INFORMATION = 'Debes identificarte por tu nombre como asistente comercial, lideres en transformación digital para empresas, desde el primer mensaje.'
DEFAULT_GREETING = 'Hola, soy [bot], asistente virtual de [empresa].'
DEFAULT_REMINDER_SCHEDULE = [
    {'from': 'none', 'to': 'first', 'amount': 48, 'unit': 'hours', 'enabled': True},
    {'from': 'first', 'to': 'second', 'amount': 1, 'unit': 'days', 'enabled': True},
    {'from': 'second', 'to': 'third', 'amount': 3, 'unit': 'days', 'enabled': True},
]

QUALIFICATION_METHODOLOGIES = {
    'basic': {
        'label': 'Básica · encaje, necesidad y contacto',
        'name': 'Básica',
        'order': 'necesidad → servicio → contacto',
        'description': 'Para empezar con poca fricción: identificar encaje, necesidad y una forma de continuar.',
    },
    'bant': {
        'label': 'BANT · presupuesto, autoridad, necesidad y plazo',
        'name': 'BANT',
        'order': 'necesidad → presupuesto → rol → plazo → contacto',
        'description': 'Presupuesto, autoridad, necesidad y plazo. No exige presupuesto aprobado ni que la persona firme la compra, salvo que tú lo establezcas.',
    },
    'champ': {
        'label': 'CHAMP · problema y prioridad primero',
        'name': 'CHAMP',
        'order': 'necesidad → rol → presupuesto → prioridad → contacto',
        'description': 'Comienza por el problema y su prioridad. Útil cuando todavía hay que aclarar el proyecto antes de hablar de inversión.',
    },
    'faint': {
        'label': 'FAINT · capacidad de invertir e interés',
        'name': 'FAINT',
        'order': 'necesidad → interés → capacidad → rol → plazo → contacto',
        'description': 'Distingue tener fondos disponibles de contar con un presupuesto ya asignado. Capacidad, autoridad, interés, necesidad y plazo.',
    },
    'anum': {
        'label': 'ANUM · autoridad, necesidad, urgencia y dinero',
        'name': 'ANUM',
        'order': 'rol → necesidad → urgencia → presupuesto → contacto',
        'description': 'Prioriza quién participa en la decisión. En este diseño, un asistente o investigador puede continuar; no se rechaza por su cargo.',
    },
    'sandler': {
        'label': 'Sandler · problema, inversión y decisión',
        'name': 'Sandler',
        'order': 'necesidad → presupuesto → decisión → contacto',
        'description': 'Adaptación de la fase de calificación: problema, inversión y decisión. No intenta ejecutar todo el sistema Sandler en un chat.',
    },
    'gpctba_ci': {
        'label': 'GPCTBA/C&I · objetivos y planes',
        'name': 'GPCTBA/C&I',
        'order': 'objetivo → plan → necesidad → plazo → presupuesto → rol → consecuencia → beneficio → contacto',
        'description': 'Objetivos, planes, desafíos, plazo, presupuesto, autoridad, consecuencias de no actuar y beneficios de actuar. Profundo: normalmente conviene dejar varias dimensiones a ventas.',
    },
    'meddic': {
        'label': 'MEDDIC · compra B2B compleja',
        'name': 'MEDDIC',
        'order': 'necesidad → métrica → comprador → criterios → proceso → sponsor → contacto',
        'description': 'Compra B2B compleja: métricas, comprador económico, criterios, proceso, problema y promotor interno. Aquí se ofrece como plantilla avanzada.',
    },
    'meddpicc': {
        'label': 'MEDDPICC · compras y competencia',
        'name': 'MEDDPICC',
        'order': 'necesidad → métrica → comprador → criterios → proceso → sponsor → compras → competencia → contacto',
        'description': 'Extiende MEDDIC con contratación y competencia. Recoge lo que ya se sabe; completar toda la ficha rara vez es una buena meta de primer contacto.',
    },
    'custom': {
        'label': 'Mi metodología',
        'name': 'Mi metodología',
        'order': '',
        'description': 'Tu propio proceso: importa datos estructurados o define los requisitos y las preguntas en «Qué califica».',
    },
}


PROMPT_TEXT_FIELDS = {
    'name': ('Nombre del agente', 100),
    'enterprise_name': ('Nombre de la empresa', 150),
    'enterprise_information': ('Información de la empresa', 10000),
    'role_information': ('Información del rol', 10000),
    'saludo': ('Su saludo', 1000),
    'tone': ('Otras indicaciones de comunicación (opcional)', 10000),
    'flujo_conversacion': ('Otras indicaciones del flujo (opcional)', 20000),
    'cuando_atender': ('Cuándo atender', 12000),
    'cuando_descartar': ('Cuándo descartar', 12000),
}

DEFAULT_WHEN_TO_ATTEND = (
    'Atiende a clientes potenciales, clientes actuales o anteriores, empresas interesadas '
    'y personas que podrían convertirse en compradores. También atiende a quienes aún no '
    'tienen una intención clara, pero necesitan una pregunta de calificación.'
)
DEFAULT_WHEN_TO_DISCARD = (
    'Descarta spam, mensajes automatizados irrelevantes, intentos de fraude y solicitudes '
    'ajenas al negocio. Descarta también a vendedores, proveedores, candidatos laborales, '
    'socios, estudiantes, medios, soporte y reclamaciones, así como a quien intenta ofrecer '
    'servicios en lugar de comprar o conocer nuestros productos o servicios. Si el propósito '
    'no está claro, pregunta primero; no descartes por falta de información. Al descartar, '
    'usa semáforo rojo o gris según el caso, despídete y no escales a un humano por defecto.'
)
DEFAULT_ESCALATION_RULES = [
    'Si el prospecto quiere comprar y no fue descartado, escala cuando solicite hablar con alguien.',
    'Escala si detectas una queja, molestia o frustración en el tono.',
    'Escala si la consulta sale del alcance comercial, como soporte técnico a un cliente activo o temas legales o de facturación.',
    'Escala si revela ser una cuenta de alto valor, como una cadena con muchas sucursales o una licitación de gobierno.',
    'Si después de 2 intercambios no hay claridad suficiente para calificar o perfilar, escala sin forzar una etiqueta poco confiable.',
    'Escala si solicita explícitamente un precio final, un contrato o una fecha de entrega comprometida.',
    'Si el prospecto ya fue prospectado o escalado, no vuelvas a escalar hasta que seleccione algo distinto.',
    'Escala de inmediato si la marca es reconocida ampliamente fuera de su industria; no esperes a completar las preguntas.',
    'Empresa reconocida significa que la marca es identificable de inmediato por personas fuera de su industria. No basta con operar a nivel nacional, tener muchas sucursales o plantas, llevar años en el mercado o ser líder de su nicho. Ante la duda, no la trates como reconocida y continúa con la calificación BANT.',
    'Si no hay correo o usa un correo personal como Gmail u Outlook, no escales por ese motivo; no comentes el tipo de correo, agradece su interés y continúa el flujo.',
    'La regla de marca reconocida prevalece sobre el correo: escala aunque no tenga un correo organizacional.',
]

PROMPT_CHOICE_FIELDS = {
    'trato': ('Trato', {
        'tu': 'De tú',
        'usted': 'De usted',
    }),
    'estilo_comercial': ('Estilo comercial', {
        'directo_cordial': 'Directo y cordial',
        'consultivo': 'Consultivo',
        'formal': 'Formal',
    }),
    'qualification_method': ('Plantilla de calificación', {
        key: methodology['label'] for key, methodology in QUALIFICATION_METHODOLOGIES.items()
    }),
    'longitud_respuesta': ('Extensión de respuesta', {
        'breve': 'Breve',
        'equilibrado': 'Equilibrado',
        'explicativo': 'Explicativo',
    }),
    'calidez': ('Calidez', {
        'profesional': 'Profesional',
        'equilibrada': 'Equilibrada',
        'calida': 'Cálida',
    }),
}

PROMPT_CHOICE_DEFAULTS = {
    'trato': 'tu',
    'estilo_comercial': 'directo_cordial',
    'qualification_method': 'basic',
    'longitud_respuesta': 'breve',
    'calidez': 'equilibrada',
}

PROMPT_TOGGLE_FIELDS = {
    'evitar_tecnicismos': ('Evitar tecnicismos innecesarios', 'Evita tecnicismos innecesarios.'),
    'usar_nombre_prospecto': ('Usar el nombre del prospecto con moderación',
                              'Usa el nombre del prospecto cuando lo conozcas, sin abusar de él.'),
}
PROMPT_TOGGLE_DEFAULTS = {key: True for key in PROMPT_TOGGLE_FIELDS}

PROMPT_FLOW_TEXT_FIELDS = {
    'preguntas_servicios': ('Preguntas para otros servicios', 4000),
    'preguntas_kioskos': ('Preguntas para Kioskos', 4000),
}
PROMPT_FLOW_QUESTION_DEFAULTS = {
    'preguntas_servicios': (
        'Qué necesita y para qué.\n'
        'Giro de la empresa.\n'
        'Tamaño de la empresa.\n'
        'Urgencia, fechas o plazos.'
    ),
    'preguntas_kioskos': (
        'Cuántos kioscos contempla el proyecto.\n'
        'Qué componentes necesitan.\n'
        'Cuándo estiman decidir la compra.\n'
        'Si cuentan con presupuesto asignado.'
    ),
}
DEFAULT_CONVERSATION_OBJECTIVE = (
    'Comprender la necesidad del prospecto, confirmar la viabilidad comercial '
    'y detectar su sensibilidad a pérdidas, ganancias e incertidumbre para definir el siguiente paso.'
)
DEFAULT_SERVICE_FLOWS = [
    {'service': 'Otros servicios', 'questions': [
        '¿Qué servicio necesita y para qué?',
        '¿Cuál es el giro de la empresa?',
        '¿Cuál es el tamaño de la empresa?',
        '¿Qué urgencia o plazo tiene el proyecto?',
    ]},
    {'service': 'Kioskos digitales', 'questions': [
        '¿Cuántos kioscos contempla el proyecto?',
        '¿Qué componentes se necesitan?',
        '¿Cuándo se tomaría la decisión de compra?',
        '¿Hay presupuesto asignado?',
    ]},
]
LEGACY_DEFAULT_FLOW_HASH = 'de2152241f74f94829674e0896be36a010ed5ba393297e0abd1db14a2995176d'


def is_legacy_default_flow(value):
    return isinstance(value, str) and hashlib.sha256(
        '\n'.join(line.rstrip() for line in value.replace(
            '\r\n', '\n').strip().splitlines()).encode('utf-8')
    ).hexdigest() == LEGACY_DEFAULT_FLOW_HASH


def _questions_from_text(value, key):
    if value == PROMPT_FLOW_QUESTION_DEFAULTS[key]:
        return list(DEFAULT_SERVICE_FLOWS[0 if key == 'preguntas_servicios' else 1]['questions'])
    return [line.strip().lstrip('-• ').strip() for line in value.splitlines() if line.strip()]


LEGACY_DEFAULT_TONE = (
    '- Cercano, profesional y directo — como escribiría un consultor mexicano con buena educación, no un call center ni un vendedor agresivo.\r\n'
    '- Sin tecnicismos innecesarios. Adapta el registro (más formal o más relajado) al que use el propio prospecto.\r\n'
    '- Usa el nombre del prospecto cuando lo conozcas, sin abusar de él.\r\n'
    '- Responder breve.'
)
DEFAULT_ADDITIONAL_GUIDANCE = (
    '- Evita tecnicismos innecesarios.\r\n'
    '- Usa el nombre del prospecto cuando lo conozcas, sin abusar de él.\r\n'
    '- Responde de forma breve.'
)

TRATO_INSTRUCTIONS = {
    'tu': 'Dirígete al prospecto de tú de forma consistente.',
    'usted': 'Dirígete al prospecto de usted de forma consistente.',
}
ESTILO_COMERCIAL_INSTRUCTIONS = {
    'directo_cordial': 'Usa un estilo comercial directo y cordial: ve al punto con amabilidad.',
    'consultivo': 'Usa un estilo comercial consultivo: comprende la necesidad antes de proponer el siguiente paso.',
    'formal': 'Usa un estilo comercial formal: mantén un lenguaje sobrio, respetuoso y profesional.',
}
LONGITUD_RESPUESTA_INSTRUCTIONS = {
    'breve': 'Responde de forma breve y prioriza la información indispensable.',
    'equilibrado': 'Da el contexto necesario con una extensión equilibrada.',
    'explicativo': 'Explica con detalle los puntos relevantes y los pasos, sin repetir información.',
}
CALIDEZ_INSTRUCTIONS = {
    'profesional': 'Mantén un trato profesional y correcto, sin familiaridad.',
    'equilibrada': 'Usa un trato cordial y equilibrado, cercano sin ser demasiado informal.',
    'calida': 'Usa un trato cálido, amable y cercano, sin perder profesionalismo.',
}


def with_prompt_defaults(config):
    active_toggle_defaults = {
        key: PROMPT_TOGGLE_DEFAULTS[key] for key in PROMPT_TOGGLE_FIELDS}
    normalized = {**PROMPT_CHOICE_DEFAULTS, **active_toggle_defaults, **config}
    normalized.setdefault('custom_methodology', '')
    normalized.pop('terminar_con_pregunta', None)
    legacy_brevity = normalized.pop('responder_breve', None)
    if 'longitud_respuesta' not in config:
        normalized['longitud_respuesta'] = 'equilibrado' if legacy_brevity is False else 'breve'
    normalized.setdefault('saludo', DEFAULT_GREETING)
    if isinstance(normalized['saludo'], str):
        normalized['saludo'] = normalized['saludo'].replace(
            '{bot}', '[bot]').replace('{empresa}', '[empresa]')
    normalized.setdefault('cuando_atender', DEFAULT_WHEN_TO_ATTEND)
    normalized.setdefault('cuando_descartar', DEFAULT_WHEN_TO_DISCARD)
    if 'escalation_rules' not in normalized:
        legacy_rules = normalized.get('reglas_escalamiento', '')
        if isinstance(legacy_rules, str) and legacy_rules.strip():
            normalized['escalation_rules'] = [
                re.sub(r'^\s*(?:[-*•]|\d+[.)])\s*', '', line).strip()
                for line in legacy_rules.splitlines() if line.strip()
            ]
        else:
            normalized['escalation_rules'] = list(DEFAULT_ESCALATION_RULES)
    normalized.pop('reglas_escalamiento', None)
    tone = normalized.get('tone')
    if isinstance(tone, str) and tone.replace('\r\n', '\n') in (
        LEGACY_DEFAULT_TONE.replace('\r\n', '\n'),
        DEFAULT_ADDITIONAL_GUIDANCE.replace('\r\n', '\n'),
    ):
        normalized['tone'] = ''
    if is_legacy_default_flow(normalized.get('flujo_conversacion')):
        normalized['flujo_conversacion'] = ''
        for key, value in PROMPT_FLOW_QUESTION_DEFAULTS.items():
            normalized.setdefault(key, value)
    if 'service_flows' not in normalized:
        flows = []
        for key, service in (('preguntas_servicios', 'Otros servicios'),
                             ('preguntas_kioskos', 'Kioskos digitales')):
            questions = _questions_from_text(normalized.get(key, ''), key)
            if questions:
                flows.append({'service': service, 'questions': questions})
        normalized['service_flows'] = flows
    for key in PROMPT_FLOW_TEXT_FIELDS:
        normalized.pop(key, None)
    normalized.setdefault('objective_conversation',
                          DEFAULT_CONVERSATION_OBJECTIVE if normalized['service_flows'] else '')
    normalized.setdefault('agrupar_preguntas', True)
    normalized.setdefault('reminder_schedule', [dict(
        item) for item in DEFAULT_REMINDER_SCHEDULE])
    if isinstance(normalized['reminder_schedule'], list):
        normalized['reminder_schedule'] = [
            {**item, 'enabled': item.get('enabled', True)
             } if isinstance(item, dict) else item
            for item in normalized['reminder_schedule']
        ]
    return normalized


def default_prompt_config():
    active_toggle_defaults = {
        key: PROMPT_TOGGLE_DEFAULTS[key] for key in PROMPT_TOGGLE_FIELDS}
    return {**{key: '' for key in PROMPT_TEXT_FIELDS},
            'cuando_atender': DEFAULT_WHEN_TO_ATTEND,
            'cuando_descartar': DEFAULT_WHEN_TO_DISCARD,
            'escalation_rules': list(DEFAULT_ESCALATION_RULES),
            **PROMPT_CHOICE_DEFAULTS,
            **active_toggle_defaults, 'objective_conversation': '',
            'service_flows': [], 'agrupar_preguntas': True,
            'custom_methodology': '',
            'reminder_schedule': [dict(item) for item in DEFAULT_REMINDER_SCHEDULE],
            'role_information': DEFAULT_ROLE_INFORMATION, 'saludo': DEFAULT_GREETING,
            'business_units': []}


def render_greeting(configuration):
    """Sustituye únicamente los dos marcadores públicos del saludo."""
    values = {'bot': configuration['name'],
              'empresa': configuration['enterprise_name']}
    return re.sub(r'[\[{](bot|empresa)[\]}]', lambda match: values[match.group(1)], configuration['saludo'])


def sofia_defaults():
    defaults = json.loads(
        (Path(__file__).parent / 'agent_prompt_defaults.json').read_text(encoding='utf-8'))
    defaults['prompt'] = with_prompt_defaults(defaults['prompt'])
    return defaults


def validate_prompt_config(value):
    if not isinstance(value, dict) or set(value) != set(default_prompt_config()):
        raise ValidationError(
            'La configuración debe contener todos los campos del prompt.')
    for key, (label, limit) in PROMPT_TEXT_FIELDS.items():
        if not isinstance(value[key], str) or len(value[key]) > limit:
            raise ValidationError(
                f'{label} debe ser texto de hasta {limit} caracteres.')
    if not isinstance(value['objective_conversation'], str) or len(value['objective_conversation']) > 4000:
        raise ValidationError(
            'El objetivo debe ser texto de hasta 4000 caracteres.')
    flows = value['service_flows']
    if not isinstance(flows, list) or len(flows) > 50:
        raise ValidationError('Configura hasta 50 servicios en el flujo.')
    names = set()
    for flow in flows:
        if not isinstance(flow, dict) or set(flow) != {'service', 'questions'}:
            raise ValidationError(
                'Cada servicio debe tener nombre y preguntas.')
        service, questions = flow['service'], flow['questions']
        if (not isinstance(service, str) or not service.strip() or len(service.strip()) > 100
                or service.strip().casefold() in names):
            raise ValidationError(
                'Los servicios deben tener nombres únicos de hasta 100 caracteres.')
        names.add(service.strip().casefold())
        if (not isinstance(questions, list) or not 1 <= len(questions) <= 20
                or any(not isinstance(question, str) or not question.strip() or len(question.strip()) > 500
                       for question in questions)):
            raise ValidationError(
                'Cada servicio debe tener entre 1 y 20 preguntas de hasta 500 caracteres.')
    rules = value['escalation_rules']
    if (not isinstance(rules, list) or len(rules) > 30
            or any(not isinstance(rule, str) or not rule.strip() or len(rule.strip()) > 2000 for rule in rules)):
        raise ValidationError(
            'Configura hasta 30 reglas de escalamiento de hasta 2000 caracteres cada una.')
    schedule = value['reminder_schedule']
    transitions = [('none', 'first'), ('first', 'second'), ('second', 'third')]
    if not isinstance(schedule, list) or len(schedule) > 3:
        raise ValidationError('Configura hasta 3 tiempos de recordatorio.')
    disabled_stage = False
    for index, item in enumerate(schedule):
        if (not isinstance(item, dict) or set(item) != {'from', 'to', 'amount', 'unit', 'enabled'}
                or (item.get('from'), item.get('to')) != transitions[index]
                or type(item.get('amount')) is not int or not 1 <= item['amount'] <= 365
                or item.get('unit') not in ('hours', 'days') or type(item.get('enabled')) is not bool):
            raise ValidationError(
                'Los tiempos de recordatorio deben avanzar en orden y usar un intervalo de 1 a 365 horas o días.')
        if disabled_stage and item['enabled']:
            raise ValidationError(
                'Habilita los recordatorios en orden, sin saltar etapas.')
        disabled_stage = disabled_stage or not item['enabled']
    if type(value['agrupar_preguntas']) is not bool:
        raise ValidationError(
            'Agrupar preguntas debe estar activado o desactivado.')
    for key, (label, choices) in PROMPT_CHOICE_FIELDS.items():
        if not isinstance(value[key], str) or value[key] not in choices:
            raise ValidationError(f'{label} tiene una opción no válida.')
    if not isinstance(value['custom_methodology'], str) or len(value['custom_methodology']) > 10000:
        raise ValidationError(
            'Mi metodología debe ser texto de hasta 10000 caracteres.')
    if value['qualification_method'] == 'custom' and not value['custom_methodology'].strip():
        raise ValidationError('Describe tu metodología antes de guardarla.')
    for key, (label, _) in PROMPT_TOGGLE_FIELDS.items():
        if type(value[key]) is not bool:
            raise ValidationError(
                f'{label} debe estar activado o desactivado.')
    if not value['name'].strip() or not value['enterprise_name'].strip():
        raise ValidationError(
            'El nombre del agente y el de la empresa son obligatorios.')
    if not value['saludo'].strip():
        raise ValidationError('El saludo no puede estar vacío.')
    units = value['business_units']
    if (not isinstance(units, list) or any(type(pk) is not int or pk <= 0 for pk in units)
            or len(units) != len(set(units))):
        raise ValidationError(
            'business_units debe contener los identificadores de las unidades, sin duplicados.')


def render_response_prompt(template, configuration, units, **conversation_values):
    """Una sola sustitución: los valores editados se insertan como texto literal."""
    template_variables = set(Template(template).get_identifiers())
    configuration = with_prompt_defaults(configuration)
    values = {key: configuration.get(key, '') for key in PROMPT_TEXT_FIELDS}
    values['reglas_escalamiento'] = '\n'.join(
        f'- {rule}' for rule in configuration['escalation_rules']
    )
    values['greeting'] = render_greeting(configuration)
    choices = configuration
    values['treatment'] = TRATO_INSTRUCTIONS[choices['trato']]
    response_preferences = [
        *(instruction for key, (_, instruction)
          in PROMPT_TOGGLE_FIELDS.items() if choices[key]),
        LONGITUD_RESPUESTA_INSTRUCTIONS[choices['longitud_respuesta']],
        CALIDEZ_INSTRUCTIONS[choices['calidez']],
    ]
    if values['tone'].strip():
        response_preferences.append('Otras indicaciones de comunicación:\n' + values['tone'].strip())
    values['response_preferences'] = '\n'.join(response_preferences)
    values['response_length'] = {
        'breve': 'breves',
        'equilibrado': 'de extensión equilibrada',
        'explicativo': 'explicativas',
    }[choices['longitud_respuesta']]
    commercial_style = ESTILO_COMERCIAL_INSTRUCTIONS[choices['estilo_comercial']]
    legacy_commercial_style = commercial_style if 'estilo_ventas' not in template_variables else ''
    if 'response_preferences' in template_variables:
        values['tone'] = '\n'.join(part for part in (
            values['treatment'] if 'treatment' not in template_variables else '',
            legacy_commercial_style,
        ) if part) or 'Sigue el trato, las preferencias de respuesta y el estilo de ventas definidos en este prompt.'
    else:
        values['tone'] = '\n'.join(part for part in (
            values['tone'],
            'Las siguientes preferencias de comunicación tienen prioridad sobre cualquier indicación anterior de tono o registro:',
            values['treatment'] if 'treatment' not in template_variables else '',
            legacy_commercial_style,
            LONGITUD_RESPUESTA_INSTRUCTIONS[choices['longitud_respuesta']],
            CALIDEZ_INSTRUCTIONS[choices['calidez']],
            *(instruction for key, (_, instruction)
              in PROMPT_TOGGLE_FIELDS.items() if choices[key]),
        ) if part)
    if configuration['objective_conversation'].strip() or configuration['service_flows']:
        flow_parts = []
        if configuration['objective_conversation'].strip():
            flow_parts.append(
                'Objetivo: ' + configuration['objective_conversation'])
        if configuration['service_flows']:
            fallback = (
                'Usa «Otros servicios» cuando no haya un flujo específico. '
                if any(flow['service'].strip().casefold() == 'otros servicios'
                       for flow in configuration['service_flows']) else
                'Si no hay un servicio coincidente, aclara primero la necesidad. '
            )
            flow_parts.append(
                'Identifica el servicio de interés. Usa sus preguntas configuradas como contexto '
                'y sigue el orden de la metodología de calificación para los datos pertinentes que falten. '
                + fallback
            )
        for flow in configuration['service_flows']:
            flow_parts.append('Servicio: ' + flow['service'] + '\n' + '\n'.join(
                f'- {question}' for question in flow['questions']
            ))
        if configuration['service_flows']:
            flow_parts.append(
                'Agrupa las preguntas pendientes en un solo mensaje estructurado.'
                if configuration['agrupar_preguntas'] else
                'Haz las preguntas pendientes de forma gradual, según avance la conversación.'
            )
        if values['flujo_conversacion'].strip():
            flow_parts.append(values['flujo_conversacion'])
        values['flujo_conversacion'] = '\n\n'.join(flow_parts)
    methodology = QUALIFICATION_METHODOLOGIES.get(
        configuration['qualification_method'], QUALIFICATION_METHODOLOGIES['basic']
    )
    if configuration['qualification_method'] == 'custom':
        methodology_guidance = (
            'Metodología de calificación personalizada:\n'
            + configuration['custom_methodology'].strip()
        )
    else:
        methodology_guidance = (
            f"Plantilla de calificación: {methodology['name']}.\n"
            f"Orden de preguntas: {methodology['order']}.\n"
            f"{methodology['description']}"
        )
    values['estilo_ventas'] = '\n\n'.join((
        methodology_guidance,
        'Aplica este orden a los datos que faltan y sean pertinentes. No repitas respuestas ya conocidas; '
        'en plantillas avanzadas, deja las dimensiones restantes al equipo de ventas.',
        'Estilo comercial: ' + commercial_style,
    ))
    values['business_units'] = '\n'.join(
        f"- {unit['name']}: {unit['description']}" for unit in units
    ) or 'No hay unidades de negocio configuradas. Usa NO_DETERMINADO.'
    values['business_unit_names'] = json.dumps(
        [unit['name'] for unit in units], ensure_ascii=False)
    return Template(template).substitute(**values, **conversation_values)


def legacy_prompt_config(name, web, general):
    """Recupera los apartados editables de los prompts completos anteriores."""
    config = sofia_defaults()['prompt']
    config['name'] = name
    for key, source, phrase, level in (
        ('tone', general, 'Tono y estilo', 2),
        ('flujo_conversacion', web or general, 'Bienvenida, descubrimiento', 3),
        ('reglas_escalamiento', general or web, 'Reglas de escalamiento', 2),
    ):
        heading = re.search(
            r'^#{' + str(level) + r'} .*' + phrase + r'.*\n', source, re.M | re.I)
        if not heading:
            continue
        remaining = source[heading.end():]
        next_heading = re.search(
            r'^#{1,' + str(level) + r'} ', remaining, re.M)
        text = remaining[:next_heading.start()] if next_heading else remaining
        if text.strip() and '${' + key + '}' not in text:
            config[key] = text.strip()
    enterprise = re.search(
        r'asistente de prospección de ([^,\n]+)', general or web, re.I)
    if enterprise and '${' not in enterprise.group(1):
        config['enterprise_name'] = enterprise.group(1).strip()
    return config
