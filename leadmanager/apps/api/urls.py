from django.urls import path, include


urlpatterns = [
    path('prospects/', include('leadmanager.apps.api.prospects.urls')),
    path('agents/', include('leadmanager.apps.api.agents.urls')),
]
