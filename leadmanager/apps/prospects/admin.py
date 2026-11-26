from django.contrib import admin

from .models import (
    ProspectModel, ConversationModel, ConversationEscalationModel, MessengerReminderModel,
    ChatHistoryModel, NotQualifiedCategoryModel, EmailMessageModel,
)


def _resumen(value, limite=120):
    texto = value if isinstance(value, str) else str(value)
    return texto if len(texto) <= limite else f'{texto[:limite]}…'


@admin.register(ConversationModel)
class ConversationModelAdmin(admin.ModelAdmin):
    list_display = ('id', 'session_id', 'source', 'state', 'prospect', 'created_at')
    list_filter = ('source', 'state')
    # Busca por session_id exacto o parcial; también por el correo o nombre del prospecto.
    search_fields = ('session_id', 'prospect__email', 'prospect__name')
    raw_id_fields = ('prospect',)
    ordering = ('-created_at',)


@admin.register(ChatHistoryModel)
class ChatHistoryModelAdmin(admin.ModelAdmin):
    list_display = ('id', 'conversation_id', 'session_id', 'role', 'contenido', 'created_at')
    list_select_related = ('conversation',)
    # «message» busca dentro del JSON del mensaje (por ejemplo, un correo).
    search_fields = ('conversation__session_id', 'message')
    raw_id_fields = ('conversation', 'email_message')
    ordering = ('conversation_id', 'created_at', 'id')

    @admin.display(description='Session id', ordering='conversation__session_id')
    def session_id(self, obj):
        return obj.conversation.session_id if obj.conversation else ''

    @admin.display(description='Rol')
    def role(self, obj):
        if isinstance(obj.message, dict):
            return obj.message.get('role') or obj.message.get('type') or ''
        return ''

    @admin.display(description='Contenido')
    def contenido(self, obj):
        message = obj.message
        if isinstance(message, dict):
            message = message.get('content', message)
        return _resumen(message)


@admin.register(ConversationEscalationModel)
class ConversationEscalationModelAdmin(admin.ModelAdmin):
    list_display = ('id', 'conversacion', 'session_key', 'seller', 'created_at')
    list_select_related = ('session', 'seller')
    search_fields = ('session__session_id', 'conversation_content')
    raw_id_fields = ('session', 'seller')
    ordering = ('-created_at',)

    # Aquí session_id es la llave de la conversación, no su session_id de texto.
    @admin.display(description='Conversación', ordering='session_id')
    def conversacion(self, obj):
        return obj.session_id

    @admin.display(description='Session id', ordering='session__session_id')
    def session_key(self, obj):
        return obj.session.session_id if obj.session else ''


admin.site.register(EmailMessageModel)
admin.site.register(ProspectModel)
admin.site.register(MessengerReminderModel)
admin.site.register(NotQualifiedCategoryModel)
