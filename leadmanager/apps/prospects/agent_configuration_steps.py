"""Pasos visibles del editor de cada agente.

Cada agente puede definir su orden y sus plantillas sin cambiar el menú ni el
código de navegación. Las plantillas contienen los campos de su formulario.
"""


PROSPECTOR_STEPS = (
    {
        'key': 'communication',
        'label': 'Comunicación',
        'template': 'prospects/agent_steps/communication.html',
    },
    {
        'key': 'conversation_flow',
        'label': 'Flujo de conversación',
        'template': 'prospects/agent_steps/conversation_flow.html',
    },
    {
        'key': 'sales',
        'label': 'Ventas',
        'template': 'prospects/agent_steps/sales.html',
    },
    {
        'key': 'escalation',
        'label': 'Reglas de escalamiento',
        'template': 'prospects/agent_steps/escalation.html',
    },
    {
        'key': 'business_units',
        'label': 'Unidades de negocio',
        'template': 'prospects/agent_steps/business_units.html',
    },
    {
        'key': 'reminders',
        'label': 'Recordatorios',
        'template': 'prospects/agent_steps/reminders.html',
    },
    {
        'key': 'test',
        'label': 'Prueba',
        'template': 'prospects/agent_steps/test.html',
    },
)

PROSPECTOR_TEST_CASES = (
    {'key': 'viendo', 'label': 'Solo está viendo',
     'message': 'Hola, solo estoy viendo qué opciones tienen. Aún no tengo un proyecto definido.',
     'follow_up': 'Me interesa conocer las opciones de inteligencia artificial para mi empresa.'},
    {'key': 'proveedor', 'label': 'Proveedor',
     'message': 'Hola, somos proveedores de logística y queremos ofrecerles nuestros servicios.',
     'follow_up': 'Podemos enviarles una presentación si nos indican con quién hablar.'},
    {'key': 'correo_incompleto', 'label': 'Correo incompleto',
     'message': 'Buscamos kioscos digitales. Mi correo es contacto@ y quisiera una cotización.',
     'follow_up': 'El correo correcto es contacto@empresa-ejemplo.com.'},
    {'key': 'correo_prueba', 'label': 'Correo de prueba',
     'message': 'Quiero información sobre Odoo. Mi correo es test@example.com.',
     'follow_up': 'Todavía no tengo un correo empresarial para compartir.'},
    {'key': 'soporte', 'label': 'Soporte',
     'message': 'Ya soy cliente y necesito soporte para un sistema que ustedes implementaron.',
     'follow_up': 'La aplicación muestra un error al iniciar sesión.'},
    {'key': 'insulto', 'label': 'Insulto directo',
     'message': 'Su servicio es pésimo. Estoy muy molesto y quiero una solución.',
     'follow_up': 'Quiero hablar con alguien que pueda resolver mi problema.'},
    {'key': 'no_contactar', 'label': 'No contactar',
     'message': 'No quiero que me vuelvan a contactar. Por favor eliminen mis datos.',
     'follow_up': 'Confirmo que no deseo recibir más mensajes.'},
    {'key': 'humano', 'label': 'Quiere humano',
     'message': 'Prefiero hablar con una persona de su equipo antes de continuar.',
     'follow_up': '¿Podrían llamarme para conversar sobre mi proyecto?'},
    {'key': 'prisa', 'label': 'Tiene prisa',
     'message': 'Busco kioscos interactivos para reducir filas. Necesito resolverlo esta semana.',
     'follow_up': 'Serían cinco kioscos y puedo compartir los detalles hoy.'},
    {'key': 'empleo', 'label': 'Empleo',
     'message': 'Hola, quiero saber si tienen vacantes. Me interesa trabajar con ustedes.',
     'follow_up': 'Tengo experiencia en desarrollo de software.'},
)

AGENT_CONFIGURATION_STEPS = {
    'prospector': PROSPECTOR_STEPS,
}

AGENT_TEST_CASES = {
    'prospector': PROSPECTOR_TEST_CASES,
}


def get_agent_configuration_steps(agent_key):
    return AGENT_CONFIGURATION_STEPS.get(agent_key, PROSPECTOR_STEPS)


def get_agent_test_cases(agent_key):
    return AGENT_TEST_CASES.get(agent_key, PROSPECTOR_TEST_CASES)
