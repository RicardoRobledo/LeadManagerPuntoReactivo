import json
import secrets

from django.db import IntegrityError, transaction
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.throttling import ScopedRateThrottle

from .serializers import ChatbotFormSerializer, WebChatSerializer

from leadmanager.apps.automations.models import AgentModel, AgentRunModel, PendingRunModel
from leadmanager.apps.prospects.models import (
    ChatHistoryModel,
    ConversationModel,
    ConversationSourceEnum,
    ConversationStateEnum,
)
from leadmanager.apps.prospects.web_chat import (
    WEB_CHAT_LIMIT_MESSAGE, message_limit_state,
)
from leadmanager.apps.automations.tasks import process_pending_run


class ChatbotFormView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'chatbot_form'

    def post(self, request):
        serializer = ChatbotFormSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(
                {
                    'error': 'Missing parameters',
                    'fields': serializer.errors
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        form_data = dict(serializer.validated_data)

        # La fila se escribe ANTES de encolar: si el pipeline falla o el
        # mensaje se pierde, el lead sigue registrado y el barrido lo
        # reintenta. Sin esto, un fallo lo borraba sin dejar rastro.
        pendiente = PendingRunModel.encolar(
            source=ConversationSourceEnum.FORM,
            payload=form_data,
            identifier=form_data.get('email', ''),
        )

        process_pending_run.delay(pendiente.id)

        return Response(
            {
                'message': 'Datos recibidos.'
            },
            status=status.HTTP_202_ACCEPTED,
        )


class WebChatView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'web_chat'

    @transaction.atomic
    def post(self, request):
        serializer = WebChatSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(
                {
                    'error': 'Missing parameters',
                    'fields': serializer.errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        session_id = serializer.validated_data.get(
            'session_id') or secrets.token_urlsafe(24)

        # Todo el payload del canal viaja en un solo dict, igual que
        # ``process_form``: el agente lo recibe bajo ctx['web'] y de ahí saca
        # el correo y el mensaje. Se reescribe el session_id con el ya
        # resuelto, porque el serializer lo deja en None cuando no viene.
        web_data = dict(serializer.validated_data)
        web_data['session_id'] = session_id

        # Serializa los envíos de la sesión; el contador nunca viene del cliente.
        conversations = ConversationModel.objects.select_for_update().filter(
            source=ConversationSourceEnum.WEB, session_id=session_id,
        ).order_by('-id')
        conversation = conversations.first()
        if conversation is None:
            try:
                with transaction.atomic():
                    conversation = ConversationModel.objects.create(
                        source=ConversationSourceEnum.WEB, session_id=session_id,
                    )
            except IntegrityError:
                # Dos primeros POST simultáneos compiten por la sesión abierta.
                conversation = conversations.first()
                if conversation is None:
                    raise

        limit_state = message_limit_state(conversation)
        if limit_state['message_limit_reached']:
            if conversation.state != ConversationStateEnum.CLOSED:
                conversation.state = ConversationStateEnum.CLOSED
                conversation.save(update_fields=['state', 'updated_at'])
            return Response({
                'error': WEB_CHAT_LIMIT_MESSAGE,
                'code': 'message_limit_reached',
                'session_id': session_id,
                'conversation_closed': True,
                **limit_state,
            }, status=status.HTTP_403_FORBIDDEN)

        # El historial lo crea el agente en
        # ``verify_existing_conversation_step``, de forma asíncrona. Entre este
        # POST y esa escritura hay una ventana en la que el historial todavía
        # muestra el turno anterior completo, y el poll daría por buena la
        # respuesta vieja. Por eso se captura aquí el último id conocido: es la
        # marca de agua a partir de la cual el poll debe buscar.
        after_id = (
            ChatHistoryModel.objects
            .filter(conversation=conversation)
            .order_by('-id')
            .values_list('id', flat=True)
            .first()
        ) or 0

        processing = AgentRunModel.objects.filter(
            status=AgentRunModel.StatusEnum.RUNNING,
            mode=AgentModel.ModeEnum.PRODUCTION,
            source=ConversationSourceEnum.WEB,
            conversation__session_id=session_id,
        ).exists()

        # El worker puede no haber creado todavía el AgentRun. La fila durable
        # también cuenta como turno en curso, incluso al recargar el widget.
        pendiente = PendingRunModel.objects.filter(
            source=ConversationSourceEnum.WEB,
            identifier=session_id,
            status__in=[
                PendingRunModel.StatusEnum.PENDING,
                PendingRunModel.StatusEnum.PROCESSING,
            ],
        ).order_by('-id').first()

        if pendiente is not None:
            return Response({
                'session_id': session_id,
                'status': 'processing',
                'after_id': pendiente.payload.get('_after_id', after_id),
                **limit_state,
            }, status=status.HTTP_202_ACCEPTED)

        if processing:
            return Response({
                'session_id': session_id,
                'status': 'processing',
                'after_id': after_id,
                **limit_state,
            }, status=status.HTTP_202_ACCEPTED)

        conversation.web_message_count += 1
        # También el último mensaje permitido recibe respuesta del agente.
        # El worker cierra la conversación después de completar ese turno.
        conversation.state = ConversationStateEnum.OPEN
        conversation.save(update_fields=['web_message_count', 'state', 'updated_at'])
        limit_state = message_limit_state(conversation)

        # El worker usa esta misma conversación aunque el CRM la haya cerrado.
        web_data['_conversation_id'] = conversation.pk
        web_data['_after_id'] = after_id
        pendiente = PendingRunModel.encolar(
            source=ConversationSourceEnum.WEB,
            payload=web_data,
            identifier=session_id,
        )

        transaction.on_commit(lambda: process_pending_run.delay(pendiente.id))

        return Response(
            {
                'session_id': session_id,
                'status': 'accepted',
                'after_id': after_id,
                **limit_state,
            },
            status=status.HTTP_202_ACCEPTED,
        )


class WebChatMessagesView(APIView):
    """Historial visible del widget; las búsquedas y datos del CRM son internos."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'web_chat_poll'

    def get(self, request):
        session_id = request.query_params.get('session_id', '').strip()
        try:
            after_id = int(request.query_params.get('after_id', 0))
            if after_id < 0:
                raise ValueError
        except (TypeError, ValueError):
            return Response({'error': 'after_id inválido.'}, status=400)

        if not session_id or len(session_id) > 255:
            return Response({'error': 'session_id inválido.'}, status=400)

        pendiente = PendingRunModel.objects.filter(
            source=ConversationSourceEnum.WEB, identifier=session_id,
        ).order_by('-id').first()
        processing = pendiente is not None and pendiente.status in (
            PendingRunModel.StatusEnum.PENDING,
            PendingRunModel.StatusEnum.PROCESSING,
        )
        failed = pendiente is not None and pendiente.status in (
            PendingRunModel.StatusEnum.ABANDONED,
            PendingRunModel.StatusEnum.INCOMPLETE,
        )
        conversation = ConversationModel.objects.filter(
            source=ConversationSourceEnum.WEB, session_id=session_id,
        ).order_by('-id').first()
        limit_state = message_limit_state(conversation)

        rows = ChatHistoryModel.objects.filter(
            conversation__source=ConversationSourceEnum.WEB,
            conversation__session_id=session_id, id__gt=after_id,
        ).order_by('id')
        # No publicar una respuesta parcial que un reintento pueda revertir.
        # Los turnos anteriores sí se pueden recuperar mientras se espera.
        if processing or failed:
            rows = rows.filter(id__lte=pendiente.payload.get('_after_id', 0))

        messages = []
        last_id = after_id
        for row in rows:
            last_id = row.id
            visible = self.public_message(row.message)
            if visible:
                messages.append({
                    'id': row.id,
                    'role': visible[0],
                    'content': visible[1],
                    'created_at': row.created_at.isoformat(),
                })

        reply = next((m['content'] for m in reversed(messages)
                      if m['role'] == 'assistant'), None)
        if processing:
            poll_status = 'processing'
        elif failed:
            poll_status = 'failed'
        else:
            poll_status = 'ready' if reply else 'idle'
        response = Response({
            'session_id': session_id,
            'status': poll_status,
            'messages': messages,
            'last_id': last_id,
            'reply': reply,
            'conversation_closed': bool(
                conversation and conversation.state == 'closed' and not processing
            ),
            **limit_state,
        })
        response['Cache-Control'] = 'no-store'
        return response

    @staticmethod
    def public_message(message):
        if not isinstance(message, dict) or message.get('kind') == 'history_summary':
            return None
        role = message.get('role')
        content = message.get('content')
        if role == 'user':
            content = message.get('display_content', content)
            # Compatibilidad con mensajes web guardados antes del chat conversacional.
            if ('display_content' not in message and isinstance(content, str)
                    and ', mensaje:' in content):
                content = content.split(', mensaje:', 1)[1]
        elif role == 'assistant':
            if isinstance(content, str):
                try:
                    content = json.loads(content)
                except (ValueError, TypeError):
                    return None
            content = content.get('reply_to_customer') if isinstance(content, dict) else None
        else:
            return None
        if isinstance(content, str) and content.strip():
            return role, content
        return None
