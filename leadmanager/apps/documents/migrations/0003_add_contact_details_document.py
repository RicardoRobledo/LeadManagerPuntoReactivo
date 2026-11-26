"""
Agrega el documento «Datos de contacto» a Información de la empresa.

Los teléfonos, correos, domicilio y horarios no estaban en ninguna
sección, y son de lo primero que pide un cliente. Sin un espacio propio
terminaban repartidos entre documentos que hablan de otra cosa, donde
la IA no los busca.
"""

from django.db import migrations

from leadmanager.apps.documents.knowledge_structure import (
    sync_knowledge_structure,
)


SECTION_NAME = 'Información de la empresa'
DOCUMENT_NAME = 'Datos de contacto'


def add_document(apps, schema_editor):
    """Crea el espacio nuevo sin tocar lo que ya está cargado."""
    TopicModel = apps.get_model('documents', 'TopicModel')
    DocumentModel = apps.get_model('documents', 'DocumentModel')

    sync_knowledge_structure(TopicModel, DocumentModel)


def remove_document(apps, schema_editor):
    """
    Quita el espacio, salvo que ya tenga contenido.

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
        document_name=DOCUMENT_NAME,
        content='',
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('documents', '0002_rename_products_section'),
    ]

    operations = [
        migrations.RunPython(add_document, remove_document),
    ]
