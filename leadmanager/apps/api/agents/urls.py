from django.urls import path

from .views import (
    ChatbotFormView,
    WebChatView,
    WebChatMessagesView,
)


urlpatterns = [
    path('form/', ChatbotFormView.as_view(), name='chatbot-form'),
    path('web-chat/', WebChatView.as_view(), name='web-chat'),
    path('web-chat/messages/', WebChatMessagesView.as_view(), name='web-chat-messages'),
]
