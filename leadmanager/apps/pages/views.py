from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.clickjacking import xframe_options_exempt
from django.http import Http404
from django.http import FileResponse, HttpResponse
from django.core.files.storage import default_storage
import re

from leadmanager.apps.automations.agent_catalog import get_agent_configuration
from leadmanager.apps.automations.agent_configuration import DEFAULT_BUSINESS_UNIT_NAME, render_greeting
from leadmanager.apps.automations.models import AgentChannelAssignment, AgentModel


__author__ = 'RSSpe'
__version__ = '0.1'


def custom_404_view(request, exception):
    return render(request, 'pages/404.html', status=404)


class FormView(View):

    def get(self, request):
        source = request.GET.get('source')

        if not source:
            raise Http404('No se encontró el origen de la solicitud')

        source = source.lower()

        sources = {
            'google': 0,
            'facebook': 1,
        }

        if source not in sources:
            raise Http404('No se encontró el origen de la solicitud')

        return render(
            request,
            'pages/form.html',
            context={'source_id': sources[source]}
        )


FRAME_ANCESTORS = (
    "frame-ancestors 'self' "
    "https://estrasol.com.mx https://www.estrasol.com.mx "
    "https://nextwave.mx https://www.nextwave.mx"
)


def active_web_agent():
    assignment = AgentChannelAssignment.objects.filter(
        channel='web', active=True, agent__is_active=True,
        agent__category=AgentModel.CategoryEnum.PROSPECTING,
        agent__mode=AgentModel.ModeEnum.PRODUCTION,
    ).select_related('agent').first()
    return assignment.agent if assignment else None


@method_decorator(xframe_options_exempt, name='dispatch')
class WebChatPageView(View):
    def get(self, request):
        agent = active_web_agent()
        configuration, units = get_agent_configuration(agent.key if agent else 'prospector')
        if agent:
            configuration = {**configuration, 'name': agent.name}
        if configuration['saludo'] == 'Hola, soy Sofía, asistente virtual de Estrasol.':
            configuration = {**configuration, 'saludo': 'Hola, soy [bot], asistente virtual de Estrasol.'}
        response = render(request, 'webchat/chat.html', {
            'chat_configuration': {
                'name': configuration['name'],
                'enterprise_name': configuration['enterprise_name'],
                'saludo': render_greeting(configuration),
                'business_units': [unit['name'] for unit in units if unit['name'] != DEFAULT_BUSINESS_UNIT_NAME],
            },
        })
        response['Content-Security-Policy'] = FRAME_ANCESTORS
        return response


class WidgetAvatarView(View):
    """Imagen pública del agente configurado para la burbuja de chat web."""
    def get(self, request):
        agent = active_web_agent()
        avatar = agent.avatar if agent else ''
        match = re.fullmatch(r'agent-avatars/[0-9a-f]{32}\.(png|jpg|webp)', avatar or '')
        if match:
            try:
                image = default_storage.open(avatar, 'rb')
            except FileNotFoundError:
                pass
            else:
                response = FileResponse(image, content_type={
                    'png': 'image/png', 'jpg': 'image/jpeg', 'webp': 'image/webp',
                }[match.group(1)])
                response['X-Content-Type-Options'] = 'nosniff'
                response['Cache-Control'] = 'no-store'
                return response
        response = HttpResponse(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 80">'
            '<rect width="80" height="80" rx="40" fill="#eef2ff"/>'
            '<circle cx="40" cy="31" r="13" fill="#2451f5"/>'
            '<path d="M16 70c2-15 12-23 24-23s22 8 24 23" fill="#2451f5"/></svg>',
            content_type='image/svg+xml',
        )
        response['X-Content-Type-Options'] = 'nosniff'
        response['Cache-Control'] = 'no-store'
        return response


class WidgetDemoView(View):

    def get(self, request):
        return render(request, 'pages/widget_demo.html')
