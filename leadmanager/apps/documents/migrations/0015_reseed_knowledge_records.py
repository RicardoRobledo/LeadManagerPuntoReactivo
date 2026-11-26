"""
Vuelve a sembrar los registros canónicos de la base de conocimiento.

El proyecto reemplazó sus migraciones originales 0001-0014 por una
migración compactada. Algunas bases conservan aquel historial como
aplicado aunque sus registros de knowledge se hayan eliminado después.
Esta migración usa el siguiente número libre del historial completo para
evitar colisiones y obliga a ejecutar de nuevo la sincronización.

La sincronización es idempotente: crea únicamente topics y documentos
faltantes, actualiza las descripciones canónicas y nunca reemplaza el
contenido que ya se haya cargado en un documento.
"""

from django.db import migrations

from leadmanager.apps.documents.knowledge_structure import (
    sync_knowledge_structure,
)


def reseed_knowledge_records(apps, schema_editor):
    """Crea de nuevo los registros faltantes de knowledge."""
    TopicModel = apps.get_model('documents', 'TopicModel')
    DocumentModel = apps.get_model('documents', 'DocumentModel')

    sync_knowledge_structure(TopicModel, DocumentModel)


class Migration(migrations.Migration):

    dependencies = [
        ('documents', '0004_add_proof_documents'),
    ]

    operations = [
        # El reverso es deliberadamente vacío: bajar una migración no
        # debe borrar knowledge ni contenido que un usuario ya cargó.
        migrations.RunPython(
            reseed_knowledge_records,
            migrations.RunPython.noop,
        ),
    ]
