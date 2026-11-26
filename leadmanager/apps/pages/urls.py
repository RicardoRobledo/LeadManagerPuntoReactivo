from django.urls import path
from .views import FormView, WebChatPageView, WidgetDemoView, WidgetAvatarView


app_name = 'app_pages'


urlpatterns = [
    path('', FormView.as_view(), name='form'),
    path('chat/', WebChatPageView.as_view(), name='webchat'),
    path('widget-avatar/', WidgetAvatarView.as_view(), name='widget_avatar'),
    path('widget-demo/', WidgetDemoView.as_view(), name='widget_demo'),
]
