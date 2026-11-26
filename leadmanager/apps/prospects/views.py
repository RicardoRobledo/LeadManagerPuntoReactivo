from django.db.models import Count, Q
import json
import logging
import re
import uuid
from copy import deepcopy
from zoneinfo import ZoneInfo

from asgiref.sync import sync_to_async
from datetime import timedelta
from string import Template
import asyncio

from django.http import Http404, HttpResponseBadRequest, HttpResponseForbidden
from django.views.decorators.csrf import csrf_protect
from django.utils.decorators import method_decorator
from django.db.models import Count, Q, Max
from django.db import transaction, IntegrityError
from django.db.models.functions import TruncDate
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.core.paginator import Paginator
from django.core.exceptions import ValidationError
from django.core.files import File
from django.core.files.storage import default_storage
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.urls import reverse
from django.contrib import messages
from django import forms
from django.views import View
from django.views.decorators.http import require_GET

from leadmanager.apps.documents.knowledge_structure import KNOWLEDGE_STRUCTURE
from leadmanager.apps.documents.models import MAX_TEXT_LENGTH
from leadmanager.apps.automations.models import (
    AgentBusinessUnitModel, AgentChannelAssignment, AgentModel, LearningAnnotationModel,
    TestConversationModel,
)
from leadmanager.apps.automations.forms import AgentPromptForm
from leadmanager.apps.automations.agent_configuration import QUALIFICATION_METHODOLOGIES, sofia_defaults
from leadmanager.apps.automations.agent_channels import ChannelConflict, ChannelRequired, save_agent_channels
from leadmanager.apps.automations.services.learning_annotations import (
    MIN_ANNOTATIONS_TO_SUMMARIZE, condense_active_annotations,
)

from .chatbot import create_response
from .agent_configuration_steps import get_agent_configuration_steps, get_agent_test_cases
from .crm_data import (
    contact_from_crm,
    extract_chat_analysis,
    fill_empty_crm,
    merge_crm_payload,
    parse_json_object,
)
from .date_ranges import bucket_labels, resolve_range
from .exports import (
    build_export_filters,
    build_export_preview,
    build_export_workbook,
    export_filename,
)
from .models import ProspectModel, ConversationModel, ConversationEscalationModel, ChatHistoryModel, ConversationSourceEnum, ConversationStateEnum
from .test_conversations import (
    decorate_test_rows, test_conversation_payload, test_semaforos,
)
from .qualification import (
    DESCARTADOS, NO_ESCALADOS, conversation_semaforos, filter_by_semaforo,
    normalize_semaforo_filter,
)


mx_zi = ZoneInfo('America/Mexico_City')

logger = logging.getLogger(__name__)


def get_semaforo_filter(request):
    selection = normalize_semaforo_filter(
        request.GET.get('semaforo', request.GET.get('state', 'all')),
    )
    if selection is None:
        messages.warning(request, 'El estado seleccionado no es válido. Se muestran todos los estados.')
        return 'all'
    return selection


def get_utm_filter_options():
    """
    Valores distintos de utm_source y utm_campaign que hay en la base,
    para llenar los comboboxes de filtros del dashboard, la tabla de
    conversaciones y estadísticas. Se sacan de toda la tabla, no del
    periodo visible: si no, elegir un rango borraría las opciones.
    """

    def valores(campo):
        return (
            ConversationModel.objects
            .exclude(**{campo: ''})
            .values_list(campo, flat=True)
            .distinct()
            .order_by(campo)
        )

    return valores('utm_source'), valores('utm_campaign')


@login_required
@require_GET
def get_dashboard(request):

    now = timezone.now().astimezone(mx_zi)

    start_day = now.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    end_day = start_day + timedelta(days=1)
    start_year = start_day.replace(month=1, day=1)

    # El rango lo resuelve date_ranges: incluye los periodos de siempre,
    # el mes anterior completo y el rango libre con dos fechas.
    rango = resolve_range(request, default='today')

    interval = rango.interval
    date_from = rango.date_from
    date_to = rango.date_to

    date_filters = rango.filters
    semaforo_filter = get_semaforo_filter(request)

    # El "Total Annual" de las tarjetas siempre es del año en curso, pase
    # lo que pase con el intervalo: es la cifra de contexto. El filtro de
    # atribución (UTM) sí lo acota, para que hable de lo mismo que el
    # resto del tablero.
    year_filters = {
        'created_at__gte': start_year,
        'created_at__lt': end_day,
    }

    # ==========================================
    # FILTRO DE ATRIBUCIÓN (UTMs)
    # ==========================================

    utm_source = request.GET.get('utm_source', '').strip()
    utm_campaign = request.GET.get('utm_campaign', '').strip()

    utm_filters = {}
    if utm_source:
        utm_filters['utm_source'] = utm_source
    if utm_campaign:
        utm_filters['utm_campaign'] = utm_campaign

    # ==========================================
    # CONVERSACIONES DEL INTERVALO SELECCIONADO
    # ==========================================

    evaluation_scope = ConversationModel.objects.filter(**utm_filters).filter(
        Q(**date_filters) | Q(**year_filters),
    )
    semaforos = conversation_semaforos(evaluation_scope)
    conversations = filter_by_semaforo(
        evaluation_scope.filter(**date_filters), semaforo_filter, semaforos,
    )

    email_conversations = conversations.filter(
        source='email',
    )

    form_conversations = conversations.filter(
        source='form',
    )

    # El canal del widget. Antes filtraba source='whatsapp' pese a lo que
    # decia el comentario, asi que el chat web no entraba en ninguna cifra
    # del panel.
    web_conversations = conversations.filter(
        source='web',
    )

    source_by_id = dict(
        conversations.values_list('id', 'source')
    )
    qualified_ids = {pk for pk in source_by_id if semaforos.get(pk) == 'verde'}
    not_qualified_ids = {pk for pk in source_by_id if semaforos.get(pk) in NO_ESCALADOS}

    # ==========================================
    # EMAILS
    # ==========================================

    emails_conversation_general_count = (
        email_conversations.count()
    )

    # Escalados = último semáforo verde, igual que en Conversaciones.
    emails_conversation_qualified_count = sum(
        1 for conversation_id in qualified_ids
        if source_by_id.get(conversation_id) == 'email'
    )

    # ==========================================
    # FORMULARIOS
    # ==========================================

    forms_count = form_conversations.count()

    # Faltaba: la tarjeta "Qualified" de formularios mostraba el total,
    # por eso los dos numeros salian iguales.
    forms_qualified_count = sum(
        1 for conversation_id in qualified_ids
        if source_by_id.get(conversation_id) == 'form'
    )

    # ==========================================
    # CHATS WEB
    # ==========================================

    web_count = web_conversations.count()

    # El mismo criterio se aplica al chatweb.
    web_qualified_count = sum(
        1 for conversation_id in qualified_ids
        if source_by_id.get(conversation_id) == 'web'
    )

    # ==========================================
    # CONVERSACIONES DEL AÑO
    # ==========================================

    conversations_year = filter_by_semaforo(
        evaluation_scope.filter(**year_filters), semaforo_filter, semaforos,
    )

    emails_conversation_count_year = (
        conversations_year
        .filter(source='email')
        .count()
    )

    forms_count_year = (
        conversations_year
        .filter(source='form')
        .count()
    )

    web_count_year = (
        conversations_year
        .filter(source='web')
        .count()
    )

    # ==========================================
    # TOTALES GENERALES
    # ==========================================

    # Se cuentan TODAS las conversaciones del periodo, no la suma de una
    # lista fija de canales. Sumar canal por canal fue justo lo que dejo
    # al chat web fuera del total: nadie lo agrego a la suma cuando se
    # creo ese canal, y lo mismo pasaria con messenger, linkedin o
    # instagram. De paso, asi estas cifras cuadran con las de la pantalla
    # de conversaciones, que tambien cuenta todo.

    general_interactions_count = conversations.count()

    general_qualifieds_count = len(qualified_ids)

    general_interactions_count_year = conversations_year.count()

    # ==========================================
    # CONTEXTO
    # ==========================================

    utm_sources, utm_campaigns = get_utm_filter_options()

    context = {
        'general_interactions_count': (
            general_interactions_count
        ),
        'general_qualifieds_count': (
            general_qualifieds_count
        ),
        'general_not_qualifieds_count': len(not_qualified_ids),
        'general_unrated_count': len(source_by_id) - len(qualified_ids) - len(not_qualified_ids),
        'semaforo': semaforo_filter,
        'general_interactions_count_year': (
            general_interactions_count_year
        ),
        'emails_conversation_count': (
            emails_conversation_general_count
        ),
        'emails_conversation_qualified_count': (
            emails_conversation_qualified_count
        ),
        'emails_conversation_count_year': (
            emails_conversation_count_year
        ),
        'forms_count': forms_count,
        'forms_qualified_count': forms_qualified_count,
        'forms_count_year': forms_count_year,
        'web_count': web_count,
        'web_qualified_count': web_qualified_count,
        'web_count_year': web_count_year,
        'interval': interval,
        'range_label': rango.label,
        'date_from_input': rango.date_from_input,
        'date_to_input': rango.date_to_input,
        'utm_source': utm_source,
        'utm_campaign': utm_campaign,
        'utm_sources': utm_sources,
        'utm_campaigns': utm_campaigns,
        'source_options': ConversationSourceEnum.choices,
    }

    return render(
        request,
        'prospects/dashboard.html',
        context,
    )


def extract_contact(payload):
    """
    Devuelve el contacto que la IA guardó en crm_update.

    Es el mismo dato que muestra el modal al abrir la conversación. No
    se lee de la tabla de prospectos porque esa se llena en otro
    momento del flujo, así que puede estar vacía o quedarse atrás
    mientras la conversación ya tiene nombre y teléfono.

    Returns:
        dict o None. None significa que este mensaje no trae contacto,
        y hay que seguir buscando hacia atrás.
    """
    if not isinstance(payload, dict):
        return None

    crm_update = payload.get('crm_update')

    if not isinstance(crm_update, dict):
        return None

    contact = crm_update.get('contacto')

    if not isinstance(contact, dict):
        return None

    name = contact.get('nombre')
    phone = contact.get('telefono')

    # Un contacto sin nombre ni teléfono no sirve de nada: se ignora
    # para que la búsqueda siga con los mensajes anteriores.
    if not name and not phone:
        return None

    return {
        'name': name,
        'phone': phone,
        'company': contact.get('empresa'),
    }


def extract_escalation_data(escalation):
    """
    Devuelve los datos planos guardados en conversation_content.

    El JSON puede traer los campos en la raíz o dentro de crm_update.
    Se toma crm_update como base y la raíz gana en caso de repetirse,
    igual que hace el endpoint del modal.
    """
    content = escalation.conversation_content or {}

    # Por seguridad, soportar contenido guardado como texto JSON.
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            content = {}

    if not isinstance(content, dict):
        return {}

    data = {}

    crm_update = content.get('crm_update')

    if isinstance(crm_update, dict):
        data.update(crm_update)

    for key, value in content.items():
        if key != 'crm_update':
            data[key] = value

    return data


class ConversationsView(LoginRequiredMixin, View):

    def get(self, request):

        # ==========================================
        # FILTRO DE INTERVALO
        # ==========================================
        # El rango lo resuelve date_ranges, igual que en el dashboard.
        # Acepta el 'day' de antes como sinónimo de 'today', para que los
        # enlaces que alguien tenga guardados sigan funcionando.

        rango = resolve_range(request, default='today')
        interval = rango.interval

        # ==========================================
        # FILTRO DE SEMÁFORO
        # ==========================================

        semaforo_filter = get_semaforo_filter(request)

        # ==========================================
        # FILTRO DE ORIGEN
        # ==========================================

        sources = (
            'all',
            'email',
            'form',
            'web',
            'whatsapp',
            'messenger',
            'linkedin',
            'instagram',
        )

        source = request.GET.get(
            'source',
            'all',
        )

        if source not in sources:
            messages.warning(
                request,
                'El canal seleccionado no es válido. Se muestran todos los canales.',
            )
            source = 'all'

        # ==========================================
        # MODO: PRODUCCIÓN O PRUEBAS
        # ==========================================
        # Producción lista las conversaciones reales. Pruebas lista las
        # conversaciones de los agentes en modo shadow, que viven en su
        # propia tabla (TestConversationModel) y nunca se mezclan con las
        # reales. Ambas usan los mismos filtros.

        mode = request.GET.get('mode', 'production')

        if mode not in ('production', 'shadow_testing'):
            messages.warning(
                request,
                'El modo seleccionado no es válido. Se muestran las conversaciones de producción.',
            )
            mode = 'production'

        # ==========================================
        # FILTRO DE UTM
        # ==========================================

        utm_source = request.GET.get('utm_source', '').strip()
        utm_campaign = request.GET.get('utm_campaign', '').strip()
        traffic_origin = request.GET.get('traffic_origin', '').strip()

        # ==========================================
        # BÚSQUEDA Y PAGINACIÓN
        # ==========================================

        search_prospect = request.GET.get(
            'search_email',
            '',
        ).strip()

        search_message = request.GET.get(
            'search_message',
            '',
        ).strip()

        page_number = request.GET.get(
            'page',
            1,
        )

        # ==========================================
        # RANGO DE FECHAS
        # ==========================================

        date_from = rango.date_from
        date_to = rango.date_to

        # ==========================================
        # CONSULTA PRINCIPAL
        # ==========================================

        is_test = mode == 'shadow_testing'

        if is_test:
            # Los filtros de canal, atribución y prospecto se leen de la
            # conversación real que acompaña cada conversación de pruebas.
            in_range = TestConversationModel.objects.filter(
                created_at__gte=date_from,
                created_at__lt=date_to,
            )
            semaforos = test_semaforos(in_range)
            qs = in_range.select_related('agent', 'conversation__prospect')
            prefix = 'conversation__'
        else:
            in_range = ConversationModel.objects.filter(
                created_at__gte=date_from,
                created_at__lt=date_to,
            )
            semaforos = conversation_semaforos(in_range)
            qs = in_range.select_related('prospect')
            prefix = ''

        qs = filter_by_semaforo(qs, semaforo_filter, semaforos)

        if source != 'all':
            qs = qs.filter(**{f'{prefix}source': source})

        if utm_source:
            qs = qs.filter(**{f'{prefix}utm_source': utm_source})

        if utm_campaign:
            qs = qs.filter(**{f'{prefix}utm_campaign': utm_campaign})

        if traffic_origin:
            qs = qs.filter(**{f'{prefix}traffic_origin': traffic_origin})

        if search_prospect:
            prospect_search = (
                Q(**{f'{prefix}prospect__name__icontains': search_prospect})
                | Q(**{f'{prefix}prospect__phone__icontains': search_prospect})
                | Q(**{f'{prefix}prospect__email__icontains': search_prospect})
                | Q(**{f'{prefix}session_id__icontains': search_prospect})
            )
            if '@' in search_prospect:
                # En web la sesión no es el correo y el prospecto puede
                # no existir todavía. Buscar también en su historial,
                # tanto si el contenido es texto como si es JSON.
                prospect_search |= Q(
                    messages__message__content__icontains=search_prospect,
                )
            qs = qs.filter(prospect_search).distinct()

        if search_message:
            qs = qs.filter(
                messages__message__content__icontains=search_message,
            ).distinct()

        qs = qs.order_by('-created_at', '-pk')

        # ==========================================
        # CONTEOS DEL INTERVALO
        # ==========================================
        # Las tarjetas cuentan el mismo conjunto filtrado que la tabla y
        # que los otros tableros cuando comparten periodo y filtros.
        filtered_ids = list(qs.values_list('pk', flat=True))
        conversations_count = len(filtered_ids)
        escalated_count = sum(semaforos.get(pk) == 'verde' for pk in filtered_ids)
        not_escalated_count = sum(
            semaforos.get(pk) in NO_ESCALADOS for pk in filtered_ids
        )
        in_progress_count = sum(semaforos.get(pk) == 'amarillo' for pk in filtered_ids)
        discarded_count = sum(semaforos.get(pk) in DESCARTADOS for pk in filtered_ids)
        unrated_count = conversations_count - escalated_count - not_escalated_count

        # ==========================================
        # PAGINACIÓN
        # ==========================================

        paginator = Paginator(
            qs,
            15,
        )

        page_obj = paginator.get_page(
            page_number,
        )

        # ==========================================
        # ÚLTIMO MENSAJE DE CADA CONVERSACIÓN
        # ==========================================
        # Se agrupa por la clave primaria de la conversación y no por
        # session_id: varias conversaciones pueden compartir el mismo
        # session_id y, de hacerlo por ese campo, todas terminarían
        # mostrando el último mensaje de una sola.

        # Las conversaciones de pruebas arman sus filas desde su propia
        # tabla de mensajes; lo que sigue es solo para las reales.
        if is_test:
            decorate_test_rows(page_obj, semaforos)
            conversation_ids = []
        else:
            conversation_ids = [
                row.pk
                for row in page_obj
            ]

        last_msgs = {}
        contacts = {}

        if conversation_ids:
            message_rows = (
                ChatHistoryModel.objects
                .filter(
                    conversation_id__in=conversation_ids,
                )
                .order_by(
                    'conversation_id',
                    '-id',
                )
            )

            for row in message_rows:
                cid = row.conversation_id

                # Los registros vienen ordenados del más
                # reciente al más antiguo. Una sesión se deja
                # de recorrer cuando ya se tienen las dos
                # cosas que se buscan.
                if cid in last_msgs and cid in contacts:
                    continue

                message = row.message or {}

                role = message.get('role')

                content = message.get('content', '')

                parsed_content = parse_json_object(content) if role == 'assistant' else {}

                # El contacto lo escribe la IA junto con su
                # respuesta, así que el último mensaje de la
                # sesión puede ser del cliente y no traerlo:
                # hay que seguir hacia atrás hasta encontrarlo.
                if cid not in contacts:
                    contact = extract_contact(parsed_content)

                    if contact:
                        contacts[cid] = contact

                # Mostrar la misma respuesta que en el historial del modal,
                # tanto si content es un objeto como si es texto JSON.
                # Las actualizaciones internas siguen aportando el contacto,
                # pero no deben reemplazar al último mensaje visible.
                if role == 'assistant':
                    reply_keys = (
                        'reply_to_customer_esp',
                        'response_to_customer_esp',
                        'reply_to_customer',
                    )
                    if any(key in parsed_content for key in reply_keys):
                        content = next((
                            parsed_content[key] for key in reply_keys
                            if isinstance(parsed_content.get(key), str)
                            and parsed_content[key].strip()
                        ), None)
                        if content is None:
                            continue
                    elif isinstance(parsed_content.get('crm_update'), dict):
                        continue

                if cid not in last_msgs:
                    last_msgs[cid] = {
                        'role': role,
                        'content': content,
                    }

        # Agregar temporalmente a cada conversación el último mensaje y
        # el contacto, con los nombres que espera la plantilla.
        for row in ([] if is_test else page_obj):
            cid = row.pk
            row.semaforo = semaforos.get(cid, '')

            message_info = last_msgs.get(cid, {})

            row.last_role = message_info.get('role')

            row.last_message = message_info.get('content', '')

            contact = contacts.get(cid, {})

            prospect = row.prospect

            row.contact_name = (
                contact.get('name')
                or (prospect.name if prospect else None)
            )

            row.contact_phone = (
                contact.get('phone')
                or (prospect.phone if prospect else None)
            )

        utm_sources, utm_campaigns = get_utm_filter_options()

        traffic_origins = (
            ConversationModel.objects
            .exclude(traffic_origin='')
            .values_list('traffic_origin', flat=True)
            .distinct()
            .order_by('traffic_origin')
        )

        # ==========================================
        # CONTEXTO
        # ==========================================

        context = {
            'conversations_count': conversations_count,
            'conversation_agent_name': (
                AgentModel.objects.filter(key='prospector')
                .values_list('name', flat=True).first() or 'Sofía'
            ),
            'escalated_count': escalated_count,
            'not_escalated_count': not_escalated_count,
            'in_progress_count': in_progress_count,
            'discarded_count': discarded_count,
            'unrated_count': unrated_count,
            'interval': interval,
            'range_label': rango.label,
            'date_from_input': rango.date_from_input,
            'date_to_input': rango.date_to_input,
            'semaforo': semaforo_filter,
            'source': source,
            'mode': mode,
            'search_prospect': search_prospect,
            'search_message': search_message,
            'page_obj': page_obj,
            'page': page_obj.number,
            'num_pages': paginator.num_pages,
            'utm_source': utm_source,
            'utm_campaign': utm_campaign,
            'traffic_origin': traffic_origin,
            'utm_sources': utm_sources,
            'utm_campaigns': utm_campaigns,
            'traffic_origins': traffic_origins,
            'has_previous': page_obj.has_previous(),
            'has_next': page_obj.has_next(),
            'previous_page_number': (
                page_obj.previous_page_number()
                if page_obj.has_previous()
                else 1
            ),
            'next_page_number': (
                page_obj.next_page_number()
                if page_obj.has_next()
                else paginator.num_pages
            ),
            'request': request,
        }

        return render(
            request,
            'prospects/conversations.html',
            context,
        )


CHAT_SOURCES = [
    ConversationSourceEnum.WEB,
    ConversationSourceEnum.MESSENGER,
    ConversationSourceEnum.WHATSAPP,
    ConversationSourceEnum.INSTAGRAM,
]


class ConversationView(LoginRequiredMixin, View):

    def get(self, request, conversation_id):

        # ==========================================
        # CONVERSACIÓN PRINCIPAL
        # ==========================================

        conversation_record = (
            ConversationModel.objects
            .filter(pk=conversation_id)
            .first()
        )

        if not conversation_record:
            return JsonResponse(
                {'error': 'No se encontró la conversación'},
                status=404,
            )

        # ==========================================
        # HISTORIAL DE MENSAJES
        # ==========================================

        message_history = (
            ChatHistoryModel.objects
            .filter(conversation_id=conversation_id)
            .order_by('created_at', 'id')
        )

        conversation_data = []

        for history_item in message_history:
            message = history_item.message or {}

            role = message.get('role')
            content = message.get('content', '')

            if role == 'assistant':
                parsed_content = parse_json_object(content)

                # El panel muestra la versión española, independientemente
                # del idioma que se haya enviado al prospecto.
                reply_keys = (
                    'reply_to_customer_esp',
                    'response_to_customer_esp',
                    'reply_to_customer',
                )
                if any(key in parsed_content for key in reply_keys):
                    reply = next((
                        parsed_content[key] for key in reply_keys
                        if isinstance(parsed_content.get(key), str)
                        and parsed_content[key].strip()
                    ), None)
                    if reply is None:
                        continue
                    content = reply
                elif isinstance(parsed_content.get('crm_update'), dict):
                    # Chatweb guarda solo el análisis interno. Sus datos
                    # alimentan las tarjetas CRM, no una burbuja del chat.
                    # El historial original se conserva para ese análisis.
                    continue

            conversation_data.append({
                'type': role,
                'content': content,
                'created_at': history_item.created_at,
                'id': history_item.id,
            })

        # ==========================================
        # ESCALAMIENTO A MOSTRAR
        # ==========================================
        # En la vista de escalamientos cada fila es un escalamiento
        # distinto de la misma sesión, así que el id llega por query
        # string. Sin ese parámetro se cae al más reciente, que es lo
        # que necesita la vista normal de conversaciones.

        escalations = (
            conversation_record.escalations
            .select_related('seller')
        )

        escalation_id = request.GET.get('escalation')

        if escalation_id:
            selected_escalation = escalations.filter(
                pk=escalation_id,
            ).first()

            if not selected_escalation:
                return JsonResponse(
                    {'error': 'No se encontró la derivación a una persona'},
                    status=404,
                )
        else:
            selected_escalation = escalations.order_by(
                '-created_at',
                '-id',
            ).first()

        crm = None
        is_qualified = selected_escalation is not None

        # ==========================================
        # DATOS CRM DEL ESCALAMIENTO
        # ==========================================

        if selected_escalation:
            # crm_data concentra el parseo defensivo (string JSON,
            # crm_update anidado, raíz que gana) que antes vivía aquí.
            crm = merge_crm_payload(
                selected_escalation.conversation_content
            )

            # Garantizar que el panel identifique la conversación
            # como calificada.
            crm.setdefault(
                'conversation_state',
                'qualified',
            )

            crm['is_qualified'] = True
            crm['escalation_id'] = selected_escalation.id
            crm['escalated_at'] = (
                selected_escalation.created_at
            )

            # ==========================================
            # DATOS DEL VENDEDOR
            # ==========================================

            seller = selected_escalation.seller

            if seller:
                crm['seller_id'] = str(seller.id)
                crm['seller_name'] = seller.name
                crm['seller_email'] = seller.email
                crm['seller_phone'] = seller.phone
            else:
                crm['seller_id'] = None
                crm['seller_name'] = None
                crm['seller_email'] = None
                crm['seller_phone'] = None

        # ==========================================
        # SI FALTAN DATOS, EXTRAERLOS DEL
        # ÚLTIMO MENSAJE DE IA (MERGE GENÉRICO)
        # ==========================================
        # Solo aplica cuando no se pidió un escalamiento concreto. Al
        # abrir una fila de la tabla de escalamientos se debe mostrar
        # exactamente su conversation_content: mezclarlo con el último
        # mensaje de la sesión traía datos de otro momento.

        if not escalation_id:
            # Buscar hacia atrás el último mensaje del asistente que
            # realmente contenga un crm_update. Una conversación no
            # escalada guarda el análisis estructurado dentro del
            # historial.
            recent_ai_messages = (
                ChatHistoryModel.objects
                .filter(
                    conversation_id=conversation_id,
                    message__role='assistant',
                )
                .order_by('-created_at', '-id')[:20]
            )

            for ai_history in recent_ai_messages:
                crm_update, parsed = extract_chat_analysis(
                    ai_history.message
                )

                if not crm_update:
                    continue

                if crm is None:
                    crm = {}

                # Rellenar TODOS los campos que falten
                # (contacto, resumen, semáforo, bant, etc.).
                fill_empty_crm(crm, crm_update)

                if not crm.get('conversation_state'):
                    crm['conversation_state'] = parsed.get(
                        'conversation_state'
                    )

                if selected_escalation is None:
                    crm['is_qualified'] = False

                break

        # ==========================================
        # CONTACTO PARA EL PANEL
        # ==========================================
        # El escalamiento guarda el contacto en campos planos
        # (contact_name, partner_name, email_from), mientras que el
        # panel espera un objeto "contacto". Antes se veía lleno solo
        # porque el merge lo tomaba del último mensaje de la sesión.

        crm = contact_from_crm(crm)

        # ==========================================
        # ATRIBUCIÓN (UTMs)
        # ==========================================
        # Va aparte del análisis CRM: la atribución es de la conversación
        # completa, no del escalamiento, y se debe ver aunque la IA no
        # haya dejado análisis.

        attribution = {
            'source': conversation_record.source or '',
            'utm_source': conversation_record.utm_source,
            'utm_medium': conversation_record.utm_medium,
            'utm_campaign': conversation_record.utm_campaign,
            'utm_content': conversation_record.utm_content,
            'utm_term': conversation_record.utm_term,
            'traffic_origin': conversation_record.traffic_origin,
        }

        # ==========================================
        # RESPUESTA
        # ==========================================

        return JsonResponse(
            {
                'conversation': conversation_data,
                'crm': crm,
                'is_qualified': is_qualified,
                'attribution': attribution,
            },
            status=200,
        )


CHAT_SOURCES = [
    ConversationSourceEnum.WEB,
    ConversationSourceEnum.MESSENGER,
    ConversationSourceEnum.WHATSAPP,
    ConversationSourceEnum.INSTAGRAM,
]


class TestConversationView(LoginRequiredMixin, View):
    """
    El modal de una conversación de pruebas: los mensajes del prospecto con
    las respuestas que dio el agente de pruebas, leídos de su propia tabla.
    """

    def get(self, request, test_conversation_id):
        test_conversation = (
            TestConversationModel.objects
            .select_related('agent', 'conversation')
            .filter(pk=test_conversation_id)
            .first()
        )

        if test_conversation is None:
            return JsonResponse(
                {'error': 'No se encontró la conversación de pruebas'},
                status=404,
            )

        return JsonResponse(test_conversation_payload(test_conversation), status=200)


class StatisticsView(LoginRequiredMixin, View):

    def get(self, request):
        # Antes estaba fijo a "mes actual contra mes anterior". Ahora el
        # periodo lo elige quien mira, y la comparación se hace contra el
        # periodo inmediatamente anterior de la misma duración: si pides
        # del 1 al 31 de octubre, compara contra los 31 días previos.
        rango = resolve_range(request, default='month')
        anterior = rango.previous
        semaforo_filter = get_semaforo_filter(request)

        # =========================
        # FILTRO DE ATRIBUCIÓN (UTMs)
        # =========================
        # Acota el tablero entero a una fuente o campaña. La comparación
        # contra el periodo anterior usa el mismo filtro: si no, se
        # estaría comparando la campaña contra todo el tráfico.

        utm_source = request.GET.get('utm_source', '').strip()
        utm_campaign = request.GET.get('utm_campaign', '').strip()

        utm_q = Q()
        if utm_source:
            utm_q &= Q(utm_source=utm_source)
        if utm_campaign:
            utm_q &= Q(utm_campaign=utm_campaign)

        cur = Q(created_at__gte=rango.date_from,
                created_at__lt=rango.date_to) & utm_q
        last = Q(created_at__gte=anterior.date_from,
                 created_at__lt=anterior.date_to) & utm_q

        SOURCE_VALUES = [s.value for s in ConversationSourceEnum]

        # =========================
        # TOTALES POR SOURCE (1 query)
        # =========================
        aggregations = {}
        for value in SOURCE_VALUES:
            aggregations[f'{value}_current'] = Count(
                'id', filter=cur & Q(source=value))
            aggregations[f'{value}_last'] = Count(
                'id', filter=last & Q(source=value))

        aggregations['chats_current'] = Count(
            'id', filter=cur & Q(source__in=CHAT_SOURCES))
        aggregations['chats_last'] = Count(
            'id', filter=last & Q(source__in=CHAT_SOURCES))
        aggregations['unknown_current'] = Count(
            'id', filter=cur & Q(source__isnull=True))
        aggregations['unknown_last'] = Count(
            'id', filter=last & Q(source__isnull=True))
        aggregations['total_current'] = Count('id', filter=cur)
        aggregations['total_last'] = Count('id', filter=last)

        evaluation_scope = ConversationModel.objects.filter(
            created_at__gte=anterior.date_from,
            created_at__lt=rango.date_to,
        ).filter(utm_q)
        semaforos = conversation_semaforos(evaluation_scope)
        filtered_scope = filter_by_semaforo(evaluation_scope, semaforo_filter, semaforos)
        totals = filtered_scope.aggregate(**aggregations)

        # =========================
        # ESTADOS DE PROSPECCIÓN SEGÚN EL ÚLTIMO SEMÁFORO
        # =========================
        conv_cur = filtered_scope.filter(cur)

        source_by_id = dict(conv_cur.values_list('id', 'source'))
        qualified_ids = {pk for pk in source_by_id if semaforos.get(pk) == 'verde'}
        in_progress_ids = {pk for pk in source_by_id if semaforos.get(pk) == 'amarillo'}
        discarded_ids = {pk for pk in source_by_id if semaforos.get(pk) in DESCARTADOS}
        not_qualified_ids = {pk for pk in source_by_id if semaforos.get(pk) in NO_ESCALADOS}

        qualified_count = len(qualified_ids)
        in_progress_count = len(in_progress_ids)
        discarded_count = len(discarded_ids)

        not_qualified_count = len(not_qualified_ids)
        unrated_count = len(source_by_id) - qualified_count - not_qualified_count

        qualified_rate = (
            round(qualified_count / totals['total_current'] * 100, 1)
            if totals['total_current'] else 0
        )

        por_fuente = {}

        for conversation_id, source in source_by_id.items():
            if source is None:
                continue

            counts = por_fuente.setdefault(
                source, {
                    'qualified': 0, 'not_qualified': 0,
                    'in_progress': 0, 'discarded': 0, 'unrated': 0,
                })

            if conversation_id in qualified_ids:
                counts['qualified'] += 1
            elif conversation_id in not_qualified_ids:
                counts['not_qualified'] += 1
                counts['in_progress' if conversation_id in in_progress_ids else 'discarded'] += 1
            else:
                counts['unrated'] += 1

        qualification_by_source = [
            {
                'source': source,
                'qualified': counts['qualified'],
                'not_qualified': counts['not_qualified'],
                'in_progress': counts['in_progress'],
                'discarded': counts['discarded'],
                'unrated': counts['unrated'],
            }
            for source, counts in sorted(por_fuente.items())
        ]

        # =========================
        # ATRIBUCIÓN (UTMs) DEL PERIODO
        # =========================
        # Qué campaña o canal trae más leads. Se agrupa por el rango
        # elegido y se excluyen los vacíos: un correo entrante no tiene
        # navegador ni URL de aterrizaje, así que contarlo como "(vacío)"
        # solo mete ruido.

        SIN_ATRIBUCION = Q(
            utm_source='',
            utm_medium='',
            utm_campaign='',
            utm_content='',
            utm_term='',
            traffic_origin='',
        )

        attributed_count = conv_cur.exclude(SIN_ATRIBUCION).count()
        unattributed_count = conv_cur.count() - attributed_count

        total_atribuible = totals['total_current'] or 0

        def _con_porcentaje(rows, llave):
            return [
                {
                    'label': fila[llave],
                    'total': fila['total'],
                    'percent': (
                        round(fila['total'] / total_atribuible * 100, 1)
                        if total_atribuible else 0
                    ),
                }
                for fila in rows
            ]

        utm_by_source = _con_porcentaje(
            conv_cur
            .exclude(utm_source='')
            .values('utm_source')
            .annotate(total=Count('id'))
            .order_by('-total', 'utm_source')[:10],
            'utm_source',
        )

        utm_by_campaign = _con_porcentaje(
            conv_cur
            .exclude(utm_campaign='')
            .values('utm_campaign')
            .annotate(total=Count('id'))
            .order_by('-total', 'utm_campaign')[:10],
            'utm_campaign',
        )

        traffic_by_origin = _con_porcentaje(
            conv_cur
            .exclude(traffic_origin='')
            .values('traffic_origin')
            .annotate(total=Count('id'))
            .order_by('-total', 'traffic_origin')[:10],
            'traffic_origin',
        )

        # =========================
        # GRÁFICAS DEL PERIODO (1 query)
        # =========================
        # Siempre se agrupa por día en la consulta; juntarlos por mes,
        # cuando el rango es largo, se hace después en Python. Así la
        # base hace una sola pasada sin importar el periodo elegido.

        day_annotations = {
            value: Count('id', filter=Q(source=value))
            for value in SOURCE_VALUES
        }
        day_annotations['chats'] = Count(
            'id', filter=Q(source__in=CHAT_SOURCES))
        day_annotations['total'] = Count('id')

        rows = (
            conv_cur
            .annotate(day=TruncDate('created_at', tzinfo=mx_zi))
            .values('day')
            .annotate(**day_annotations)
        )
        by_day = {r['day']: r for r in rows}

        # Un rango de una semana se grafica por día; uno de un año, por
        # mes. Con 365 barras no se entiende nada.
        _, cortes = bucket_labels(rango)

        def serie(key):
            datos = []

            for inicio, fin, etiqueta in cortes:
                suma = 0
                dia = inicio.date()
                tope = fin.date()

                while dia < tope:
                    suma += by_day.get(dia, {}).get(key, 0)
                    dia += timedelta(days=1)

                datos.append({'day': etiqueta, 'total': suma})

            return datos

        # =========================
        # CONTEXT
        # =========================
        utm_sources, utm_campaigns = get_utm_filter_options()

        context = {
            **totals,
            'qualified_count': qualified_count,
            'not_qualified_count': not_qualified_count,
            'in_progress_count': in_progress_count,
            'discarded_count': discarded_count,
            'unrated_count': unrated_count,
            'semaforo': semaforo_filter,
            'qualified_rate': qualified_rate,
            'qualification_by_source': qualification_by_source,
            'utm_by_source': utm_by_source,
            'utm_by_campaign': utm_by_campaign,
            'traffic_by_origin': traffic_by_origin,
            'attributed_count': attributed_count,
            'unattributed_count': unattributed_count,
            'chats_by_day': serie('chats'),
            'total_by_day': serie('total'),
            'interval': rango.interval,
            'range_label': rango.label,
            'previous_label': anterior.label,
            'date_from_input': rango.date_from_input,
            'date_to_input': rango.date_to_input,
            'utm_source': utm_source,
            'utm_campaign': utm_campaign,
            'utm_sources': utm_sources,
            'utm_campaigns': utm_campaigns,
        }

        for value in SOURCE_VALUES:
            context[f'{value}_by_day'] = serie(value)

        return render(request, 'prospects/statistics.html', context)


class AgentsView(LoginRequiredMixin, View):

    def get(self, request):
        agents = AgentModel.objects.all().defer('prompt').prefetch_related('channel_assignments')
        search = request.GET.get('search', '').strip()[:100]
        category = request.GET.get('category', '')
        if category not in AgentModel.CategoryEnum.values:
            category = ''
        if search:
            agents = agents.filter(Q(name__icontains=search) | Q(role__icontains=search))
        if category:
            agents = agents.filter(category=category)
        response = render(request, 'prospects/agents.html', {
            'agents': agents,
            'search': search,
            'category': category,
            'category_choices': AgentModel.CategoryEnum.choices,
            'channel_conflict': request.session.pop('channel_conflict', None),
        })
        response['Cache-Control'] = 'no-store'
        return response


class AgentCreateForm(forms.Form):
    name = forms.CharField(label='Nombre', max_length=100)


class AgentCreateView(LoginRequiredMixin, View):
    def get(self, request):
        return render(request, 'prospects/agent_create.html', {'form': AgentCreateForm()})

    @transaction.atomic
    def post(self, request):
        form = AgentCreateForm(request.POST)
        if not form.is_valid():
            return render(request, 'prospects/agent_create.html', {'form': form}, status=400)
        name = form.cleaned_data['name']
        defaults = sofia_defaults()
        prompt = defaults['prompt']
        prompt['name'] = name
        prompt['business_units'] = []
        agent = AgentModel.objects.create(
            key=f'agent-{uuid.uuid4().hex}', name=name, is_active=False,
            role='Agente de prospección comercial',
            description=(
                'Atiende consultas, identifica necesidades y califica prospectos '
                'para canalizarlos con el especialista adecuado.'
            ),
            category=AgentModel.CategoryEnum.PROSPECTING, channels=[], prompt=prompt,
        )
        # Las unidades se copian de los valores base del proyecto
        # (agent_prompt_defaults.json), no de las que tenga hoy otro agente:
        # así no dependen de lo que alguien haya editado o borrado en Sofía.
        # Cada copia es propia del agente nuevo y se edita por separado.
        # NO_DETERMINADO ya la creó la señal post_save del agente.
        for unit in defaults['business_units']:
            AgentBusinessUnitModel.objects.get_or_create(
                agent=agent, name=unit['name'],
                defaults={'description': unit['description']},
            )
        return redirect('app_prospects:agent_configuration', agent_key=agent.key)


class AgentDuplicateView(LoginRequiredMixin, View):
    """
    Copia un agente de prospección para editarlo sin tocar el original.

    La copia nace inactiva y en producción: no atiende ningún canal ni corre
    como agente de pruebas hasta que alguien la active o la ponga en modo de
    pruebas desde su configuración. Lleva su propia copia del prompt, las
    unidades de negocio, los canales seleccionados (sin reservarlos), la
    opción de aprendizaje y la imagen. No lleva historial de ejecuciones ni
    anotaciones de aprendizaje.
    """

    @transaction.atomic
    def post(self, request, agent_key):
        original = get_object_or_404(
            AgentModel.objects.filter(category=AgentModel.CategoryEnum.PROSPECTING),
            key=agent_key,
        )
        name = f'Copia de {original.name}'[:100]
        prompt = deepcopy(original.prompt)
        prompt['name'] = name
        prompt['business_units'] = []

        duplicate = AgentModel.objects.create(
            key=f'agent-{uuid.uuid4().hex}', name=name,
            is_active=False, mode=AgentModel.ModeEnum.PRODUCTION,
            role=original.role, description=original.description,
            category=original.category, channels=list(original.channels or []),
            learning_enabled=original.learning_enabled, prompt=prompt,
        )

        # Unidades propias: editar las de la copia no cambia las del original.
        # NO_DETERMINADO ya la creó la señal post_save del agente.
        for unit in original.business_units.all():
            AgentBusinessUnitModel.objects.get_or_create(
                agent=duplicate, name=unit.name,
                defaults={'description': unit.description},
            )

        # Los canales quedan seleccionados pero inactivos: activar la copia
        # pasa por la validación de conflictos de siempre.
        AgentChannelAssignment.objects.bulk_create([
            AgentChannelAssignment(agent=duplicate, channel=channel, active=False)
            for channel in original.channel_assignments.values_list('channel', flat=True)
        ])

        # La imagen se copia a un archivo propio: al borrar uno de los dos
        # agentes se borra su archivo, y no debe llevarse el del otro.
        match = _AVATAR_PATH.fullmatch(original.avatar or '')
        if match:
            try:
                with default_storage.open(original.avatar, 'rb') as image:
                    duplicate.avatar = default_storage.save(
                        f'agent-avatars/{uuid.uuid4().hex}.{match.group(1)}', File(image),
                    )
            except FileNotFoundError:
                duplicate.avatar = ''
            else:
                duplicate.save(update_fields=['avatar', 'updated_at'])

        messages.success(
            request,
            f'Se creó «{duplicate.name}». Está inactiva y en producción: '
            'revisa su configuración antes de activarla o ponerla en pruebas.',
        )
        return redirect('app_prospects:agent_configuration', agent_key=duplicate.key)


class AgentStatusView(LoginRequiredMixin, View):
    def post(self, request, agent_key):
        agent = get_object_or_404(AgentModel, key=agent_key)
        value = request.POST.get('is_active')
        if value not in ('0', '1'):
            return HttpResponseForbidden('Estado no válido.')
        try:
            save_agent_channels(agent.pk, active=value == '1')
        except ChannelConflict as exc:
            request.session['channel_conflict'] = {'items': exc.conflicts, 'message': str(exc)}
        except ChannelRequired as exc:
            request.session['channel_conflict'] = {'title': 'Selecciona un canal', 'message': str(exc), 'items': []}
        return redirect('app_prospects:agents')


_AVATAR_PATH = re.compile(r'^agent-avatars/[0-9a-f]{32}\.(png|jpg|webp)$')


class AgentAvatarView(LoginRequiredMixin, View):
    def post(self, request, agent_key):
        agent = get_object_or_404(AgentModel, key=agent_key)
        action = request.POST.get('action')
        old_avatar = agent.avatar
        if action == 'remove':
            agent.avatar = ''
        elif action == 'upload':
            upload = request.FILES.get('avatar')
            if not upload or upload.size > 2 * 1024 * 1024:
                messages.error(request, 'Selecciona una imagen de hasta 2 MB.')
                return redirect('app_prospects:agent_configuration', agent_key=agent.key)
            header = upload.read(12)
            upload.seek(0)
            if header.startswith(b'\x89PNG\r\n\x1a\n'):
                extension = 'png'
            elif header.startswith(b'\xff\xd8\xff'):
                extension = 'jpg'
            elif header[:4] == b'RIFF' and header[8:12] == b'WEBP':
                extension = 'webp'
            else:
                messages.error(request, 'Usa una imagen PNG, JPG o WebP válida.')
                return redirect('app_prospects:agent_configuration', agent_key=agent.key)
            agent.avatar = default_storage.save(f'agent-avatars/{uuid.uuid4().hex}.{extension}', upload)
        else:
            return HttpResponseForbidden('Acción no válida.')
        agent.save(update_fields=['avatar', 'updated_at'])
        if old_avatar != agent.avatar and _AVATAR_PATH.fullmatch(old_avatar or ''):
            transaction.on_commit(lambda: default_storage.delete(old_avatar))
        messages.success(request, 'Imagen del agente actualizada.')
        return redirect('app_prospects:agent_configuration', agent_key=agent.key)


class AgentAvatarFileView(LoginRequiredMixin, View):
    def get(self, request, agent_key):
        agent = get_object_or_404(AgentModel, key=agent_key)
        match = _AVATAR_PATH.fullmatch(agent.avatar or '')
        if not match:
            raise Http404
        try:
            file = default_storage.open(agent.avatar, 'rb')
        except FileNotFoundError:
            raise Http404
        content_type = {'png': 'image/png', 'jpg': 'image/jpeg', 'webp': 'image/webp'}[match.group(1)]
        response = FileResponse(file, content_type=content_type)
        response['X-Content-Type-Options'] = 'nosniff'
        response['Cache-Control'] = 'private, no-cache'
        return response


class AgentChannelsForm(forms.Form):
    channels = forms.MultipleChoiceField(
        label='Canales', choices=ConversationSourceEnum.choices, required=False,
        widget=forms.CheckboxSelectMultiple(attrs={'class': 'peer sr-only'}),
    )


class AgentChannelsView(LoginRequiredMixin, View):
    def _agent(self, agent_key):
        return get_object_or_404(AgentModel.objects.exclude(key='conversation_analyzer'), key=agent_key)

    def get(self, request, agent_key):
        agent = self._agent(agent_key)
        form = AgentChannelsForm(initial={
            'channels': list(agent.channel_assignments.values_list('channel', flat=True)),
        })
        return render(request, 'prospects/agent_channels.html', {'agent': agent, 'form': form})

    def post(self, request, agent_key):
        agent = self._agent(agent_key)
        form = AgentChannelsForm(request.POST)
        channel_conflict = None
        if form.is_valid():
            try:
                save_agent_channels(agent.pk, channels=form.cleaned_data['channels'])
            except ChannelConflict as exc:
                channel_conflict = {'items': exc.conflicts, 'message': str(exc)}
            except ChannelRequired as exc:
                channel_conflict = {'title': 'Selecciona un canal', 'message': str(exc), 'items': []}
            else:
                messages.success(request, f'Canales de {agent.name} guardados.')
                return redirect('app_prospects:agents')
        return render(request, 'prospects/agent_channels.html', {
            'agent': agent, 'form': form, 'channel_conflict': channel_conflict,
        }, status=400)


class AgentDeleteView(LoginRequiredMixin, View):
    def get(self, request, agent_key):
        agent = get_object_or_404(AgentModel, key=agent_key)
        if agent.key in ('prospector', 'conversation_analyzer'):
            return HttpResponseForbidden('Los agentes integrados no se pueden eliminar.')
        return render(request, 'prospects/agent_delete.html', {'agent': agent})

    def post(self, request, agent_key):
        agent = get_object_or_404(AgentModel, key=agent_key)
        if agent.key in ('prospector', 'conversation_analyzer'):
            return HttpResponseForbidden('Los agentes integrados no se pueden eliminar.')
        old_avatar = agent.avatar
        agent.delete()
        if _AVATAR_PATH.fullmatch(old_avatar or ''):
            transaction.on_commit(lambda: default_storage.delete(old_avatar))
        return redirect('app_prospects:agents')


class AgentAnalysesView(LoginRequiredMixin, View):

    def get(self, request, agent_key):
        agent = get_object_or_404(AgentModel, key=agent_key)
        if agent.key == 'conversation_analyzer':
            annotations = LearningAnnotationModel.objects.select_related('agent')
        else:
            annotations = agent.learning_annotations.select_related('agent')
        annotations = annotations.order_by('-created_at', '-pk')
        # Mismo filtro que la tabla de anotaciones de la configuración.
        active_only = request.GET.get('annotation_status') == 'active'
        if active_only:
            annotations = annotations.filter(is_active=True)
        response = render(request, 'prospects/agent_analyses.html', {
            'agent': agent,
            'page_obj': Paginator(annotations, 30).get_page(request.GET.get('page')),
            'annotation_status': 'active' if active_only else None,
        })
        response['Cache-Control'] = 'no-store'
        return response


class AgentAnnotationStatusView(LoginRequiredMixin, View):
    def post(self, request, agent_key, annotation_id):
        value = request.POST.get('is_active')
        if value not in ('0', '1'):
            return HttpResponseBadRequest('Estado de anotación no válido.')

        try:
            with transaction.atomic():
                agent = get_object_or_404(AgentModel.objects.select_for_update(), key=agent_key)
                annotation = get_object_or_404(LearningAnnotationModel, pk=annotation_id, agent=agent)
                annotation.is_active = value == '1'
                annotation.save(update_fields=['is_active', 'updated_at'])
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
        else:
            messages.success(request, 'Anotación activada.' if annotation.is_active else 'Anotación desactivada.')
        return annotations_redirect(request, agent_key)


class AgentAnnotationsCondenseView(LoginRequiredMixin, View):
    def post(self, request, agent_key):
        agent = get_object_or_404(AgentModel, key=agent_key)
        try:
            condense_active_annotations(agent.pk)
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
        except Exception:
            logger.exception('No se pudieron resumir las anotaciones del agente %s.', agent.key)
            messages.error(
                request,
                'No se pudieron resumir las anotaciones. '
                'Las originales se conservan. Vuelve a intentarlo.',
            )
        else:
            messages.success(
                request,
                'Las anotaciones activas se reemplazaron por una sola anotación activa resumida. '
                'Ya puedes activar otras.',
            )
        return annotations_redirect(request, agent_key)


def annotations_redirect(request, agent_key):
    """Regresa a la pestaña Anotaciones conservando la página y el filtro."""
    query = {'annotation_page': request.POST.get('annotation_page', '1')}
    if request.POST.get('annotation_status') == 'active':
        query['annotation_status'] = 'active'
    return redirect(reverse(
        'app_prospects:agent_configuration', kwargs={'agent_key': agent_key},
        query=query,
        fragment='anotaciones',
    ))


# Mismo tope que el esquema con el que el analizador genera las sugerencias.
MAX_ANNOTATION_SUGGESTION_LENGTH = 4000


class AgentAnnotationUpdateView(LoginRequiredMixin, View):
    """Edita las sugerencias de una anotación; las observaciones no se tocan."""

    def post(self, request, agent_key, annotation_id):
        suggestion = (request.POST.get('suggestion') or '').strip()

        with transaction.atomic():
            agent = get_object_or_404(AgentModel.objects.select_for_update(), key=agent_key)
            annotation = get_object_or_404(LearningAnnotationModel, pk=annotation_id, agent=agent)
            if not suggestion:
                messages.error(request, 'Las sugerencias no pueden quedar vacías.')
            elif len(suggestion) > MAX_ANNOTATION_SUGGESTION_LENGTH:
                messages.error(
                    request,
                    f'Las sugerencias admiten hasta {MAX_ANNOTATION_SUGGESTION_LENGTH} caracteres.',
                )
            elif suggestion != annotation.suggestion:
                annotation.suggestion = suggestion
                annotation.save(update_fields=['suggestion', 'updated_at'])
                messages.success(request, 'Sugerencias actualizadas.')

        return annotations_redirect(request, agent_key)


class AgentAnnotationDeleteView(LoginRequiredMixin, View):
    """Elimina definitivamente una anotación del agente."""

    def post(self, request, agent_key, annotation_id):
        with transaction.atomic():
            agent = get_object_or_404(AgentModel.objects.select_for_update(), key=agent_key)
            annotation = get_object_or_404(LearningAnnotationModel, pk=annotation_id, agent=agent)
            annotation.delete()
        messages.success(request, 'Anotación eliminada.')
        return annotations_redirect(request, agent_key)


class AgentConfigurationView(LoginRequiredMixin, View):

    def get(self, request, agent_key):
        agent = get_object_or_404(AgentModel, key=agent_key)
        annotations = agent.learning_annotations.order_by('-created_at', '-pk')
        active_annotation_count = annotations.filter(is_active=True).count()
        active_annotations_only = request.GET.get('annotation_status') == 'active'
        if active_annotations_only:
            annotations = annotations.filter(is_active=True)
        response = render(request, 'prospects/agent_configuration.html', {
            'agent': agent,
            'prompt_form': AgentPromptForm(),
            'qualification_methodologies': QUALIFICATION_METHODOLOGIES,
            'configuration_steps': get_agent_configuration_steps(agent.key),
            'test_cases': get_agent_test_cases(agent.key),
            'annotation_page': Paginator(annotations, 30).get_page(request.GET.get('annotation_page')),
            'annotation_status': 'active' if active_annotations_only else None,
            'show_annotations': 'annotation_page' in request.GET or active_annotations_only,
            'active_annotation_count': active_annotation_count,
            'max_active_annotations': LearningAnnotationModel.MAX_ACTIVE_ANNOTATIONS,
            'annotation_can_summarize': active_annotation_count >= MIN_ANNOTATIONS_TO_SUMMARIZE,
            'annotation_capacity_reached': active_annotation_count >= LearningAnnotationModel.MAX_ACTIVE_ANNOTATIONS,
        })
        response['Cache-Control'] = 'no-store'
        return response


class KnowledgeView(LoginRequiredMixin, View):

    def get(self, request):
        # La estructura vive en la app de documentos y se siembra por
        # migración, así que la plantilla no la duplica. El tope de
        # caracteres viaja igual, para no repetir el número aquí y
        # allá y que un día dejen de coincidir.
        return render(
            request,
            'prospects/knowledge.html',
            {
                'knowledge_structure': KNOWLEDGE_STRUCTURE,
                'max_text_length': MAX_TEXT_LENGTH,
            },
        )


async def validate_messages_format(data):
    """
    Validates that the incoming JSON follows the OpenAI-style chat format:
    {'messages': [{'role': 'user|assistant', 'content': '...'}]}
    """
    # 1. Is it a dictionary and does it have the 'messages' key?
    if not isinstance(data, dict) or 'messages' not in data:
        return False, "La solicitud debe incluir una lista de mensajes."

    messages = data['messages']

    # 2. Is 'messages' a list?
    if not isinstance(messages, list):
        return False, "Los mensajes deben enviarse como una lista."

    # 3. Validate each message inside the list
    allowed_roles = {'user', 'assistant'}
    for index, msg in enumerate(messages):
        if not isinstance(msg, dict):
            return False, f"El mensaje {index} no tiene un formato válido."

        # Does it have exactly 'role' and 'content' keys?
        if set(msg.keys()) != {'role', 'content'}:
            return False, f"El mensaje {index} debe incluir únicamente el remitente y el contenido."

        # Is the role valid?
        if msg['role'] not in allowed_roles:
            return False, f"El remitente del mensaje {index} no es válido."

        # Is the content a string?
        if not isinstance(msg['content'], str):
            return False, f"El contenido del mensaje {index} debe ser texto."

    return True, None


@method_decorator(csrf_protect, name='dispatch')
class ChatbotAnalyzerView(View):

    async def dispatch(self, request, *args, **kwargs):
        is_authenticated = await sync_to_async(lambda: request.user.is_authenticated)()

        if not is_authenticated:
            return HttpResponseForbidden('Authentication required')

        return await super().dispatch(request, *args, **kwargs)

    async def get(self, request):
        return await sync_to_async(render)(
            request,
            'prospects/chatbot_analyzer.html'
        )

    async def post(self, request):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'La solicitud no tiene un formato JSON válido'}, status=400)

        is_valid, error_msg = await validate_messages_format(data)
        if not is_valid:
            return JsonResponse({'error': error_msg}, status=400)

        messages = data['messages']

        if len(messages) > 15:
            return JsonResponse({'error': 'Se alcanzó el límite de mensajes. Inicia una nueva conversación.'}, status=429)

        try:
            response = await create_response(messages)
        except Exception as e:
            return JsonResponse({'error': 'El servicio de IA no está disponible. Intenta de nuevo más tarde.'}, status=503)

        return JsonResponse({'response': response}, status=200)


class ExportPreviewView(LoginRequiredMixin, View):
    """
    Contadores y vista previa del modal de exportación. Resuelve los
    mismos filtros que la descarga, para que lo que se ve sea lo que se
    descarga.
    """

    def get(self, request):
        try:
            filters = build_export_filters(request)
            data = build_export_preview(filters)
        except Exception:
            logger.exception(
                'No se pudo construir la vista previa de exportación')
            return JsonResponse(
                {'error': 'No se pudo consultar la información. '
                          'Intenta de nuevo.'},
                status=500,
            )

        return JsonResponse(data, status=200)


class ExportDownloadView(LoginRequiredMixin, View):
    """
    Descarga el libro .xlsx con los datos reales del backend, no solo
    la página visible. La construcción vive en exports.py; aquí solo se
    resuelven los filtros y se arma la respuesta.
    """

    def get(self, request):
        try:
            filters = build_export_filters(request)
            workbook = build_export_workbook(filters, user=request.user)
        except Exception:
            logger.exception('No se pudo generar la exportación a Excel')
            return JsonResponse(
                {'error': 'No se pudo generar el archivo. Intenta de nuevo.'},
                status=500,
            )

        return FileResponse(
            workbook,
            as_attachment=True,
            filename=export_filename(filters),
            content_type=(
                'application/vnd.openxmlformats-officedocument'
                '.spreadsheetml.sheet'
            ),
        )
