from django.contrib import admin

from .models import TopicModel, DocumentModel


@admin.register(TopicModel)
class TopicAdmin(admin.ModelAdmin):

    list_display = ('id', 'topic_name', 'is_active', 'description', 'created_at')
    list_filter = ('is_active',)
    list_editable = ('is_active',)
    search_fields = ('topic_name', 'description')
    ordering = ('topic_name',)


@admin.register(DocumentModel)
class DocumentAdmin(admin.ModelAdmin):

    list_display = ('id', 'document_name', 'is_active', 'topic_id', 'created_at')
    list_filter = ('topic_id', 'is_active')
    list_editable = ('is_active',)
    search_fields = ('document_name', 'content')
    autocomplete_fields = ('topic_id',)
    ordering = ('-created_at',)
