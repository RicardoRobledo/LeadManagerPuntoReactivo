import requests

from django.conf import settings


def _text(value):
    """Fields in crm_update arrive as null when Sofia doesn't have them,
    so .get(k, "") isn't enough: the None has to be converted."""
    return (value or "").strip()


def call(model, method, args, kwargs=None):
    """Calls a method on an Odoo model via JSON-RPC."""
    if kwargs is None:
        kwargs = {}

    body = {
        "jsonrpc": "2.0",
        "method": "call",
        "params": {
            "service": "object",
            "method": "execute_kw",
            "args": [settings.ODOO_DB, settings.ODOO_UID,
                     settings.ODOO_API_KEY, model, method, args, kwargs],
        },
    }

    r = requests.post(f"{settings.ODOO_URL}/jsonrpc", json=body, timeout=30)
    response = r.json()

    if response.get("error"):
        raise RuntimeError(
            f"ODOO ERROR [{model}.{method}]: {response['error']}")

    return response.get("result")


def find_existing_lead(crm_update):
    """
    Busca un lead ACTIVO en la etapa "Lead" con ese correo o teléfono.
    Devuelve el dict del lead, o None.

    Solo esa etapa: es la que todavía no ha tocado un vendedor. Si el lead
    ya avanzó (Sin Calificar, Cotización, Won, Perdida...), no se busca y
    create_or_update_lead abre una oportunidad nueva, en vez de
    sobreescribir la que el vendedor está trabajando.
    """
    contact = crm_update.get("contacto") or {}

    email = _text(contact.get("correo"))
    phone = _text(contact.get("telefono"))

    conditions = []
    if email:
        # ilike (not "=ilike"): some leads hold two emails in one field
        conditions.append(["email_from", "ilike", email])
    if phone:
        # ilike: phone numbers are stored with free formatting in Odoo
        conditions.append(["phone", "ilike", phone])

    if not conditions:
        return None

    odoo_lead_stage_id = 15

    domain = (["|"] + conditions) if len(conditions) == 2 else conditions
    domain = domain + [["stage_id", "=", odoo_lead_stage_id]]

    results = call("crm.lead", "search_read", [domain], {
        "fields": ["id", "name", "email_from", "phone", "contact_name",
                   "partner_name", "partner_id", "function", "type",
                   "user_id", "x_studio_unidad_de_venta"],
        "limit": 1,
        "order": "id desc",
    })

    return results[0] if results else None


def find_or_create_company(name):
    """
    Looks up a company by name. Creates it if missing.

    Deliberately takes no email or phone: those belong to the person.
    Putting them on the company pollutes real partner records, and it also
    made the person lookup match the company and return its id instead.
    """

    name = _text(name)

    if not name:
        return None

    existing = call("res.partner", "search_read",
                    [[["name", "=ilike", name], ["is_company", "=", True]]],
                    {"fields": ["id"], "limit": 1})

    if existing:
        return existing[0]["id"]

    return call("res.partner", "create",
                [{"name": name, "is_company": True}])


def find_person_by_source(identifier, source):
    """
    Looks up a person (not a company) by the identifier of the channel the
    message arrived through. Returns the partner dict or None.

    `source` says what `identifier` is: "whatsapp" makes it a phone number;
    "email", "form" and "web" make it an email address. Only that field is
    searched — the other
    one is whatever Sofia extracted from the conversation, and matching on
    it can pull up the wrong person (a company switchboard already on file
    for a colleague, a shared inbox like ventas@ used by several people).

    Filters is_company=False on purpose: if the same email also sat on a
    company record, without that filter this would return the company's id
    and the lead would end up linked to the company instead of the contact.
    """
    if source == "whatsapp":
        field, operator, identifier = "phone", "ilike", _text(identifier)
    elif source in ("email", "form", "web"):
        field, operator, identifier = "email", "=ilike", _text(identifier)
    else:
        # Un source desconocido no se puede buscar. Antes no había este
        # else y la ejecución seguía hasta el search_read con ``field`` y
        # ``operator`` sin asignar, reventando en UnboundLocalError.
        return None

    if not identifier:
        return None

    # ``order`` explícito: sin él, un correo con contactos duplicados en
    # Odoo devuelve uno distinto según el orden por omisión del modelo.
    # ``id desc`` prefiere el más reciente, igual que find_existing_lead.
    existing = call("res.partner", "search_read",
                    [[[field, operator, identifier],
                      ["is_company", "=", False]]],
                    {"fields": ["id", "name", "email", "phone",
                                "function", "parent_id"],
                     "limit": 1,
                     "order": "id desc"})

    return existing[0] if existing else None


def create_person(name, email=None, phone=None, job_title=None,
                  company_id=None):
    """Creates a person in res.partner. Returns its id, or None when there
    is neither a name nor an email to identify it by."""
    name = _text(name)
    email = _text(email)
    phone = _text(phone)
    job_title = _text(job_title)

    if not name and not email:
        return None

    return call("res.partner", "create", [{
        "name": name or email,
        "is_company": False,
        "email": email or False,
        "phone": phone or False,
        "function": job_title or False,
        "parent_id": company_id or False,
    }])


def build_lead_values(crm_update, source=None, seller_id=None):
    """Translates the agent's crm_update into crm.lead fields."""
    contact = crm_update.get("contacto") or {}
    bant = crm_update.get("bant") or {}

    sesgos = crm_update.get("sesgos_detectados") or []
    sesgos_display = ", ".join(sesgos) if sesgos else "Ninguno detectado"
    score = crm_update.get("score_comercial")

    description = "".join([
        "<b>📊 NUEVA OPORTUNIDAD</b><br><br>",

        "<b>🎯 CONTEXTO GENERAL</b><br>",
        f"• Canal: {source or '—'}<br>",
        f"• Unidad de negocio: {crm_update.get('unidad_negocio_detectada') or '—'}<br><br>",

        "<b>💼 BANT</b><br>",
        f"• Autoridad: {bant.get('authority') or '—'}<br>",
        f"• Necesidad: {bant.get('need') or '—'}<br>",
        f"• Tiempo: {bant.get('timing') or '—'}<br>",
        f"• Guía negociación: {crm_update.get('guia_negociacion_sugerida') or '—'}<br><br>",

        "<b>🗣️ RESUMEN DE CONVERSACIÓN</b><br>",
        f"{crm_update.get('resumen_conversacion') or '—'}<br>",
        f"{crm_update.get('resumen_empresa') or '—'}<br><br>",

        "<b>📍 ANÁLISIS COMERCIAL</b><br>",
        f"• Punto de referencia: {crm_update.get('punto_de_referencia') or '—'}<br>",
        f"• Sesgos detectados: {sesgos_display}<br>",
        f"• Orientación decisión: {crm_update.get('orientacion_decision') or '—'}<br>",
        f"• Necesidad de certidumbre: {crm_update.get('necesidad_certidumbre') or '—'}<br>",
        f"• Semáforo: {(crm_update.get('semaforo') or '—').upper()}<br>",
        f"• Score comercial: {score if score is not None else '—'}<br>",
        f"• Perfil psicológico: {crm_update.get('etiqueta_perfil_psicologico') or '—'}",
    ])

    unit = crm_update.get("unidad_negocio_detectada") or "no_determinado"
    display_name = _text(contact.get("nombre")) or _text(contact.get("correo"))

    values = {
        "name": f"{unit} — {display_name or 'Sin nombre'}",
        "contact_name": _text(contact.get("nombre")) or False,
        "email_from": _text(contact.get("correo")) or False,
        "phone": _text(contact.get("telefono")) or False,
        "partner_name": _text(contact.get("empresa")) or False,
        "function": _text(bant.get("authority")) or False,
        "description": description,
        "tag_ids": [[6, 0, [settings.ODOO_TAG_ID]]],
        "type": "opportunity",
        "user_id": seller_id or False,
        "x_studio_unidad_de_venta": find_business_unit_id(
            crm_update.get("unidad_negocio_detectada")) or False,
    }

    return {k: v for k, v in values.items() if v is not False or k == "type"}


def find_business_unit_id(name):
    """Traduce la unidad de negocio detectada por el LLM al id de
    crm.business.unit. El campo x_studio_unidad_de_venta es many2one:
    no acepta el texto, necesita el id."""
    name = _text(name).replace("_", " ")

    if not name or name.upper() == "NO DETERMINADO":
        return None

    found = call("crm.business.unit", "search_read",
                 [[["name", "=ilike", name]]],
                 {"fields": ["id"], "limit": 1})

    return found[0]["id"] if found else None


def create_or_update_lead(crm_update, source, identifier, person, seller_id=None, company_id=None):
    """
    Punto de entrada. Crea o actualiza una oportunidad, y crea el contacto
    (res.partner) cuando todavía no existe.

    Flujo:
      1. Resuelve la persona: reutiliza el partner si ya existe en Odoo y
         solo lo crea si falta. El lead se liga a la PERSONA, nunca a la
         empresa.
      2. Busca un lead activo de ese correo/teléfono en la etapa "Lead"
         (etapa "Lead", id 15). Luego, una de dos:
         - lo encuentra -> lo actualiza: llena solo los campos vacíos y
           reemplaza la descripción con el resumen acumulado de Sofia. Es
           seguro porque en esa etapa ningún vendedor lo ha tocado.
         - no lo encuentra (no hay lead, o el anterior ya avanzó de etapa,
           se ganó, se perdió o se archivó) -> crea una oportunidad nueva
           y no toca la anterior.

    Solo se crea la persona cuando hay un identificador con qué volver a
    encontrarla. Sin él no habría forma de buscarla después, y cada
    mensaje agregaría otro duplicado a la libreta de contactos; en ese
    caso el nombre queda como texto en contact_name del lead.

    Ojo: Odoo copia el correo/teléfono del lead al partner ligado cuando
    esos campos del partner están vacíos. Como aquí los datos vienen de un
    LLM leyendo un correo, una extracción mala puede sobreescribir en
    silencio un contacto real.

    :param crm_update: el bloque crm_update que produce el agente.
    :param source: canal del identificador: "email", "form", "web" o
        "whatsapp".
    :param identifier: correo o teléfono verificado con el que se busca a
        la persona.
    :param person: el dict del partner ya resuelto (o {} si no se
        encontró), tal como lo devuelve find_person_by_source. El partner,
        NO el envoltorio {"data", "exists"} de ctx["person"].
    :param seller_id: id del usuario de Odoo que se asigna como vendedor.
    :param company_id: id de la empresa en Odoo, para la persona nueva.
    :return: {"action": "created" | "updated",
              "lead_id": int,      # crm.lead
              "person_id": int,    # res.partner, secuencia distinta
              "seller_id": int | None,
              "seller_name": str | None}
    """
    values = build_lead_values(crm_update, source=source, seller_id=seller_id)

    contact = crm_update.get("contacto") or {}
    bant = crm_update.get("bant") or {}

    # `person` ya ES el dict del partner (o {} si no se encontró); no viene
    # anidado bajo "person" ni trae "exists" adentro
    person = person or {}
    person_id = person.get("id")

    # La persona se crea con el dato VERIFICADO del canal, no con lo que
    # extrajo el modelo: find_person_by_source la busca por ese mismo dato,
    # y si no coinciden, la próxima vez no la encuentra y crea otra.
    email = identifier if source in (
        "email", "form", "web") else contact.get("correo")
    phone = identifier if source == "whatsapp" else contact.get("telefono")

    if not person_id and identifier:
        person_id = create_person(
            contact.get("nombre"),
            email=email,
            phone=phone,
            job_title=bant.get("authority"),
            company_id=company_id,
        )

    # the lead links to the PERSON, not the company
    if person_id:
        values["partner_id"] = person_id

    def _resultado(action, lead_id, assigned_user=None):
        """lead_id es de crm.lead; person_id es de res.partner. Son ids de
        modelos distintos, no se pueden intercambiar."""
        assigned_id = (
            assigned_user[0] if isinstance(assigned_user, (list, tuple))
            else assigned_user or seller_id
        )
        return {
            "action": action,
            "lead_id": lead_id,
            "person_id": person_id,
            "seller_id": assigned_id,
            "seller_name": (
                assigned_user[1] if isinstance(assigned_user, (list, tuple))
                and len(assigned_user) > 1 else None
            ),
        }

    lead = find_existing_lead(crm_update)

    if lead is None:
        # No hay lead en la etapa "Lead": es la primera vez, o el anterior ya
        # lo movió un vendedor (o se ganó o se perdió). Se abre uno nuevo y
        # el anterior no se toca.
        lead_id = call("crm.lead", "create", [values])
        if not lead_id:
            raise RuntimeError('Odoo no confirmó la creación del lead.')
        return _resultado("created", lead_id)

    updates = {}

    # only fill gaps, never overwrite what a human entered
    for field in ("contact_name", "email_from", "phone",
                  "partner_name", "function", "partner_id",
                  "x_studio_unidad_de_venta"):
        if not lead.get(field) and values.get(field):
            updates[field] = values[field]

    # Sofia's summary is already cumulative, so it gets replaced. Es seguro
    # porque aquí solo llegan leads en etapa "Lead": ningún vendedor ha
    # escrito notas todavía.
    updates["description"] = values["description"]

    if lead.get("type") != "opportunity":
        updates["type"] = "opportunity"

    if values.get("user_id") and not lead.get("user_id"):
        updates["user_id"] = values["user_id"]

    if not call("crm.lead", "write", [[lead["id"]], updates]):
        raise RuntimeError('Odoo no confirmó la actualización del lead.')
    return _resultado("updated", lead["id"], lead.get("user_id"))
