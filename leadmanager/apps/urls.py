from django.urls import path, include


urlpatterns = [
    path('', include('leadmanager.apps.prospects.urls')),
    path('documents/', include('leadmanager.apps.documents.urls')),
    path('automations/', include('leadmanager.apps.automations.urls')),
]
