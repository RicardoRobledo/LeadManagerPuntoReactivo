from django.urls import path

from .views import (
    AgentPromptsView, AgentPreviewView, AgentRunsView, AgentRunDetailView,
    EmailDeliveryListView, EmailDeliveryDetailView,
)


app_name = 'app_automations'


urlpatterns = [
    path('emails/', EmailDeliveryListView.as_view(), name='email_deliveries'),
    path('emails/<int:delivery_id>/', EmailDeliveryDetailView.as_view(), name='email_delivery_detail'),
    path('agents/<slug:agent_key>/prompts/', AgentPromptsView.as_view(), name='agent_prompts'),
    path('agents/<slug:agent_key>/preview/', AgentPreviewView.as_view(), name='agent_preview'),
    path('runs/', AgentRunsView.as_view(), name='runs'),
    path('runs/<int:run_id>/', AgentRunDetailView.as_view(), name='run_detail'),
]
