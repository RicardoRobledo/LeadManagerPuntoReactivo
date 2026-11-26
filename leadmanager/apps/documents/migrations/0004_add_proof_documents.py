"""
Agrega «Casos de éxito» y «Quiénes confiaron en nosotros».

La sección ya decía qué distingue a la empresa, pero no tenía dónde
guardar la prueba de que eso es cierto. Son dos documentos y no uno
porque responden a preguntas distintas: uno cuenta proyectos, el otro
enumera clientes, y cada uno se puede apagar por separado.
"""

from django.db import migrations

from leadmanager.apps.documents.knowledge_structure import (
    sync_knowledge_structure,
)


SECTION_NAME = 'Información de la empresa'

DOCUMENT_NAMES = (
    'Casos de éxito',
    'Quiénes confiaron en nosotros',
)


def add_documents(apps, schema_editor):
    """Crea los espacios nuevos sin tocar lo que ya está cargado."""
    TopicModel = apps.get_model('documents', 'TopicModel')
    DocumentModel = apps.get_model('documents', 'DocumentModel')

    sync_knowledge_structure(TopicModel, DocumentModel)


def remove_documents(apps, schema_editor):
    """
    Quita los espacios, salvo los que ya tengan contenido.

    Revertir una migración no debe costar información que alguien
    cargó a mano.
    """
    TopicModel = apps.get_model('documents', 'TopicModel')
    DocumentModel = apps.get_model('documents', 'DocumentModel')

    topic = TopicModel.objects.filter(topic_name=SECTION_NAME).first()

    if not topic:
        return

    DocumentModel.objects.filter(
        topic_id=topic,
        document_name__in=DOCUMENT_NAMES,
        content='',
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('documents', '0003_add_contact_details_document'),
    ]

    operations = [
        migrations.RunPython(add_documents, remove_documents),
    ]
