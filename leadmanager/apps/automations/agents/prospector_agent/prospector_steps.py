import json
from string import Template
from pydantic import ValidationError

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import transaction

from leadmanager.apps.documents.models import DocumentModel
from leadmanager.apps.prospects.models import ProspectModel, ChatHistoryModel, ConversationModel, ConversationStateEnum, EmailMessageModel, EmailMessageStatusEnum
from leadmanager.apps.automations.models import (
    AgentEventModel,
    AgentModel,
    LearningAnnotationModel,
)
from leadmanager.apps.automations.agent_catalog import get_agent_configuration
from leadmanager.apps.automations.agent_configuration import DEFAULT_BUSINESS_UNIT_NAME, render_response_prompt
from .schemas.configured_answer import configured_answer_schema

from ...services.imap_service import send_email, casos_de_exito_cabe_como_adjunto
from ...services.odoo_service import find_person_by_source, find_or_create_company, create_or_update_lead
from ...google_sheets_tasks import register_google_sheet_lead_task
from .prospect_resource_handler import ProspectResourceHandler
from .schemas.prospect_schema import SearchSchema
from ..general_schemas import ValidationSchema
from ..general_tools import web_search_tool
from ..general_functions import get_previous_conversation_context
from ..error_policy import CorreoOmitidoError

import openai
import logging


__author__ = "Ricardo"
__version__ = "0.1"

logger = logging.getLogger(__name__)

def _sync_prospect(person_id, email, datos):
    """
    Refleja el contacto en ProspectModel y devuelve la fila, o None si no hay
    ni person_id ni correo con qué indexarla.

    ProspectModel tiene DOS columnas únicas, person_id y email. Buscar solo
    por person_id hacía que el INSERT chocara con la otra
    ("prospects_email_key") cuando Odoo devolvía otro partner para un
    correo ya guardado, como pasa con los contactos duplicados en Odoo. Por
    eso se busca por las dos, y la fila se repunta al partner vigente.

    Sin person_id -un visitante web que todavía no está en Odoo- la fila se
    ata solo al correo. Si después escala, la búsqueda por correo la
    encuentra y le pone el person_id: queda una sola fila, no dos.
    """
    email = (email or "").strip().lower() or None

    if not person_id and not email:
        return None

    datos = {campo: valor or None for campo, valor in datos.items()}

    # phone y function son varchar(50): un puesto largo de la IA reventaría
    # el guardado después de haber escrito en Odoo.
    for campo in ("phone", "function"):
        if datos.get(campo):
            datos[campo] = datos[campo][:50]

    # Solo se busca por person_id cuando hay uno: filter(person_id=None) es
    # "IS NULL" y traería la fila de cualquier otro visitante sin partner.
    prospect = None
    if person_id:
        prospect = ProspectModel.objects.filter(person_id=person_id).first()
    if prospect is None and email:
        prospect = ProspectModel.objects.filter(email__iexact=email).first()

    if prospect is None:
        return ProspectModel.objects.create(person_id=person_id, email=email, **datos)

    # Sin person_id no se toca el que ya tenga la fila: un None no la
    # desliga del partner con el que ya estaba.
    if person_id:
        prospect.person_id = person_id

    # email también es único: solo se escribe si no lo tiene otra fila.
    if email and not ProspectModel.objects.filter(
            email__iexact=email).exclude(pk=prospect.pk).exists():
        prospect.email = email

    for campo, valor in datos.items():
        if valor:  # un null del modelo no borra lo que ya había
            setattr(prospect, campo, valor)

    prospect.save()
    return prospect


UTM_KEYS = ('utm_source', 'utm_medium', 'utm_campaign',
            'utm_content', 'utm_term', 'traffic_origin',)


def _leer_utms(ctx):
    """
    Los UTM que mando el sitio, sacados del payload del canal.

    La llave del payload es el propio source: ctx['web'] o ctx['form'].
    El correo no trae ninguno y devuelve los cinco vacios, que es
    correcto: un correo entrante no tiene navegador ni URL de aterrizaje
    de donde sacarlos.
    """
    contenido = ctx.get(ctx.get('source')) or {}

    return {
        clave: (contenido.get(clave) or '').strip()[:255]
        for clave in UTM_KEYS
    }


def revertir_turno(ctx):
    """
    Borra lo que esta corrida escribio en el historial, para que un
    reintento no duplique el mensaje del prospecto.

    Solo aplica si el turno murio ANTES de generar la respuesta. A partir
    de ahi el historial ya vale algo, y ademas generate_history_summary_step
    pudo haberlo compactado en un resumen: eso no se puede deshacer.

    Nunca levanta: se llama desde un ``except`` y, si fallara, taparia el
    error original.

    Devuelve cuantos mensajes borro, -1 si se llevo la conversacion entera,
    o None si la reversa misma fallo.
    """
    try:
        if ctx.get('response_to_prospect') is not None:
            return 0

        conversation = ctx.get('conversation')

        if conversation is None:
            return 0

        if ctx.get('_conversacion_nueva') and not ctx.get('_google_sheets_registration_attempted'):
            # La creo esta corrida y quedaria vacia: se va entera, y el
            # CASCADE se lleva sus mensajes.
            ConversationModel.objects.filter(id=conversation.id).delete()
            return -1

        # Sheets puede haber registrado o estar por registrar este ID.
        # Conservarlo evita otra fila cuando el pipeline vuelva a intentarlo.

        marca = ctx.get('_marca_historial')

        if marca is None:
            # Sin marca no se sabe que mensajes son de este turno. Ante la
            # duda no se borra: duplicar es molesto, borrar de mas no.
            return 0

        borrados, _ = ChatHistoryModel.objects.filter(
            conversation_id=conversation.id,
            id__gt=marca,
        ).delete()

        return borrados

    except Exception:
        logger.exception('No se pudo revertir el turno')
        return None


# ---------------------


def verify_existing_prospect_step(agent, ctx):
    """
    Checks if the prospect already exists in odoo based the source and
    respective email or phone. If it exists, adds the prospect data to
    ``ctx["person"]``. If not, sets ``ctx["person"]["data"]`` to ``{}``.
    """

    source = ctx.get("source")
    identifier = None
    prospect = None
    db_prospect = None

    if source in ['email', 'form', 'whatsapp', 'web']:
        if source == 'email':
            email_content = ctx["email"]
            identifier = email_content.get("from", {}).get("email", "")
        elif source == 'form':
            form_content = ctx["form"]
            identifier = form_content.get("email")
        elif source == 'whatsapp':
            whatsapp_content = ctx["whatsapp"]
            identifier = whatsapp_content.get("phone")
        elif source == 'web':
            web_content = ctx["web"]
            identifier = web_content.get("email")

        db_prospect = ProspectModel.objects.filter(email__iexact=identifier).values(
            'id', 'email', 'phone', 'name', 'function', 'enterprise', 'enterprise_summary', 'person_id'
        ).first()

    prospect = find_person_by_source(identifier=identifier, source=source)

    agent.log_event(
        ctx,
        message="Datos del prospecto obtenidos de crm",
        data={"prospect": prospect if prospect else {},
              "exists": bool(prospect),
              "db_data": db_prospect,
              "db_exists": bool(db_prospect),
              "source": source},
        level=AgentEventModel.LevelEnum.INFO,
    )

    ctx["source_identifier"] = identifier
    ctx["person"] = {
        "data": prospect if prospect else {},  # Odoo person data
        "exists": bool(prospect),  # Odoo person exists
        "db_data": db_prospect,  # Db person data
        "db_exists": bool(db_prospect),  # Db person exists
    }


def verify_existing_conversation_step(agent, ctx):
    """
    Verify if there is an open conversation for the session_id.
    If it exists, retrieve the last N messages. If not, create a new one.
    The conversation is stored in ctx["conversation"] and the history in ctx["history"].
    Finally a message is created in the ChatHistoryModel with the current message.
    """
    source = ctx.get('source', '')

    conversation = None
    session_id = None
    all_messages = []

    message = {"role": "user", "content": ""}

    if source == 'email':
        email_content = ctx["email"]
        session_id = email_content.get("from", {}).get("email", "")
        message['content'] = 'correo:{email}. cuerpo:{body}'.format(
            email=email_content.get("from", {}).get("email", ""),
            body=email_content.get("body", ""),
        )

    elif source == 'whatsapp':
        whatsapp_content = ctx["whatsapp"]
        session_id = whatsapp_content.get("phone", "")
        message['content'] = whatsapp_content.get("message", "")

    elif source == 'form':
        form_content = ctx["form"]
        session_id = form_content.get("email", "")
        message['content'] = 'name:{name}, email:{email}, phone:{phone}, subject:{subject}, message:{message}'.format(
            name=form_content.get("name", ""),
            email=form_content.get("email", ""),
            phone=form_content.get("phone", ""),
            subject=form_content.get("subject", ""),
            message=form_content.get("message", ""),
        )

    elif source == 'web':
        web_content = ctx["web"]
        session_id = web_content.get("session_id", "")
        content = ', correo:{email}, nombre:{name}, empresa:{company_name}, unidad_negocio:{business_unit}, mensaje:{message}'.format(
            business_unit=web_content.get("business_unit", ""),
            name=web_content.get("name", ""),
            email=web_content.get("email", ""),
            company_name=web_content.get("company_name", ""),
            message=web_content.get("message", ""),
        )

        if web_content.get("preferred_contact_method") == "phone":
            content = 'medio_preferido:whatsapp, telefono:{phone}'.format(
                phone=web_content.get("phone", "")) + content
        elif web_content.get("preferred_contact_method") == "email":
            content = 'medio_preferido:correo' + content

        message['content'] = content

    if source == 'web' and web_content.get('_conversation_id'):
        conversation = ConversationModel.objects.filter(
            pk=web_content['_conversation_id'], source='web', session_id=session_id,
        ).prefetch_related('messages')
        if not conversation.exists():
            raise ValueError('La conversación web del mensaje ya no existe.')
    else:
        conversation = ConversationModel.objects.filter(
            session_id=session_id, state=ConversationStateEnum.OPEN
        ).prefetch_related('messages')

    ctx['_conversacion_nueva'] = not conversation.exists()

    utms = _leer_utms(ctx)

    if ctx['_conversacion_nueva']:

        conversation = ConversationModel.objects.create(
            session_id=session_id, state=ConversationStateEnum.OPEN,
            source=source, **utms
        )

        past_context = get_previous_conversation_context(session_id)

        if past_context:
            context_msg = {"role": "assistant", "content": past_context}
            ChatHistoryModel.objects.create(
                conversation=conversation, message=context_msg)
            all_messages.append(context_msg)

        if conversation.prospect is None:
            person_data = (ctx.get('person') or {}).get('data') or {}
            person_id = person_data.get('id')
            prospect = None

            if person_id:
                prospect = ProspectModel.objects.filter(
                    person_id=person_id,
                ).first()

            if prospect is None and source in ('email', 'form', 'web'):
                email = (ctx.get('source_identifier') or '').strip()

                if email:
                    prospect = ProspectModel.objects.filter(
                        email__iexact=email,
                    ).first()

            if prospect is not None:
                conversation.prospect = prospect
                conversation.save(
                    update_fields=['prospect', 'updated_at'],
                )

    else:

        conversation = conversation.first()

        recent_messages = sorted(
            conversation.messages.all(),
            key=lambda m: m.created_at,
            reverse=True,
        )[:settings.CHAT_HISTORY_LIMIT]

        # En web se conserva el historial público completo. Para la IA basta
        # el último resumen y los mensajes posteriores a él.
        if source == 'web':
            for index, row in enumerate(recent_messages):
                if row.message.get('kind') == 'history_summary':
                    recent_messages = recent_messages[:index + 1]
                    break

        all_messages = [
            {"role": msg.message.get(
                "role"), "content": msg.message.get("content")}
            for msg in reversed(recent_messages)
        ]

        faltantes = {
            clave: valor for clave, valor in utms.items()
            if valor and not getattr(conversation, clave)
        }

        if faltantes:
            for clave, valor in faltantes.items():
                setattr(conversation, clave, valor)

            conversation.save(
                update_fields=list(faltantes) + ['updated_at'])

    ctx['_marca_historial'] = (
        ChatHistoryModel.objects
        .filter(conversation=conversation)
        .order_by('-id')
        .values_list('id', flat=True)
        .first()
    ) or 0

    stored_message = dict(message)
    if source == 'web':
        stored_message['display_content'] = web_content.get('message', '')
    ChatHistoryModel.objects.create(
        conversation=conversation, message=stored_message)

    ctx['conversation'] = conversation
    all_messages.append(message)
    ctx['history'] = all_messages

    agent.log_event(
        ctx,
        message=f"Obteniendo conversación",
        data={"actual_message": message['content'],
              "conversation_id": conversation.id,
              "session_id": session_id, "state": conversation.state,
              "utms": {k: v for k, v in utms.items() if v}},
        level=AgentEventModel.LevelEnum.INFO,
    )


def load_knowledge_catalog_step(agent, ctx):
    """
    Recupera la base de conocimiento activa y la guarda en
    ``ctx["knowledge_catalog"]`` con el mismo formato que generaba la
    consulta de Supabase: una sección por tema y, bajo cada una, sus
    documentos activos.

    Solo entran los temas y documentos con ``is_active`` encendido, y
    un tema sin documentos activos se omite por completo, igual que en
    el ``INNER JOIN`` original.
    """

    def _describe(value):
        texto = (value or '').strip()
        return texto or 'Sin descripción'

    documents = (
        DocumentModel.objects
        .filter(is_active=True, topic_id__is_active=True)
        .select_related('topic_id')
        .order_by('topic_id_id', 'id')
    )

    blocks = []
    current_topic = None
    lines = []

    for document in documents:
        topic = document.topic_id

        if current_topic != topic.id:
            if lines:
                blocks.append('\n'.join(lines))

            current_topic = topic.id
            lines = [
                f'-[{topic.id}]: topic({topic.topic_name}): '
                f'{_describe(topic.description)}'
            ]

        lines.append(
            f'  *[{document.id}]: document({document.document_name}): '
            f'{_describe(document.description)}'
        )

    if lines:
        blocks.append('\n'.join(lines))

    ctx['knowledge_catalog'] = '\n\n'.join(blocks)
    agent.log_event(
        ctx,
        message="Base de conocimiento cargada",
        level=AgentEventModel.LevelEnum.INFO,
    )


def search_information_step(agent, ctx):
    return _search_information(agent, ctx, persist_history=True)


def _search_information(agent, ctx, *, persist_history):
    """
    Searches for relevant information based on the prospect's data, internet search, and the knowledge catalog, and
    adds the search results to the context as ``ctx["search_information_response"]``. If no relevant information is
    found, it sets ``ctx["search_information_response"]["success"]`` to False.
    """
    history = ctx.get("history", [])
    catalog = ctx.get("knowledge_catalog")
    conversation = ctx.get("conversation")
    person = ctx.get("person", {})
    source = ctx.get("source", "unknown")
    prompt_args = {}
    message = {"role": "assistant", "content": ""}
    retry_limit = settings.OPENAI_MAX_RETRIES
    attempt = 0
    api_failures = 0
    documents_text = None
    ultimo_error = None
    validated = False
    validator_parsed = None
    model = 'gpt-5.6-luna'

    # historial dentro del bucle de búsqueda, para no contaminar el historial
    # principal con intentos fallidos y retroalimentación
    temp_history = history.copy()

    if source in ['email', 'form', 'web']:
        prompt_args['catalog'] = catalog
        prompt_args['person'] = person.get("data", {})

        if source == 'email':
            prompt_args['email'] = ctx.get("email", {}).get(
                'from', {}).get('email', '')
            search_information_prompt = 'search_information_email_form_prompt'
        elif source == 'form':
            prompt_args['email'] = ctx.get("form", {}).get('email', '')
            search_information_prompt = 'search_information_email_form_prompt'
        elif source == 'web':
            prompt_args['email'] = ctx.get("web", {}).get('email', '')
            search_information_prompt = 'search_information_web_prompt'

        if person.get("db_exists") and (person.get("db_data") or {}).get("enterprise_summary"):
            temp_history.append({
                "role": "assistant",
                "content": "[Resumen de empresa en DB]: ya se tiene resumen de la empresa, no investigar."
            })
        else:
            temp_history.append({
                "role": "assistant",
                "content": "[Resumen de empresa en DB]: no se tiene resumen de la empresa y se debe investigar, sí el correo es empresarial se debe identificar el dominio de la empresa en el correo y buscar, o ver sí viene adjunto en el correo"
            })

    search_information_prompt = ProspectResourceHandler.get(
        search_information_prompt)

    search_information_prompt = Template(
        search_information_prompt).substitute(**prompt_args)

    while attempt < retry_limit and not validated:
        attempt += 1
        try:
            response = agent.response_model(
                system_prompt=search_information_prompt,
                input=temp_history,
                type='parsed',
                model=model,
                text_format=SearchSchema,
            )
            parsed = response.output_parsed

            if parsed.document_ids:
                docs = DocumentModel.objects.filter(
                    id__in=parsed.document_ids, is_active=True
                ).values_list("id", "document_name", "content")

                documents_text = "\n".join(
                    f"[{doc_id}] ({name}): {content}" for doc_id, name, content in docs
                )
            else:
                documents_text = 'No se encontraron documentos relevantes.'

            agent.log_event(
                ctx,
                message="Resultado de búsqueda en la base de conocimiento",
                data={
                    "parsed": parsed.model_dump(mode="json"),
                    "documents_text_preview": documents_text[:500],
                    "documents_len": len(documents_text),
                },
                level=AgentEventModel.LevelEnum.INFO,
            )

            if parsed.enterprise:
                web_result = web_search_tool(
                    query='información de la empresa o negocio {empresa}'.format(
                        empresa=parsed.enterprise)
                )

                resultado_busqueda = {
                    "estado": "informacion_encontrada" if web_result.get("answer") else "sin_informacion",
                    "consulta": web_result.get("query"),
                    "respuesta": web_result.get("answer"),
                }

                documents_text += f"\n\nInformación de empresa en la internet: {resultado_busqueda}"

                agent.log_event(
                    ctx,
                    message="Resultado de búsqueda en la web",
                    data={"resultadoBusqueda": resultado_busqueda},
                    level=AgentEventModel.LevelEnum.INFO,
                )

            temp_history.append({
                "role": "assistant",
                "content": f"Resultado de la búsqueda:\n\n{documents_text}"
            })

            validation_response = agent.response_model(
                system_prompt=ProspectResourceHandler.get(
                    "search_information_validator_prompt"),
                input=temp_history,
                type='parsed',
                model=model,
                text_format=ValidationSchema,
            )
            validator_parsed = validation_response.output_parsed

            if validator_parsed.score == 0:
                agent.log_event(
                    ctx,
                    message=f"Validación fallida en intento {attempt}",
                    level=AgentEventModel.LevelEnum.WARNING,
                    data={
                        'attempt': attempt, 'response': validator_parsed.model_dump(mode='json'), 'model': model},
                )
                temp_history.append({
                    "role": "assistant",
                    "content": f"La validación no fue correcta:\n\n{validator_parsed.reason}"
                })
                # sigue el while: se reintenta generación en la siguiente vuelta
            else:
                validated = True

        except (openai.OpenAIError, ValidationError) as exc:
            # Fallo esperado y recuperable: rate limit, timeout, respuesta mal
            # formada. Se registra y el while reintenta.
            api_failures += 1

            # Se guarda para encadenarla al RuntimeError de abajo: es lo
            # unico que le dice al task si esto se reintenta o no.
            ultimo_error = exc

            agent.log_event(
                ctx,
                message=f"Fallo recuperable en intento {attempt}",
                level=AgentEventModel.LevelEnum.WARNING,
                data={
                    'attempt': attempt,
                    'exception': exc.__class__.__name__,
                    'detail': str(exc),
                    'model': model
                },
            )
        # Cualquier otra excepción NO se atrapa aquí: sube a _run_step,
        # se guarda en AgentStepModel y detiene el run.

    if not validated and api_failures == attempt:
        # Los N intentos murieron por excepción: ninguno llegó a validarse.
        # Esto no es "no hay información", es que el servicio no respondió.
        raise RuntimeError(
            f"OpenAI falló en los {attempt} intentos de búsqueda de información"
        ) from ultimo_error

    search_information_data = {
        "data": None,
        "type": 'parsed',
        "success": None,
    }

    if not validated:
        search_information_data['data'] = "Resultado de la búsqueda: No se encontró información relevante."
        search_information_data['success'] = False
        agent.log_event(
            ctx,
            message="Se agotaron los reintentos sin validación exitosa",
            level=AgentEventModel.LevelEnum.WARNING,
            data={'attempts': attempt, 'api_failures': api_failures},
        )
    else:
        search_information_data['data'] = f"Resultado de la búsqueda: {validator_parsed.reason}"
        search_information_data['success'] = True

    message["content"] = search_information_data['data']

    ctx["search_information_response"] = search_information_data
    ctx['history'].append(message)
    if persist_history:
        ChatHistoryModel.objects.create(conversation=conversation, message=message)

    agent.log_event(
        ctx,
        message="Información obtenida de la búsqueda",
        data={"search_information_response": search_information_data},
        level=AgentEventModel.LevelEnum.INFO,
    )


def generate_prospect_response_step(agent, ctx):
    return _generate_prospect_response(agent, ctx, persist_history=True)


def _generate_prospect_response(agent, ctx, *, persist_history):
    """
    Generates a response to the prospect based on the search results and adds it to the context.

    Si tras agotar los reintentos no hay respuesta, lanza una excepción: no
    existe flujo válido sin ella, porque todos los steps posteriores leen
    ``crm_update`` de ``ctx['response_to_prospect']``.
    """

    source = ctx.get("source")
    history = ctx.get("history")
    conversation = ctx.get("conversation")
    person = ctx.get("person", {})

    substitute = []

    if source in ['email', 'form', 'web']:
        substitute.append('email')
    else:
        substitute.append('phone')

    # filling contact_data with the person's information based on the source
    for field in substitute:
        if field == 'email' and (source == 'email' or source == 'form' or source == 'web'):
            person[field] = person.get('data', {}).get('email')
        elif field == 'phone' and source == 'whatsapp':
            person[field] = person.get('data', {}).get('phone')

    lines = '\n'.join(
        f'- {k}: {v}'
        for k, v in person.get('data', {}).items()
        if v
    )

    prefilled_message = '# Datos que ya tenemos de la persona de odoo'
    prefilled_data = '{prefilled_message}\n{lines}'.format(
        prefilled_message=prefilled_message, lines=lines)

    if source == 'web':
        generate_prospect_response_prompt = ProspectResourceHandler.get(
            "generate_prospect_response_web_prompt")
    else:
        generate_prospect_response_prompt = ProspectResourceHandler.get(
            "generate_prospect_response_prompt")
    if '_agent_configuration' in ctx:
        configuration = ctx['_agent_configuration']
        configured_units = ctx['_agent_units']
    else:
        configuration, configured_units = get_agent_configuration(agent.name)
    generate_prospect_response_prompt = render_response_prompt(
        generate_prospect_response_prompt, configuration, configured_units,
        prefilled_data=prefilled_data,
        search_results=str(ctx['search_information_response']['data']),
    )
    text_format = configured_answer_schema(
        source, tuple(unit['name'] for unit in configured_units))

    retry_limit = settings.OPENAI_MAX_RETRIES
    attempt = 0
    api_failures = 0
    response_to_prospect = None
    ultimo_error = None

    model = 'gpt-5.6-terra'

    enterprise_summary = (person.get("db_data") or {}
                          ).get("enterprise_summary")

    if source == 'form':
        history.append({
            "role": "assistant",
            "content": 'Datos desde el formulario, se debe preguntar el puesto que no viene en el formulario solo si no se se tiene ya el registro del puesto'
        })

    if person.get("db_exists") and enterprise_summary:
        history.append({
            "role": "assistant",
            "content": f"[Resumen de empresa en DB]: {enterprise_summary}."
        })

    suggestions = LearningAnnotationModel.objects.filter(
        agent__key=agent.name,
        is_active=True,
    ).order_by('created_at', 'pk').values_list('suggestion', flat=True)

    annotation_content = '\n\n'.join(
        f'- {suggestion.strip()}'
        for suggestion in suggestions
        if suggestion.strip()
    )

    if annotation_content:
        history.append({
            'role': 'assistant',
            'content': '# Anotaciones\n\nEstas son notas que se han hecho en base a tus conversaciones anteriores y lo que haz aprendido de ellas usalas para mejorar tus conversaciones: ' + annotation_content,
        })

    while attempt < retry_limit and response_to_prospect is None:
        attempt += 1
        try:

            response = agent.response_model(
                system_prompt=generate_prospect_response_prompt,
                input=history,
                type='parsed',
                model=model,
                text_format=text_format,
            )
            response_to_prospect = response.output_parsed.model_dump(
                mode="json")

            history.append({
                "role": "assistant",
                "content": json.dumps(response_to_prospect, ensure_ascii=False)
            })

        except (openai.OpenAIError, ValidationError) as exc:
            api_failures += 1
            ultimo_error = exc
            agent.log_event(
                ctx,
                message=f"Fallo recuperable generando respuesta, intento {attempt}",
                level=AgentEventModel.LevelEnum.WARNING,
                data={
                    'attempt': attempt,
                    'exception': exc.__class__.__name__,
                    'detail': str(exc),
                    'model': model
                },
            )
        # Cualquier otra excepción sube a _run_step y detiene el run.

    if response_to_prospect is None:
        raise RuntimeError(
            f"No se pudo generar la respuesta al prospecto tras {attempt} intentos "
            f"({api_failures} fallos de OpenAI)"
        ) from ultimo_error

    ctx['response_to_prospect'] = response_to_prospect

    if persist_history:
        ChatHistoryModel.objects.create(
            conversation=conversation, message=history[-1]
        )

    agent.log_event(
        ctx,
        message="Respuesta generada para el prospecto",
        data={'response_to_prospect': response_to_prospect},
        level=AgentEventModel.LevelEnum.INFO,
    )


def generate_history_summary_step(agent, ctx):

    history = ctx.get("history", [])
    person = ctx.get("person", {})

    if len(history) >= settings.CHAT_HISTORY_LIMIT:
        conversation = ctx.get("conversation")
        _, configured_units = get_agent_configuration(agent.name)

        summary_prompt = ProspectResourceHandler.get(
            "generate_history_summary_prompt")

        # El resumen es opcional: si falla, el turno sigue sin compactar.
        # Antes cualquier error aquí detenía el run, y como el envío al
        # prospecto es el último step, el correo se quedaba sin respuesta
        # justo cuando el historial llegaba al límite (3er o 4º correo).
        # Sin compactar no se pierde nada: verify_existing_conversation_step
        # ya recorta lo que se carga a CHAT_HISTORY_LIMIT.
        try:
            response = agent.response_model(
                system_prompt=summary_prompt,
                input=history,
                type='parsed',
                model='gpt-5.6-luna',
                text_format=configured_answer_schema(ctx.get('source'), tuple(
                    unit['name'] for unit in configured_units)),
            )
            if response.output_parsed is None:
                raise ValueError('el modelo no devolvió un resumen parseable')
            summary = response.output_parsed.model_dump(mode="json")
        except Exception as exc:
            logger.warning(
                'No se pudo resumir el historial de la conversación %s: %s',
                getattr(conversation, 'id', None), exc)
            agent.log_event(
                ctx,
                message="No se generó el resumen del historial; se continúa sin compactar",
                data={'exception': exc.__class__.__name__, 'detail': str(exc)},
                level=AgentEventModel.LevelEnum.WARNING,
            )
            return

        message = {"role": "assistant", "content": json.dumps(
            summary, ensure_ascii=False), }

        ctx['history'] = [message]

        with transaction.atomic():
            if ctx.get('source') == 'web':
                message['kind'] = 'history_summary'
            else:
                conversation.messages.all().delete()
            ChatHistoryModel.objects.create(
                conversation=conversation, message=message)

        agent.log_event(
            ctx,
            message="Resumen del historial generado",
            data={'history_summary': summary},
            level=AgentEventModel.LevelEnum.INFO,
        )
    else:
        agent.log_event(
            ctx,
            message="No se generó el resumen del historial debido a que no se ha sobrepasado el límite de mensajes para resumir",
            level=AgentEventModel.LevelEnum.INFO,
        )


def register_google_sheet_step(agent, ctx):
    """Encola el registro después de generar la respuesta y su análisis CRM.

    Usa la unidad detectada y completa el contacto con el análisis actual,
    conservando los datos originales recibidos cuando estén disponibles.
    La tarea comprueba el ID en Sheets antes de insertar, en cada intento.
    """
    if getattr(agent, 'mode', AgentModel.ModeEnum.PRODUCTION) != AgentModel.ModeEnum.PRODUCTION:
        ctx['google_sheet_registration'] = {'status': 'skipped', 'reason': 'shadow_mode'}
        return

    conversation = ctx.get('conversation')
    if conversation is None or not conversation.pk:
        raise ValueError('No hay una conversación guardada para registrar en Google Sheets.')

    response = ctx.get('response_to_prospect')
    crm_update = response.get('crm_update') if isinstance(response, dict) else None
    if not isinstance(crm_update, dict):
        raise ValueError('Se necesita la respuesta generada con crm_update para registrar en Google Sheets.')
    contact = crm_update.get('contacto') or {}

    source = ctx.get('source')
    received = ctx.get(source) or {}
    sender = (received.get('from') or {}) if source == 'email' else {}
    email_identifier = ctx.get('source_identifier') if source in ('email', 'form', 'web') else ''
    phone_identifier = ctx.get('source_identifier') if source == 'whatsapp' else ''
    business_unit = (
        crm_update.get('unidad_negocio_detectada')
        or received.get('business_unit') or received.get('unidad_negocio')
        or DEFAULT_BUSINESS_UNIT_NAME
    )
    payload = {
        'business_unit': business_unit,
        'conversation_id': conversation.pk,
        'email': sender.get('email') or received.get('email') or email_identifier or contact.get('correo') or '',
        'name': sender.get('name') or received.get('name') or received.get('nombre') or contact.get('nombre') or '',
        'phone': received.get('phone') or received.get('telefono') or phone_identifier or contact.get('telefono') or '',
        'enterprise': (
            received.get('company_name') or received.get('enterprise')
            or received.get('empresa') or contact.get('empresa') or ''
        ),
        'received_at': conversation.created_at.isoformat(),
    }

    # Incluso un fallo al publicar puede ser ambiguo. El reintento debe
    # conservar este ID y consultar Sheets antes de intentar otra alta.
    ctx['_google_sheets_registration_attempted'] = True
    task = register_google_sheet_lead_task.delay(payload)
    ctx['google_sheet_registration'] = {
        'status': 'queued', 'task_id': task.id,
        'conversation_id': conversation.pk, 'business_unit': business_unit,
    }
    agent.log_event(
        ctx, message='Registro de la conversación en Google Sheets encolado',
        data=ctx['google_sheet_registration'], level=AgentEventModel.LevelEnum.INFO,
    )


def get_odoo_seller_step(agent, ctx):

    person = ctx.get("person", {}).get("data", {})

    # In this moment, the seller is hardcoded to 14, which corresponds to the user "Ignacio Navarro" in Odoo.
    odoo_seller_id = settings.ODOO_SELLER_ID

    crm_update = ctx.get("response_to_prospect", {}).get("crm_update", {})

    if crm_update['escalar_a_humano'] == True:
        pass

    ctx["odoo"] = {"seller_id": odoo_seller_id}

    agent.log_event(
        ctx,
        message=f"Seller ID obtenido: {odoo_seller_id}",
        level=AgentEventModel.LevelEnum.INFO,
    )


def find_or_create_company_step(agent, ctx):
    """
    Finds or creates a company in Odoo based on the prospect's data and adds the company data to the context.
    If the prospect is to be escalated to a human, it checks if the person has a parent company in Odoo.
    If not, it creates a new company in Odoo using the provided company data.
    """

    person = ctx.get("person", {})
    company_name = None
    company_id = None
    crm_update = ctx.get("response_to_prospect", {}).get("crm_update", {})

    if crm_update['escalar_a_humano'] == True:

        if person.get("exists"):
            person = person.get("data", {})
            if person.get("parent_id"):
                company_id = person.get("parent_id")[0]
                company_name = person.get("parent_id")[1]

        if company_id is None:
            company_name = crm_update.get("contacto", {}).get("empresa")
            company_id = find_or_create_company(company_name)

    ctx["odoo"]["company_id"] = company_id
    ctx["odoo"]["company_name"] = company_name

    if company_id and company_name:
        agent.log_event(
            ctx,
            message=f"Empresa encontrada o creada: {company_name} (ID: {company_id})",
            level=AgentEventModel.LevelEnum.INFO,
        )
    else:
        agent.log_event(
            ctx,
            message="No se encontró ni se creó ninguna empresa.",
            level=AgentEventModel.LevelEnum.INFO,
        )


def create_or_update_lead_step(agent, ctx):
    """
    Registra el prospecto en Odoo cuando la conversación se escala a un
    humano, y refleja ese resultado en la base local.

    Solo escribe en Odoo si crm_update['escalar_a_humano'] es True.
    Mientras Sofia siga atendiendo sola, Odoo no se entera de la
    conversación, y el histórico vive únicamente en ConversationModel /
    ChatHistoryModel.

    Cuando sí escala, hace tres cosas:

      1. create_or_update_lead actualiza el lead de esa persona si sigue en
         la etapa "Lead", o crea una oportunidad nueva si no hay o si el
         anterior ya avanzó de etapa. Reutiliza el contacto de res.partner
         si ya existía y solo lo crea si falta.

      2. Refleja el contacto en ProspectModel (ver _sync_prospect),
         indexado por person_id, que es el id de res.partner — NO el de
         crm.lead. Son secuencias independientes: el partner #8256 y el
         lead #8256 son registros distintos.

      3. Marca la conversación como CLOSED y la liga al prospecto: de
         aquí en adelante la atiende un vendedor, no Sofia.

    En web se mantiene abierta mientras el agente siga preguntando y solo
    se cierra al terminar el enrutamiento o escalar a un vendedor.
    Sin escalar no se escribe nada en Odoo: el prospecto queda solo en la
    base local, ligado al partner que encontró el paso 1 o, si no está en
    Odoo, solo al correo.

    Si no hay ni correo ni nombre, create_person no puede crear el
    contacto y person_id llega en None. En ese caso el guardado local se
    omite y la conversación se cierra sin prospecto.
    """

    response_to_prospect = ctx.get("response_to_prospect", {})
    crm_update = response_to_prospect.get("crm_update", {})
    contact = crm_update.get("contacto") or {}
    bant = crm_update.get("bant") or {}
    source = ctx.get("source")
    identifier = ctx.get("source_identifier")
    email = identifier if source in (
        "email", "form", "web") else contact.get("correo")

    if crm_update['escalar_a_humano'] == True:

        # El partner, NO el envoltorio {"data", "exists"}: con el envoltorio,
        # create_or_update_lead no encuentra el "id" y crea un duplicado.
        partner = ctx.get("person", {}).get("data", {})
        seller_id = ctx.get("odoo", {}).get("seller_id", None)
        company_id = ctx.get("odoo", {}).get("company_id", None)

        data = create_or_update_lead(
            crm_update=crm_update,
            source=source,
            person=partner,
            identifier=identifier,
            seller_id=seller_id,
            company_id=company_id,
        )

        # function y phone son varchar(50) en la tabla: _sync_prospect los
        # recorta antes de guardar.
        prospect = _sync_prospect(data.get("person_id"), email, {
            "name": contact.get("nombre"),
            "phone": (contact.get("telefono") or ""),
            "enterprise": contact.get("empresa"),
            "enterprise_summary": crm_update.get("resumen_empresa"),
            "function": bant.get("authority") or "",
        })

        ConversationModel.objects.filter(id=ctx.get("conversation").id).update(
            state=ConversationStateEnum.CLOSED, prospect=prospect)

        agent.log_event(
            ctx,
            message="Prospecto creado o actualizado",
            data=data,
            level=AgentEventModel.LevelEnum.INFO,
        )

    elif crm_update['escalar_a_humano'] == False and source == 'web':

        # Conserva la misma conversación para el siguiente mensaje del widget.
        # Nada de esto escribe en Odoo. El person_id es el partner que el
        # paso 1 encontró con una búsqueda de solo lectura, o None si no
        # está en Odoo; en ese caso _sync_prospect ata la fila al correo.
        web = ctx.get("web", {})
        partner_id = ctx.get("person", {}).get("data", {}).get("id")
        prospect = _sync_prospect(partner_id, email, {
            "name": contact.get("nombre"),
            "phone": (contact.get("telefono") or ""),
            "enterprise": contact.get("empresa"),
            "enterprise_summary": crm_update.get("resumen_empresa"),
            "function": bant.get("authority") or "",
        })

        state = (
            ConversationStateEnum.CLOSED
            if response_to_prospect.get('conversation_state') == 'enrutamiento'
            else ConversationStateEnum.OPEN
        )
        ConversationModel.objects.filter(id=ctx.get("conversation").id).update(
            state=state, prospect=prospect)

        agent.log_event(
            ctx,
            message=f"Web sin escalar: conversación {state}, sin lead en Odoo",
            data={"prospect_id": getattr(prospect, "id", None),
                  "partner_id": partner_id},
            level=AgentEventModel.LevelEnum.INFO,
        )

    else:
        agent.log_event(
            ctx,
            message="No se crea ni actualiza lead/opportunity: no se escaló a humano",
            level=AgentEventModel.LevelEnum.INFO,
        )


def _reportar_envio_fallido(destinatario):
    """Distingue un destinatario omitido de un fallo temporal de SMTP."""
    try:
        validate_email(destinatario)
    except DjangoValidationError as exc:
        raise CorreoOmitidoError(
            'El correo se omitió porque el destinatario no es válido.'
        ) from exc
    raise RuntimeError(f'El SMTP rechazó la respuesta a {destinatario}')


def send_response_to_prospect_step(agent, ctx):
    """
    Sends the generated response to the prospect via the appropriate channel (email, form, or WhatsApp).
    """

    source = ctx.get("source")

    if source in ['email', 'form', 'whatsapp']:

        response_to_prospect = ctx.get("response_to_prospect")
        crm_update = response_to_prospect.get("crm_update", {})
        business_unit = crm_update.get("unidad_negocio_detectada", "")
        response = response_to_prospect["reply_to_customer"]

        quiere_adjunto = (
            crm_update.get("enviar_adjunto", False)
            and business_unit == "KIOSKOS DIGITALES"
        )

        # Un PDF demasiado grande hace rebotar el correo entero: el
        # prospecto se queda sin respuesta. En ese caso se manda el enlace,
        # o nada, pero la respuesta siempre sale.
        send_attachment = quiere_adjunto and casos_de_exito_cabe_como_adjunto()

        if send_attachment:
            response = f"{response}\n\nTe envío un pdf con nuestros casos de éxito."
        elif quiere_adjunto:
            agent.log_event(
                ctx,
                message="PDF de casos de éxito demasiado grande: se envía sin adjunto",
                level=AgentEventModel.LevelEnum.WARNING,
            )
            if settings.CASOS_DE_EXITO_URL:
                response = (
                    f"{response}\n\nAquí puedes ver nuestros casos de éxito: "
                    f"{settings.CASOS_DE_EXITO_URL}"
                )

        if source == 'email':

            email = ctx.get("email")

            enviado = send_email(
                to=email["from"]["email"],
                body=response,
                send_attachment=send_attachment,
                subject="Re: Respuesta a tu consulta de Estrasol",
                in_reply_to=email.get("message_id"),
                references=email.get("references") or email.get("message_id"),
                conversation_id=getattr(ctx.get('conversation'), 'pk', None),
                source=source, kind='response',
            )

            # send_email no levanta: devuelve False. Sin esto el correo se
            # marcaba PROCESSED aunque el SMTP lo hubiera rechazado.
            if not enviado:
                _reportar_envio_fallido(email["from"]["email"])

            agent.log_event(
                ctx,
                message="Correo enviado al prospecto",
                level=AgentEventModel.LevelEnum.INFO,
            )

            # Update the status of the email message to PROCESSED in the database
            EmailMessageModel.objects.filter(
                message_id=email.get("message_id")
            ).update(status=EmailMessageStatusEnum.PROCESSED)

        elif source == 'form':

            form = ctx.get("form")

            enviado = send_email(
                to=form["email"],
                body=response,
                send_attachment=send_attachment,
                subject="Respuesta a tu consulta de Estrasol",
                conversation_id=getattr(ctx.get('conversation'), 'pk', None),
                source=source, kind='response',
            )

            if not enviado:
                _reportar_envio_fallido(form["email"])

            agent.log_event(
                ctx,
                message="Correo enviado al prospecto (contacto por formulario)",
                level=AgentEventModel.LevelEnum.INFO,
            )

        elif source == 'whatsapp':
            pass
