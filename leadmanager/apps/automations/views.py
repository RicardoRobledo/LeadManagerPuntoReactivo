import json
import logging
from datetime import datetime, time
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from django.db import transaction
from django.shortcuts import get_object_or_404, render
from django.http import JsonResponse
from django.views import View
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from django.utils.dateparse import parse_date

from .agent_configuration import with_prompt_defaults
from .agent_preview import preview_agent_response
from .forms import AgentPromptForm
from .models import AgentModel, AgentRunModel, AgentStepModel, AgentEventModel, EmailDeliveryLogModel
from leadmanager.apps.prospects.models import ConversationSourceEnum


logger = logging.getLogger(__name__)


def _runs_with_agent_details():
    agent = AgentModel.objects.filter(key=OuterRef('agent_name'))
    return AgentRunModel.objects.annotate(
        agent_display_name=Coalesce(Subquery(agent.values('name')[:1]), 'agent_name'),
        agent_category=Subquery(agent.values('category')[:1]),
        agent_has_avatar=Exists(agent.filter(avatar__startswith='agent-avatars/')),
    )


class EmailDeliveryListView(LoginRequiredMixin, View):
    """Consulta de todos los intentos de correo, sin acciones de envío."""

    def get(self, request):
        status = request.GET.get('status', '').strip()
        kind = request.GET.get('kind', '').strip()
        source = request.GET.get('source', '').strip()
        for field, value, choices in (
            ('status', status, EmailDeliveryLogModel.StatusEnum.values),
            ('kind', kind, EmailDeliveryLogModel.KindEnum.values),
            ('source', source, (*ConversationSourceEnum.values, 'none')),
        ):
            if value and value not in choices:
                messages.warning(request, 'Un filtro no es válido; se muestran todas sus opciones.')
                if field == 'status':
                    status = ''
                elif field == 'kind':
                    kind = ''
                else:
                    source = ''

        dates = {}
        for name in ('date_from', 'date_to'):
            value = request.GET.get(name, '').strip()
            try:
                parsed = parse_date(value) if value else None
            except ValueError:
                parsed = None
            if value and parsed is None:
                messages.warning(request, 'Una fecha no es válida; se omitió ese límite.')
            dates[name] = parsed
        if dates['date_from'] and dates['date_to'] and dates['date_from'] > dates['date_to']:
            dates['date_from'], dates['date_to'] = dates['date_to'], dates['date_from']
            messages.warning(request, 'Las fechas se intercambiaron para ordenar el periodo.')

        recipient = request.GET.get('recipient', '').strip()[:320]
        search = request.GET.get('search', '').strip()[:200]
        qs = EmailDeliveryLogModel.objects.select_related('conversation')
        if status:
            qs = qs.filter(status=status)
        if kind:
            qs = qs.filter(kind=kind)
        if source:
            qs = qs.filter(source='' if source == 'none' else source)
        if recipient:
            qs = qs.filter(recipient__icontains=recipient)
        if search:
            condition = Q(subject__icontains=search) | Q(message_id__icontains=search) | Q(task_id__icontains=search)
            if search.isascii() and search.isdigit() and len(search) <= 18:
                condition |= Q(pk=int(search)) | Q(conversation_id=int(search))
            qs = qs.filter(condition)
        zona = ZoneInfo('America/Mexico_City')
        if dates['date_from']:
            qs = qs.filter(created_at__gte=datetime.combine(dates['date_from'], time.min, tzinfo=zona))
        if dates['date_to']:
            qs = qs.filter(created_at__lte=datetime.combine(dates['date_to'], time.max, tzinfo=zona))

        totals = qs.aggregate(
            total=Count('pk'),
            accepted=Count('pk', filter=Q(status=EmailDeliveryLogModel.StatusEnum.SMTP_ACCEPTED)),
            failed=Count('pk', filter=Q(status=EmailDeliveryLogModel.StatusEnum.FAILED)),
            skipped=Count('pk', filter=Q(status=EmailDeliveryLogModel.StatusEnum.SKIPPED)),
            sending=Count('pk', filter=Q(status=EmailDeliveryLogModel.StatusEnum.SENDING)),
        )
        page_obj = Paginator(qs.order_by('-created_at', '-pk'), 25).get_page(request.GET.get('page', 1))
        return render(request, 'automations/email_deliveries.html', {
            'page_obj': page_obj, 'totals': totals, 'status': status, 'kind': kind,
            'source': source, 'recipient': recipient, 'search': search,
            'date_from': dates['date_from'].isoformat() if dates['date_from'] else '',
            'date_to': dates['date_to'].isoformat() if dates['date_to'] else '',
            'status_choices': [
                (value, 'Enviado' if value == EmailDeliveryLogModel.StatusEnum.SMTP_ACCEPTED else label)
                for value, label in EmailDeliveryLogModel.StatusEnum.choices
            ],
            'kind_choices': EmailDeliveryLogModel.KindEnum.choices,
            'source_choices': ConversationSourceEnum.choices,
        })


class EmailDeliveryDetailView(LoginRequiredMixin, View):
    def get(self, request, delivery_id):
        delivery = get_object_or_404(
            EmailDeliveryLogModel.objects.select_related('conversation'), pk=delivery_id,
        )
        related = EmailDeliveryLogModel.objects.filter(kind=delivery.kind).exclude(pk=delivery.pk)
        if delivery.conversation_id:
            related = related.filter(conversation_id=delivery.conversation_id)
        else:
            related = related.filter(recipient=delivery.recipient, source=delivery.source)
        return render(request, 'automations/email_delivery_detail.html', {
            'delivery': delivery, 'related_attempts': related.order_by('-created_at', '-pk')[:10],
        })


class AgentPromptsView(LoginRequiredMixin, View):
    """Consulta y edición de los prompts desde el catálogo de agentes."""

    @staticmethod
    def _prompt_payload(agent):
        return {
            **with_prompt_defaults(agent.prompt),
            'learning_enabled': agent.learning_enabled,
            'mode': agent.mode,
            'mode_block_reason': agent.get_shadow_mode_block_reason(),
            'business_units': list(agent.business_units.values('id', 'name', 'description')),
        }

    def get(self, request, agent_key):
        agent = get_object_or_404(AgentModel, key=agent_key)
        response = JsonResponse({
            'name': agent.name,
            'prompt': self._prompt_payload(agent),
        })
        response['Cache-Control'] = 'no-store'
        return response

    @transaction.atomic
    def post(self, request, agent_key):
        agent = get_object_or_404(AgentModel.objects.select_for_update(), key=agent_key)
        form = AgentPromptForm(request.POST, agent=agent)
        if not form.is_valid():
            return JsonResponse({'errors': form.errors.get_json_data()}, status=400)
        agent = form.save()
        return JsonResponse({
            'name': agent.name,
            'message': 'Configuración guardada. Se aplicará en las próximas respuestas.',
            'prompt': self._prompt_payload(agent),
        })


@method_decorator(csrf_protect, name='dispatch')
class AgentPreviewView(LoginRequiredMixin, View):
    """Prueba el prompt guardado sin crear conversación, lead ni run."""

    def post(self, request, agent_key):
        get_object_or_404(AgentModel, key=agent_key)
        if len(request.body) > 50000:
            return JsonResponse({'error': 'La prueba es demasiado larga. Reiníciala para continuar.'}, status=400)
        try:
            payload = json.loads(request.body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return JsonResponse({'error': 'La solicitud no contiene JSON válido.'}, status=400)
        messages = payload.get('messages') if isinstance(payload, dict) else None
        if (not isinstance(messages, list) or not 1 <= len(messages) <= 11
                or len(messages) % 2 == 0):
            return JsonResponse({'error': 'Envía entre uno y seis mensajes del prospecto.'}, status=400)
        for index, message in enumerate(messages):
            expected_role = 'user' if index % 2 == 0 else 'assistant'
            limit = 2000 if expected_role == 'user' else 12000
            if (not isinstance(message, dict) or set(message) != {'role', 'content'}
                    or message['role'] != expected_role or not isinstance(message['content'], str)
                    or not message['content'].strip() or len(message['content']) > limit):
                return JsonResponse({'error': 'El historial de la prueba no es válido.'}, status=400)
        try:
            result = preview_agent_response(agent_key, messages)
        except Exception:
            logger.exception('Falló la prueba del agente %s', agent_key)
            return JsonResponse({'error': 'No se pudo obtener la respuesta del agente. Inténtalo de nuevo.'}, status=503)
        response = JsonResponse(result)
        response['Cache-Control'] = 'no-store'
        return response


class AgentRunsView(LoginRequiredMixin, View):

    def get(self, request):

        # ==========================================
        # FILTROS
        # ==========================================

        status_options = tuple(
            AgentRunModel.StatusEnum.values
        )

        status = request.GET.get('status', 'all')

        if status != 'all' and status not in status_options:
            messages.warning(
                request,
                'El filtro de estado no es válido. Se muestran todas las ejecuciones.',
            )
            status = 'all'

        source = request.GET.get('source', '').strip()
        source = {
            'chatweb': 'web',
            'correo': 'email',
            'formulario': 'form',
        }.get(source.casefold(), source)

        agent_name = request.GET.get('agent_name', '').strip()

        search = request.GET.get('search', '').strip()

        page_number = request.GET.get('page', 1)

        # ==========================================
        # CONSULTA PRINCIPAL
        # ==========================================

        qs = (
            _runs_with_agent_details()
            .select_related('conversation__prospect')
            .annotate(
                step_count=Count('steps', distinct=True),
                failed_step_count=Count(
                    'steps',
                    filter=Q(steps__status=AgentStepModel.StatusEnum.FAILED),
                    distinct=True,
                ),
            )
        )

        if status != 'all':
            qs = qs.filter(status=status)

        if source:
            qs = qs.filter(source__icontains=source)

        if agent_name:
            qs = qs.filter(agent_name__iexact=agent_name)

        if search:
            qs = qs.filter(
                Q(id__icontains=search)
                | Q(conversation__session_id__icontains=search)
            )

        qs = qs.order_by('-started_at')

        # ==========================================
        # CONTEXTO AUXILIAR
        # ==========================================

        agent_options = list(
            _runs_with_agent_details()
            .exclude(agent_name='')
            .order_by('agent_display_name', 'agent_name')
            .values('agent_name', 'agent_display_name')
            .distinct()
        )

        total_runs = AgentRunModel.objects.count()

        def count_by_status(value):
            return AgentRunModel.objects.filter(status=value).count()

        # ==========================================
        # PAGINACIÓN
        # ==========================================

        paginator = Paginator(qs, 15)

        page_obj = paginator.get_page(page_number)

        context = {
            'status': status,
            'source': source,
            'agent_name': agent_name,
            'search': search,
            'agent_options': agent_options,
            'status_options': status_options,
            'total_runs': total_runs,
            'running_count': count_by_status(
                AgentRunModel.StatusEnum.RUNNING,
            ),
            'success_count': count_by_status(
                AgentRunModel.StatusEnum.SUCCESS,
            ),
            'failed_count': count_by_status(
                AgentRunModel.StatusEnum.FAILED,
            ),
            'page_obj': page_obj,
        }

        return render(
            request,
            'automations/runs.html',
            context,
        )


class AgentRunDetailView(LoginRequiredMixin, View):

    def get(self, request, run_id):

        run = get_object_or_404(
            _runs_with_agent_details().select_related(
                'conversation__prospect',
            ),
            pk=run_id,
        )

        steps = list(
            run.steps
            .prefetch_related('events')
            .order_by('order')
        )

        for step in steps:
            step.duration = self._duration(
                step.started_at,
                step.finished_at,
            )

            for event in step.events.all():
                event.data_json = self._pretty_data(event.data)

        run_events = list(
            run.events
            .filter(step__isnull=True)
            .order_by('created_at', 'id')
        )

        for event in run_events:
            event.data_json = self._pretty_data(event.data)

        context = {
            'run': run,
            'steps': steps,
            'run_events': run_events,
            'duration': self._duration(
                run.started_at,
                run.finished_at,
            ),
        }

        return render(
            request,
            'automations/run_detail.html',
            context,
        )

    @staticmethod
    def _pretty_data(value):
        if value is None:
            return None

        return json.dumps(
            value,
            indent=2,
            ensure_ascii=False,
            default=str,
        )

    @staticmethod
    def _duration(started_at, finished_at):
        if not started_at:
            return None

        if not finished_at:
            return None

        delta = finished_at - started_at

        total_seconds = int(delta.total_seconds())

        if total_seconds < 1:
            return f'{delta.microseconds // 1000}ms'

        minutes, seconds = divmod(total_seconds, 60)

        if minutes:
            return f'{minutes}m {seconds}s'

        return f'{seconds}s'
