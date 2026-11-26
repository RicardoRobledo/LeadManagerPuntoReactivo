"""
Renombra la sección de productos para que incluya los servicios.

La empresa no solo vende productos, y tanto la sección como su ficha
prometían únicamente productos. Renombrar en lugar de dejar que la
siembra cree los nombres nuevos es lo que conserva el contenido ya
cargado: si no, quedarían una sección y un documento viejos con el
texto dentro, y otros nuevos vacíos al lado.
"""

from django.db import migrations

from leadmanager.apps.documents.knowledge_structure import (
    sync_knowledge_structure,
)


OLD_TOPIC = 'Productos'
NEW_TOPIC = 'Productos y servicios'

OLD_DOCUMENT = 'Información de productos'
NEW_DOCUMENT = 'Información de productos y servicios'


def rename(apps, old_topic, new_topic, old_document, new_document):
    """Renames the section and, inside it, its document."""
    TopicModel = apps.get_model('documents', 'TopicModel')
    DocumentModel = apps.get_model('documents', 'DocumentModel')

    # En una base recién creada la migración inicial ya sembró el
    # nombre nuevo, así que aquí no queda nada por renombrar.
    if not TopicModel.objects.filter(topic_name=new_topic).exists():
        TopicModel.objects.filter(topic_name=old_topic).update(
            topic_name=new_topic,
        )

    topic = TopicModel.objects.filter(topic_name=new_topic).first()

    if topic is None:
        return

    if not DocumentModel.objects.filter(
        topic_id=topic,
        document_name=new_document,
    ).exists():
        DocumentModel.objects.filter(
            topic_id=topic,
            document_name=old_document,
        ).update(document_name=new_document)


def apply_rename(apps, schema_editor):
    rename(apps, OLD_TOPIC, NEW_TOPIC, OLD_DOCUMENT, NEW_DOCUMENT)

    # Las descripciones son copia fija del catálogo: se reescriben
    # desde la estructura vigente.
    sync_knowledge_structure(
        apps.get_model('documents', 'TopicModel'),
        apps.get_model('documents', 'DocumentModel'),
    )


def undo_rename(apps, schema_editor):
    # Sin sembrar al final: la estructura vigente ya trae los nombres
    # nuevos y volvería a crear lo que este reverso acaba de deshacer.
    rename(apps, NEW_TOPIC, OLD_TOPIC, NEW_DOCUMENT, OLD_DOCUMENT)


class Migration(migrations.Migration):

    dependencies = [
        ('documents', '0001_squashed_0014_alter_documentmodel_content_and_more'),
    ]

    operations = [
        migrations.RunPython(apply_rename, undo_rename),
    ]
