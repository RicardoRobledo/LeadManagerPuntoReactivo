from collections import Counter
import smtplib
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from diagnostico.preparar_recordatorios import configurar, inspeccionar
from diagnostico.probar_recordatorios import simular
from leadmanager.apps.automations.tasks import send_reminders_to_stale_conversations
from leadmanager.apps.automations.models import EmailDeliveryLogModel
from leadmanager.apps.prospects.models import ConversationModel, ProspectModel


class PrepararRecordatoriosTests(TestCase):
    @mock.patch('leadmanager.apps.automations.services.imap_service.smtplib.SMTP')
    def test_conserva_bitacora_real_del_servicio_tras_revertir_datos_de_prueba(self, smtp):
        lote = 'prueba-recordatorios-bitacora'
        configurar(lote, timezone.now())
        smtp.return_value.__enter__.return_value.sendmail.return_value = {}

        resultado = simular(lote, todos_en_proceso=True, enviar_a='yupaglu@gmail.com')

        registros = EmailDeliveryLogModel.objects.all()
        self.assertEqual(set(resultado['ids_bitacora']), set(registros.values_list('pk', flat=True)))
        self.assertEqual(registros.count(), 3)
        self.assertEqual(set(registros.values_list('source', flat=True)), {'form', 'web', 'email'})
        self.assertTrue(all(registro.status == 'smtp_accepted' for registro in registros))
        self.assertTrue(all(registro.recipient == 'yupaglu@gmail.com' for registro in registros))
        self.assertTrue(all(registro.message_id and registro.finished_at for registro in registros))
        self.assertTrue(resultado['datos_originales_conservados'])
        self.assertFalse(ProspectModel.objects.exclude(email=None).exists())
        self.assertEqual([(fila['canal'], fila['semaforo']) for fila in inspeccionar(lote, timezone.now())
                          if fila['cumple_filtros']], [('email', 'amarillo')])

    @mock.patch('leadmanager.apps.automations.services.imap_service.smtplib.SMTP')
    def test_bitacora_de_prueba_conserva_fallos_sin_convertirlos_en_exitos(self, smtp):
        lote = 'prueba-recordatorios-bitacora-fallo'
        configurar(lote, timezone.now())
        smtp.return_value.__enter__.return_value.sendmail.side_effect = [
            smtplib.SMTPDataError(554, b'Prueba rechazada'), {}, {},
        ]
        with self.assertLogs('leadmanager.apps.automations.services.imap_service', level='ERROR'):
            resultado = simular(lote, todos_en_proceso=True, enviar_a='yupaglu@gmail.com')

        self.assertEqual(EmailDeliveryLogModel.objects.filter(status='failed').count(), 1)
        self.assertEqual(EmailDeliveryLogModel.objects.filter(status='smtp_accepted').count(), 2)
        self.assertEqual(len(resultado['ids_fallidos']), 1)
        self.assertEqual(len(resultado['ids_aceptados_smtp']), 2)
        self.assertTrue(resultado['datos_originales_conservados'])

    @mock.patch('leadmanager.apps.automations.tasks.send_reminder_email')
    def test_seis_casos_sin_envios_automaticos_y_sin_duplicar_al_repetir(self, smtp):
        lote = 'prueba-recordatorios-seis-casos'
        ahora = timezone.now()
        self.assertTrue(configurar(lote, ahora))
        self.assertFalse(configurar(lote, ahora))
        filas = inspeccionar(lote, ahora)

        self.assertEqual(len(filas), 6)
        self.assertEqual(Counter(f['canal'] for f in filas), {'form': 2, 'web': 2, 'email': 2})
        self.assertEqual(sum(f['escalada'] for f in filas), 3)
        self.assertTrue(all(f['antiguedad_horas'] >= 49 for f in filas))
        self.assertEqual([(f['canal'], f['semaforo']) for f in filas if f['cumple_filtros']],
                         [('email', 'amarillo')])
        self.assertFalse(any(f['podria_intentar_envio'] for f in filas))
        self.assertEqual(ProspectModel.objects.count(), 6)

        self.assertEqual(send_reminders_to_stale_conversations.run(), {'procesados': 0})
        smtp.assert_not_called()
        self.assertFalse(ConversationModel.objects.exclude(email_reminder_phase=None).exists())

    def test_la_comparacion_de_canales_se_revierte_y_no_habilita_envios(self):
        lote = 'prueba-recordatorios-comparacion'
        configurar(lote, timezone.now())

        resultado = simular(lote, todos_en_proceso=True)

        self.assertEqual([escenario['resultado']['simulados'] for escenario in resultado['escenarios']], [1, 3])
        self.assertTrue(resultado['datos_originales_conservados'])
        self.assertFalse(ProspectModel.objects.exclude(email=None).exists())
        self.assertFalse(ConversationModel.objects.exclude(email_reminder_phase=None).exists())
        filas = inspeccionar(lote, timezone.now())
        self.assertEqual([(fila['canal'], fila['semaforo']) for fila in filas if fila['cumple_filtros']],
                         [('email', 'amarillo')])

    @mock.patch('leadmanager.apps.automations.services.imap_service.send_reminder_email', return_value=True)
    def test_envio_real_de_prueba_solo_va_al_destino_indicado_y_no_persiste_cambios(self, smtp):
        lote = 'prueba-recordatorios-destino-unico'
        configurar(lote, timezone.now())

        resultado = simular(lote, todos_en_proceso=True, enviar_a='yupaglu@gmail.com')

        self.assertEqual(smtp.call_count, 3)
        self.assertTrue(all(call.args[0] == 'yupaglu@gmail.com' for call in smtp.call_args_list))
        for channel in ('Formulario', 'Chatweb', 'Correo'):
            self.assertTrue(any(f'[PRUEBA][{channel}]' in call.args[1] for call in smtp.call_args_list))
        self.assertEqual(resultado['escenarios'][0]['resultado'], {'procesados': 3})
        self.assertEqual(len(resultado['ids_aceptados_smtp']), 3)
        self.assertEqual(resultado['ids_fallidos'], [])
        self.assertTrue(resultado['smtp_real'])
        self.assertFalse(ProspectModel.objects.exclude(email=None).exists())
        self.assertFalse(ConversationModel.objects.exclude(email_reminder_phase=None).exists())

    @mock.patch('leadmanager.apps.automations.services.imap_service.send_reminder_email', return_value=False)
    def test_un_rechazo_smtp_de_prueba_no_se_cuenta_como_envio_aceptado(self, smtp):
        lote = 'prueba-recordatorios-rechazo'
        configurar(lote, timezone.now())
        with self.assertLogs('leadmanager.apps.automations.tasks', level='INFO'):
            resultado = simular(lote, todos_en_proceso=True, enviar_a='yupaglu@gmail.com')

        self.assertEqual(smtp.call_count, 3)
        self.assertEqual(resultado['ids_aceptados_smtp'], [])
        self.assertEqual(len(resultado['ids_fallidos']), 3)
        self.assertEqual(resultado['escenarios'][0]['resultado'], {'procesados': 0})
