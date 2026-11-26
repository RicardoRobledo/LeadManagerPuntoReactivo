"""
Migración inicial de la base de conocimiento.

Comprime a las catorce originales, que ya fueron borradas. Estas
fueron construyendo el esquema por partes y
corrigiendo la estructura sembrada a medida que cambiaba. Una
instalación nueva no necesita ese recorrido: las tablas se crean ya con
su forma final y ``sync_knowledge_structure`` siembra directamente la
estructura vigente.

Por eso las siete migraciones de datos originales quedan aquí en una
sola llamada: seis de ellas eran la misma siembra repetida —es
idempotente y lee la estructura viva— y las otras dos (0009 y 0010)
retiraban una sección y unos documentos que solo existen en una base
que pasó por la estructura anterior. En una base nueva nunca se crean.
"""

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models

from leadmanager.apps.documents.knowledge_structure import (
    KNOWLEDGE_STRUCTURE,
    document_names,
    sync_knowledge_structure,
)


def seed_structure(apps, schema_editor):
    """Creates the sections and document slots of the knowledge base."""
    TopicModel = apps.get_model('documents', 'TopicModel')
    DocumentModel = apps.get_model('documents', 'DocumentModel')

    sync_knowledge_structure(TopicModel, DocumentModel)


def remove_structure(apps, schema_editor):
    """
    Removes only the slots that are still empty.

    Sections and documents that already hold content are kept, so
    rolling the migration back never destroys loaded information.
    """
    TopicModel = apps.get_model('documents', 'TopicModel')
    DocumentModel = apps.get_model('documents', 'DocumentModel')

    for section in KNOWLEDGE_STRUCTURE:
        topic = TopicModel.objects.filter(
            topic_name=section['name'],
        ).first()

        if not topic:
            continue

        DocumentModel.objects.filter(
            topic_id=topic,
            document_name__in=document_names(section),
            content='',
        ).delete()

        # La sección solo se borra si se quedó sin documentos.
        if not DocumentModel.objects.filter(topic_id=topic).exists():
            topic.delete()


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='TopicModel',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('topic_name', models.CharField(max_length=255)),
                ('description', models.TextField(validators=[django.core.validators.MaxLengthValidator(57618)])),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={
                'verbose_name': 'topic',
                'verbose_name_plural': 'topics',
            },
        ),
        migrations.CreateModel(
            name='DocumentModel',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('document_name', models.CharField(max_length=255)),
                ('content', models.TextField(blank=True, default='', validators=[django.core.validators.MaxLengthValidator(57618)])),
                ('description', models.TextField(blank=True, default='', validators=[django.core.validators.MaxLengthValidator(57618)])),
                ('is_active', models.BooleanField(default=True)),
                ('topic_id', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='documents.topicmodel')),
            ],
            options={
                'verbose_name': 'document',
                'verbose_name_plural': 'documents',
            },
        ),
        # La siembra va al final: necesita las columnas ya creadas.
        migrations.RunPython(seed_structure, remove_structure),
    ]
