from django.urls import path

from .views import (
    TopicListCreateView,
    TopicDetailView,
    DocumentListCreateView,
    DocumentDetailView,
    DocumentUploadView,
)


app_name = 'app_documents'


urlpatterns = [
    path('topics/', TopicListCreateView.as_view(), name='topic_list_create'),
    path('topics/<int:topic_id>/', TopicDetailView.as_view(),
         name='topic_detail'),
    path('', DocumentListCreateView.as_view(), name='document_list_create'),
    path('<int:document_id>/', DocumentDetailView.as_view(),
         name='document_detail'),
    path('<int:document_id>/upload/', DocumentUploadView.as_view(),
         name='document_upload'),
]
