"""La prueba individual solo procesa su conversación y no duplica envíos."""

import smtplib
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from diagnostico.probar_recordatorio_schema_unico import probar
from leadmanager.apps.automations.models import EmailDeliveryLogModel
from leadmanager.apps.prospects.models import ChatHistoryModel, ConversationModel


@override_settings(
    EMAIL_HOST='smtp.example.test', EMAIL_PORT=587, EMAIL_USE_TLS=True,
    EMAIL_HOST_USER='sofia@example.test', EMAIL_HOST_PASSWORD='clave-de-prueba',
)
class RecordatorioSchemaUnicoTests(TestCase):
    def setUp(self):
        patcher = mock.patch('leadmanager.apps.automations.services.imap_service.smtplib.SMTP')
        self.smtp = patcher.start()
        self.addCleanup(patcher.stop)
        self.server = self.smtp.return_value.__enter__.return_value
        self.server.sendmail.return_value = {}

    def test_previa_no_escribe_y_envio_solo_procesa_la_conversacion_de_prueba(self):
        other = ConversationModel.objects.create(source='email', session_id='otro@example.test')
        ConversationModel.objects.filter(pk=other.pk).update(
            created_at=timezone.now() - timedelta(hours=49),
        )
        ChatHistoryModel.objects.create(conversation=other, message={
            'role': 'assistant', 'content': {'crm_update': {'semaforo': 'amarillo'}},
        })

        preview = probar('desarrollo@nextwave.mx')
        self.assertIn('vista_previa', preview['accion'])
        self.assertEqual(ConversationModel.objects.count(), 1)
        self.smtp.assert_not_called()

        result = probar('desarrollo@nextwave.mx', enviar=True)
        self.assertEqual(result['resultado_tarea'], {'procesados': 1})
        self.assertEqual(result['origen_destinatario'], 'crm_update.contacto.correo')
        self.assertEqual(result['estado'], 'smtp_accepted')
        self.assertEqual(ConversationModel.objects.count(), 2)
        created = ConversationModel.objects.get(pk=result['conversacion_id'])
        self.assertGreater(created.created_at, timezone.now() - timedelta(minutes=1))
        self.assertIn('desarrollo@nextwave.mx', created.prospect.name)
        self.assertIn(f'open_conversation={created.pk}', result['abrir_conversacion'])
        user = get_user_model().objects.create_user(username='revisor-prueba')
        self.client.force_login(user)
        page = self.client.get(reverse('app_prospects:conversations'), {
            'interval': 'today', 'semaforo': 'amarillo',
            'search_email': 'desarrollo@nextwave.mx',
        })
        self.assertEqual(page.status_code, 200)
        self.assertEqual(list(page.context['page_obj']), [created])
        self.assertEqual(EmailDeliveryLogModel.objects.count(), 1)
        attempt = EmailDeliveryLogModel.objects.get()
        self.assertEqual(attempt.recipient, 'desarrollo@nextwave.mx')
        self.assertEqual(result['abrir_en_correos'],
                         f'/leadmanager-admin/automations/emails/{attempt.pk}/')
        self.assertTrue(attempt.subject.startswith('[PRUEBA]'))
        self.server.sendmail.assert_called_once()
        ConversationModel.objects.filter(pk=created.pk).update(
            created_at=timezone.now() - timedelta(hours=49),
        )
        self.assertEqual(probar('desarrollo@nextwave.mx', enviar=True)['estado'], 'smtp_accepted')
        created.refresh_from_db()
        self.assertGreater(created.created_at, timezone.now() - timedelta(minutes=1))
        self.server.sendmail.assert_called_once()
        other.refresh_from_db()
        self.assertIsNone(other.email_reminder_phase)

    def test_fallo_smtp_no_deja_la_prueba_lista_para_reintento_inmediato(self):
        self.server.sendmail.side_effect = smtplib.SMTPDataError(554, b'Rechazado')
        with self.assertLogs('leadmanager.apps.automations.services.imap_service', level='ERROR'):
            result = probar('desarrollo@nextwave.mx', enviar=True)

        self.assertEqual(result['resultado_tarea'], {'procesados': 0})
        self.assertEqual(result['estado'], 'failed')
        conversation = ConversationModel.objects.get(pk=result['conversacion_id'])
        self.assertIsNone(conversation.email_reminder_phase)
        self.assertGreater(conversation.created_at, timezone.now() - timedelta(minutes=1))
