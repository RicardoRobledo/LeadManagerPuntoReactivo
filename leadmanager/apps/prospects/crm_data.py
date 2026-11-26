"""
Parseo defensivo de los bloques CRM que produce la IA.

El análisis no tiene forma fija: puede venir en la raíz de
``conversation_content`` o anidado bajo ``crm_update``, y a veces llega
guardado como string JSON en vez de objeto. Esta lógica vivía inline en
``ConversationView``; aquí se concentra para que el panel y la
exportación a Excel lean los datos exactamente de la misma manera.
"""

import json


__author__ = 'Ricardo'
__version__ = '0.1'


def parse_json_object(value):
    """
    Convierte un JSON guardado como texto en dict. Devuelve {} cuando el
    valor no es un objeto o no se puede parsear.
    """
    if isinstance(value, dict):
        return value

    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}

        return parsed if isinstance(parsed, dict) else {}

    return {}


def es_escalar(value):
    """
    ¿La IA marcó escalar_a_humano? Es el filtro de "calificado": la
    conversación se escaló a un humano y la oportunidad se escribe en
    Odoo con la etiqueta de Sofía. Se acepta el booleano guardado como
    texto porque el JSON histórico no siempre respeta el tipo.
    """
    if value is True:
        return True

    if isinstance(value, str):
        return value.strip().lower() == 'true'

    return False


def merge_crm_payload(payload):
    """
    Aplana ``conversation_content``: ``crm_update`` como base y la raíz
    encima, que es lo que gana cuando la clave se repite.
    """
    content = parse_json_object(payload)

    if not content:
        return {}

    crm_update = content.get('crm_update')

    if isinstance(crm_update, dict):
        crm = dict(crm_update)

        for key, value in content.items():
            if key != 'crm_update':
                crm[key] = value

        return crm

    return dict(content)


def extract_chat_analysis(message):
    """
    Saca del content de un mensaje del asistente el par
    (crm_update, raíz parseada). Cualquiera de los dos puede ser {}.
    """
    if not isinstance(message, dict):
        return {}, {}

    content = message.get('content', '')

    if not isinstance(content, str):
        return {}, {}

    parsed = parse_json_object(content)

    crm_update = parsed.get('crm_update')

    if not isinstance(crm_update, dict):
        return {}, parsed

    return crm_update, parsed


def is_empty_crm_value(value):
    """¿La clave está vacía o ausente, y por tanto se puede rellenar?"""
    return (
        value is None
        or (isinstance(value, str) and value.strip() == '')
        or (isinstance(value, dict) and not value)
        or (isinstance(value, list) and not value)
    )


def fill_empty_crm(crm, update):
    """
    Rellena en ``crm`` solo las claves vacías o ausentes, tomando los
    valores de ``update``. Nunca sobreescribe lo que ya había.
    """
    if crm is None:
        crm = {}

    for key, value in update.items():
        if is_empty_crm_value(crm.get(key)) and value not in (None, '', {}, []):
            crm[key] = value

    return crm


def contact_from_crm(crm):
    """
    Normaliza el contacto para el panel: el escalamiento lo guarda en
    campos planos (contact_name, partner_name, email_from) y el panel
    espera un objeto ``contacto``. Si ya viene como objeto, no se toca.
    """
    if crm is None or isinstance(crm.get('contacto'), dict):
        return crm

    crm['contacto'] = {
        'nombre': crm.get('contact_name') or crm.get('name'),
        'empresa': crm.get('partner_name'),
        'correo': crm.get('email_from') or crm.get('correo'),
    }

    return crm
