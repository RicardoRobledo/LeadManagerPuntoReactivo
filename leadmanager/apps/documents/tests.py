import importlib
import io
import json

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from pypdf import PdfWriter

from .knowledge_structure import (
    KNOWLEDGE_STRUCTURE,
    sync_knowledge_structure,
)
from .models import MAX_TEXT_LENGTH, TopicModel, DocumentModel


def build_pdf_from_streams(streams):
    """
    Builds a valid PDF from the drawing stream of each page.

    Se arma a mano para no sumar una librería de generación de PDF que
    solo haría falta en las pruebas.
    """
    catalog, page_tree, font = 1, 2, 3
    page_numbers = [4 + index * 2 for index in range(len(streams))]
    kids = b' '.join(b'%d 0 R' % number for number in page_numbers)

    objects = [
        b'<< /Type /Catalog /Pages %d 0 R >>' % page_tree,
        b'<< /Type /Pages /Kids [' + kids + b'] /Count %d >>' % len(streams),
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    ]

    for number, stream in zip(page_numbers, streams):
        objects.append(
            b'<< /Type /Page /Parent %d 0 R /MediaBox [0 0 300 300] '
            b'/Resources << /Font << /F1 %d 0 R >> >> '
            b'/Contents %d 0 R >>' % (page_tree, font, number + 1)
        )
        objects.append(
            b'<< /Length %d >>\nstream\n%s\nendstream' % (len(stream), stream)
        )

    document = bytearray(b'%PDF-1.4\n')
    offsets = []

    for number, body in enumerate(objects, start=catalog):
        offsets.append(len(document))
        document += b'%d 0 obj\n%s\nendobj\n' % (number, body)

    # La tabla xref guarda dónde empieza cada objeto, así que se escribe
    # al final, cuando ya se conocen las posiciones.
    start_xref = len(document)
    document += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objects) + 1)

    for offset in offsets:
        document += b'%010d 00000 n \n' % offset

    document += (
        b'trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n'
        % (len(objects) + 1, catalog, start_xref)
    )

    return bytes(document)


def build_pdf(pages=('Texto extraido del documento',)):
    """PDF con un renglón de texto por página."""
    return build_pdf_from_streams([
        b'BT /F1 12 Tf 20 150 Td (%s) Tj ET' % text.encode('latin-1')
        for text in pages
    ])


def build_pdf_without_text(page_count=1):
    """PDF con páginas en blanco: es como se ve un escaneo."""
    writer = PdfWriter()

    for _ in range(page_count):
        writer.add_blank_page(width=300, height=300)

    buffer = io.BytesIO()
    writer.write(buffer)

    return buffer.getvalue()


class KnowledgeStructureSeedTests(TestCase):
    """La estructura se siembra por migración al crear la base."""

    def test_all_sections_exist(self):
        for section in KNOWLEDGE_STRUCTURE:
            self.assertTrue(
                TopicModel.objects.filter(
                    topic_name=section['name']
                ).exists(),
                msg=f"Falta la sección {section['name']}",
            )

    def test_all_sections_have_description(self):
        for section in KNOWLEDGE_STRUCTURE:
            topic = TopicModel.objects.get(topic_name=section['name'])
            self.assertEqual(topic.description, section['description'])

        self.assertFalse(
            TopicModel.objects.filter(description='').exists()
        )

    def test_sync_repairs_a_wrong_description(self):
        section = KNOWLEDGE_STRUCTURE[0]

        topic = TopicModel.objects.get(topic_name=section['name'])
        topic.description = 'descripción incorrecta'
        topic.save()

        sync_knowledge_structure(TopicModel, DocumentModel)

        topic.refresh_from_db()
        self.assertEqual(topic.description, section['description'])

    def test_all_document_slots_exist_and_start_empty(self):
        expected_total = sum(
            len(section['documents'])
            for section in KNOWLEDGE_STRUCTURE
        )

        self.assertEqual(DocumentModel.objects.count(), expected_total)

        for section in KNOWLEDGE_STRUCTURE:
            topic = TopicModel.objects.get(topic_name=section['name'])

            # Cada documento es {name, description}: la descripción
            # vive en el documento, no en la sección.
            for entry in section['documents']:
                document = DocumentModel.objects.filter(
                    topic_id=topic,
                    document_name=entry['name'],
                ).first()

                self.assertIsNotNone(
                    document,
                    msg=f"Falta el documento {entry['name']}",
                )
                self.assertEqual(document.content, '')
                self.assertEqual(document.description, entry['description'])

    def test_sync_is_idempotent_and_keeps_content(self):
        topic = TopicModel.objects.get(
            topic_name=KNOWLEDGE_STRUCTURE[0]['name'],
        )
        document = topic.documentmodel_set.first()
        document.content = 'contenido ya cargado'
        document.save()

        topics_before = TopicModel.objects.count()
        documents_before = DocumentModel.objects.count()

        created = sync_knowledge_structure(TopicModel, DocumentModel)

        self.assertEqual(created, (0, 0))
        self.assertEqual(TopicModel.objects.count(), topics_before)
        self.assertEqual(DocumentModel.objects.count(), documents_before)

        document.refresh_from_db()
        self.assertEqual(document.content, 'contenido ya cargado')


class ReseedKnowledgeStructureMigrationTests(TestCase):
    """La migración de reparación repone registros sin perder contenido."""

    migration = importlib.import_module(
        'leadmanager.apps.documents.migrations.'
        '0015_reseed_knowledge_records'
    )

    def test_reseed_restores_missing_records_and_keeps_loaded_content(self):
        first_section = KNOWLEDGE_STRUCTURE[0]
        first_topic = TopicModel.objects.get(
            topic_name=first_section['name'],
        )
        loaded_document = DocumentModel.objects.get(
            topic_id=first_topic,
            document_name=first_section['documents'][0]['name'],
        )
        loaded_document.content = 'contenido que debe conservarse'
        loaded_document.save(update_fields=['content'])

        # Simula la base reportada: faltan un topic completo y otro
        # documento, aunque el historial de migraciones sigue aplicado.
        missing_section = KNOWLEDGE_STRUCTURE[-1]
        TopicModel.objects.filter(
            topic_name=missing_section['name'],
        ).delete()
        DocumentModel.objects.filter(
            topic_id=first_topic,
            document_name=first_section['documents'][1]['name'],
        ).delete()

        self.migration.reseed_knowledge_records(django_apps, None)

        self.assertEqual(
            TopicModel.objects.count(),
            len(KNOWLEDGE_STRUCTURE),
        )
        self.assertEqual(
            DocumentModel.objects.count(),
            sum(len(section['documents']) for section in KNOWLEDGE_STRUCTURE),
        )

        loaded_document.refresh_from_db()
        self.assertEqual(
            loaded_document.content,
            'contenido que debe conservarse',
        )


class RenameProductsSectionTests(TestCase):
    """La sección de productos pasó a llamarse «Productos y servicios»."""

    # El nombre del módulo empieza con dígito, así que no se puede
    # importar con un import normal.
    migration = importlib.import_module(
        'leadmanager.apps.documents.migrations.0002_rename_products_section'
    )

    def rename_back(self, content=''):
        """Deja la base como estaba antes de la migración."""
        topic = TopicModel.objects.get(topic_name=self.migration.NEW_TOPIC)
        topic.topic_name = self.migration.OLD_TOPIC
        topic.save(update_fields=['topic_name'])

        document = DocumentModel.objects.get(
            topic_id=topic,
            document_name=self.migration.NEW_DOCUMENT,
        )
        document.document_name = self.migration.OLD_DOCUMENT
        document.content = content
        document.save(update_fields=['document_name', 'content'])

        return topic, document

    def test_the_structure_already_seeds_the_new_names(self):
        self.assertTrue(
            TopicModel.objects
            .filter(topic_name=self.migration.NEW_TOPIC)
            .exists()
        )
        self.assertFalse(
            TopicModel.objects
            .filter(topic_name=self.migration.OLD_TOPIC)
            .exists()
        )

    def test_rename_keeps_the_loaded_content(self):
        topic, document = self.rename_back(content='Ficha ya cargada')

        self.migration.apply_rename(django_apps, None)

        topic.refresh_from_db()
        document.refresh_from_db()

        self.assertEqual(topic.topic_name, self.migration.NEW_TOPIC)
        self.assertEqual(document.document_name, self.migration.NEW_DOCUMENT)
        # Lo que importa: renombrar no puede costar el contenido.
        self.assertEqual(document.content, 'Ficha ya cargada')

    def test_rename_does_not_duplicate_the_section(self):
        self.rename_back()

        topics_before = TopicModel.objects.count()
        documents_before = DocumentModel.objects.count()

        self.migration.apply_rename(django_apps, None)

        self.assertEqual(TopicModel.objects.count(), topics_before)
        self.assertEqual(DocumentModel.objects.count(), documents_before)

    def test_rename_is_idempotent(self):
        # En una base nueva la siembra ya trae el nombre nuevo y esta
        # migración no debe encontrar nada que hacer.
        topics_before = TopicModel.objects.count()
        documents_before = DocumentModel.objects.count()

        self.migration.apply_rename(django_apps, None)
        self.migration.apply_rename(django_apps, None)

        self.assertEqual(TopicModel.objects.count(), topics_before)
        self.assertEqual(DocumentModel.objects.count(), documents_before)

    def test_the_document_describes_both(self):
        document = DocumentModel.objects.get(
            document_name=self.migration.NEW_DOCUMENT,
        )

        self.assertIn('servicio', document.description)


class DocumentsCrudTests(TestCase):

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='tester',
            password='test-pass-123',
        )
        self.client.force_login(self.user)

        self.topic = TopicModel.objects.create(topic_name='Gemas')

    # ==========================================
    # TOPICS
    # ==========================================

    def test_create_topic(self):
        response = self.client.post(
            reverse('app_documents:topic_list_create'),
            data=json.dumps({'topic_name': 'Procesos de fabricacion'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            TopicModel.objects
            .filter(topic_name='Procesos de fabricacion')
            .exists()
        )

    def test_create_topic_requires_name(self):
        response = self.client.post(
            reverse('app_documents:topic_list_create'),
            data=json.dumps({'topic_name': '   '}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)

    def test_list_topics(self):
        response = self.client.get(
            reverse('app_documents:topic_list_create')
        )

        self.assertEqual(response.status_code, 200)
        # Las 6 secciones sembradas por migración mas la de setUp.
        self.assertEqual(
            response.json()['count'],
            len(KNOWLEDGE_STRUCTURE) + 1,
        )

    def test_update_topic(self):
        response = self.client.put(
            reverse(
                'app_documents:topic_detail',
                kwargs={'topic_id': self.topic.id},
            ),
            data=json.dumps({'topic_name': 'Gemologia'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.topic.refresh_from_db()
        self.assertEqual(self.topic.topic_name, 'Gemologia')

    def test_delete_topic_cascades_documents(self):
        DocumentModel.objects.create(
            document_name='Doc',
            content='contenido',
            topic_id=self.topic,
        )

        response = self.client.delete(
            reverse(
                'app_documents:topic_detail',
                kwargs={'topic_id': self.topic.id},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['documents_deleted'], 1)
        self.assertFalse(
            DocumentModel.objects.filter(topic_id=self.topic.id).exists()
        )

    # ==========================================
    # DOCUMENTS
    # ==========================================

    def test_create_document(self):
        response = self.client.post(
            reverse('app_documents:document_list_create'),
            data=json.dumps({
                'document_name': 'Propuesta de valor',
                'content': 'Texto extraido del documento.',
                'topic_id': self.topic.id,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            response.json()['document']['topic_name'],
            'Gemas',
        )

    def test_create_document_with_unknown_topic(self):
        response = self.client.post(
            reverse('app_documents:document_list_create'),
            data=json.dumps({
                'document_name': 'Doc',
                'content': 'contenido',
                'topic_id': 99999,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 404)

    def test_filter_documents_by_topic(self):
        other_topic = TopicModel.objects.create(topic_name='Joyeria')

        DocumentModel.objects.create(
            document_name='A',
            content='a',
            topic_id=self.topic,
        )
        DocumentModel.objects.create(
            document_name='B',
            content='b',
            topic_id=other_topic,
        )

        response = self.client.get(
            reverse('app_documents:document_list_create'),
            {'topic_id': other_topic.id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 1)
        self.assertEqual(
            response.json()['documents'][0]['document_name'],
            'B',
        )

    def test_update_document(self):
        document = DocumentModel.objects.create(
            document_name='A',
            content='a',
            topic_id=self.topic,
        )

        response = self.client.patch(
            reverse(
                'app_documents:document_detail',
                kwargs={'document_id': document.id},
            ),
            data=json.dumps({'content': 'contenido actualizado'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        document.refresh_from_db()
        self.assertEqual(document.content, 'contenido actualizado')
        self.assertEqual(document.document_name, 'A')

    def test_delete_document(self):
        document = DocumentModel.objects.create(
            document_name='A',
            content='a',
            topic_id=self.topic,
        )

        response = self.client.delete(
            reverse(
                'app_documents:document_detail',
                kwargs={'document_id': document.id},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            DocumentModel.objects.filter(pk=document.id).exists()
        )

    def test_clear_content_marks_document_as_pending(self):
        document = DocumentModel.objects.create(
            document_name='A',
            content='contenido',
            topic_id=self.topic,
        )

        response = self.client.patch(
            reverse(
                'app_documents:document_detail',
                kwargs={'document_id': document.id},
            ),
            data=json.dumps({'content': ''}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['document']['is_loaded'])

        document.refresh_from_db()
        self.assertEqual(document.content, '')

    # ==========================================
    # LIMITE DE CARACTERES
    # ==========================================

    def test_response_reports_the_length_of_the_content(self):
        document = DocumentModel.objects.create(
            document_name='A',
            content='hola',
            topic_id=self.topic,
        )

        response = self.client.get(
            reverse(
                'app_documents:document_detail',
                kwargs={'document_id': document.id},
            )
        )

        report = response.json()['document']['content_length']

        self.assertEqual(report['length'], 4)
        self.assertEqual(report['max_length'], MAX_TEXT_LENGTH)
        self.assertTrue(report['length_is_valid'])

    def test_content_of_exactly_the_limit_is_accepted(self):
        document = DocumentModel.objects.create(
            document_name='A',
            topic_id=self.topic,
        )

        response = self.client.patch(
            reverse(
                'app_documents:document_detail',
                kwargs={'document_id': document.id},
            ),
            data=json.dumps({'content': 'a' * MAX_TEXT_LENGTH}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            response.json()['document']['content_length']['length_is_valid']
        )

        document.refresh_from_db()
        self.assertEqual(len(document.content), MAX_TEXT_LENGTH)

    def test_content_over_the_limit_is_rejected(self):
        document = DocumentModel.objects.create(
            document_name='A',
            content='contenido previo',
            topic_id=self.topic,
        )

        response = self.client.patch(
            reverse(
                'app_documents:document_detail',
                kwargs={'document_id': document.id},
            ),
            data=json.dumps({'content': 'a' * (MAX_TEXT_LENGTH + 1)}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)

        payload = response.json()
        self.assertEqual(payload['field'], 'content')
        self.assertEqual(payload['length'], MAX_TEXT_LENGTH + 1)
        self.assertEqual(payload['max_length'], MAX_TEXT_LENGTH)
        self.assertFalse(payload['length_is_valid'])

        document.refresh_from_db()
        self.assertEqual(document.content, 'contenido previo')

    def test_description_over_the_limit_is_rejected(self):
        document = DocumentModel.objects.create(
            document_name='A',
            topic_id=self.topic,
        )

        response = self.client.patch(
            reverse(
                'app_documents:document_detail',
                kwargs={'document_id': document.id},
            ),
            data=json.dumps({'description': 'a' * (MAX_TEXT_LENGTH + 1)}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['field'], 'description')

    def test_creating_a_document_over_the_limit_is_rejected(self):
        response = self.client.post(
            reverse('app_documents:document_list_create'),
            data=json.dumps({
                'document_name': 'A',
                'content': 'a' * (MAX_TEXT_LENGTH + 1),
                'topic_id': self.topic.id,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(DocumentModel.objects.filter(document_name='A').exists())

    def test_topic_description_over_the_limit_is_rejected(self):
        response = self.client.post(
            reverse('app_documents:topic_list_create'),
            data=json.dumps({
                'topic_name': 'Nueva seccion',
                'description': 'a' * (MAX_TEXT_LENGTH + 1),
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(
            TopicModel.objects.filter(topic_name='Nueva seccion').exists()
        )

    def test_stored_text_over_the_limit_is_reported_as_invalid(self):
        # Un registro guardado antes de que existiera el tope: la
        # respuesta tiene que delatarlo, no callarlo.
        document = DocumentModel.objects.create(
            document_name='A',
            content='a' * (MAX_TEXT_LENGTH + 10),
            topic_id=self.topic,
        )

        response = self.client.get(
            reverse(
                'app_documents:document_detail',
                kwargs={'document_id': document.id},
            )
        )

        self.assertFalse(
            response.json()['document']['content_length']['length_is_valid']
        )

    # ==========================================
    # SUBIDA DE PDF
    # ==========================================

    def upload(self, document, content, name='documento.pdf',
               content_type='application/pdf'):
        return self.client.post(
            reverse(
                'app_documents:document_upload',
                kwargs={'document_id': document.id},
            ),
            {'file': SimpleUploadedFile(name, content, content_type)},
        )

    def test_upload_pdf_saves_its_text_as_content(self):
        document = DocumentModel.objects.create(
            document_name='Catalogo',
            topic_id=self.topic,
        )

        response = self.upload(document, build_pdf(('Anillo de oro 14k',)))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['pages'], 1)
        self.assertTrue(response.json()['document']['is_loaded'])

        document.refresh_from_db()
        self.assertIn('Anillo de oro 14k', document.content)

    def test_upload_reads_every_page(self):
        document = DocumentModel.objects.create(
            document_name='Catalogo',
            topic_id=self.topic,
        )

        response = self.upload(
            document,
            build_pdf(('Primera pagina', 'Segunda pagina')),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['pages'], 2)

        document.refresh_from_db()
        self.assertIn('Primera pagina', document.content)
        self.assertIn('Segunda pagina', document.content)

    def test_upload_rejects_a_file_that_is_not_pdf(self):
        document = DocumentModel.objects.create(
            document_name='Catalogo',
            content='contenido previo',
            topic_id=self.topic,
        )

        response = self.upload(
            document,
            b'texto plano',
            name='notas.txt',
            content_type='text/plain',
        )

        self.assertEqual(response.status_code, 400)

        # Un archivo rechazado no debe pisar lo que ya estaba cargado.
        document.refresh_from_db()
        self.assertEqual(document.content, 'contenido previo')

    def test_upload_rejects_a_file_only_named_pdf(self):
        document = DocumentModel.objects.create(
            document_name='Catalogo',
            topic_id=self.topic,
        )

        response = self.upload(document, b'esto no es un PDF')

        self.assertEqual(response.status_code, 400)

        document.refresh_from_db()
        self.assertEqual(document.content, '')

    def test_upload_rejects_an_empty_file(self):
        document = DocumentModel.objects.create(
            document_name='Catalogo',
            topic_id=self.topic,
        )

        response = self.upload(document, b'')

        self.assertEqual(response.status_code, 400)

    def test_upload_rejects_a_pdf_without_selectable_text(self):
        document = DocumentModel.objects.create(
            document_name='Catalogo',
            topic_id=self.topic,
        )

        response = self.upload(document, build_pdf_without_text())

        self.assertEqual(response.status_code, 422)

        # El documento sigue pendiente: no se guardó contenido vacío.
        document.refresh_from_db()
        self.assertEqual(document.content, '')

    def test_upload_rejects_a_pdf_with_more_text_than_the_limit(self):
        document = DocumentModel.objects.create(
            document_name='Catalogo',
            topic_id=self.topic,
        )

        response = self.upload(
            document,
            build_pdf(('a' * (MAX_TEXT_LENGTH + 100),)),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['field'], 'content')
        self.assertEqual(response.json()['max_length'], MAX_TEXT_LENGTH)

        document.refresh_from_db()
        self.assertEqual(document.content, '')

    def test_upload_requires_a_file(self):
        document = DocumentModel.objects.create(
            document_name='Catalogo',
            topic_id=self.topic,
        )

        response = self.client.post(
            reverse(
                'app_documents:document_upload',
                kwargs={'document_id': document.id},
            )
        )

        self.assertEqual(response.status_code, 400)

    # ==========================================
    # AUTENTICACION
    # ==========================================

    def test_upload_requires_login(self):
        document = DocumentModel.objects.create(
            document_name='Catalogo',
            topic_id=self.topic,
        )

        self.client.logout()

        response = self.upload(document, build_pdf())

        self.assertEqual(response.status_code, 302)

        document.refresh_from_db()
        self.assertEqual(document.content, '')

    def test_requires_login(self):
        self.client.logout()

        response = self.client.get(
            reverse('app_documents:document_list_create')
        )

        self.assertEqual(response.status_code, 302)
