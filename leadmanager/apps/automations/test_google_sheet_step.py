"""Registro inicial en Sheets: cola aislada, identidad original e ID estable."""

from copy import deepcopy
from datetime import datetime, timezone as datetime_timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from celery import current_app
from celery.exceptions import Retry
from django.db import connection
from django.test import SimpleTestCase, TestCase, TransactionTestCase

from leadmanager.apps.prospects.models import ChatHistoryModel, ConversationModel, ProspectModel

from . import google_sheets_tasks as sheets_tasks
from . import tasks
from .agents.prospector_agent import prospector_steps as steps
from .agents.prospector_agent.prospector import PRODUCTION_STEPS
from .agents.prospector_agent.shadow_steps import SHADOW_STEPS
from .models import AgentModel
from .services.google_sheets_service import GoogleSheetsError


TASK_PATH = (
    'leadmanager.apps.automations.agents.prospector_agent.prospector_steps.'
    'register_google_sheet_lead_task'
)
ARRIVAL = datetime(2026, 11, 12, 23, 30, tzinfo=datetime_timezone.utc)


class GoogleSheetStepTests(SimpleTestCase):
    def setUp(self):
        self.agent = SimpleNamespace(mode=AgentModel.ModeEnum.PRODUCTION, log_event=Mock())
        self.conversation = SimpleNamespace(pk=178, id=178, created_at=ARRIVAL)

    def context(self, source, payload, **extra):
        return {
            'source': source,
            source: payload,
            'conversation': self.conversation,
            'source_identifier': 'identificador@example.test',
            'history': [{'role': 'user', 'content': 'Mensaje original'}],
            'response_to_prospect': {'crm_update': {'contacto': {}}},
            # Una vinculación histórica incorrecta no debe completar este registro.
            'person': {
                'data': {'email': 'incorrecto@example.test', 'name': 'Nombre incorrecto'},
                'db_data': {'email': 'incorrecto@example.test', 'enterprise': 'Empresa incorrecta'},
            },
            **extra,
        }

    def enqueue(self, ctx):
        with patch(TASK_PATH + '.delay', return_value=SimpleNamespace(id='sheets-task')) as send:
            steps.register_google_sheet_step(self.agent, ctx)
        send.assert_called_once()
        payload = send.call_args.args[0]
        self.assertEqual(payload['conversation_id'], 178)
        self.assertEqual(payload['received_at'], ARRIVAL.isoformat())
        self.assertTrue(ctx['_google_sheets_registration_attempted'])
        self.assertEqual(ctx['google_sheet_registration']['status'], 'queued')
        self.assertEqual(ctx['google_sheet_registration']['task_id'], 'sheets-task')
        return payload

    def test_step_is_immediately_after_response_generation_only_in_production(self):
        index = PRODUCTION_STEPS.index(steps.generate_prospect_response_step)
        self.assertIs(PRODUCTION_STEPS[index + 1], steps.register_google_sheet_step)
        self.assertNotIn(steps.register_google_sheet_step, SHADOW_STEPS)

    def test_web_keeps_received_values_and_preserves_input(self):
        original = {
            'name': 'Ana original', 'email': 'ana@example.test',
            'phone': '+525500001234', 'company_name': 'Empresa original',
            'business_unit': 'ODOO', 'message': 'Quiero información',
        }
        ctx = self.context('web', deepcopy(original))
        history = deepcopy(ctx['history'])
        payload = self.enqueue(ctx)
        self.assertEqual(payload['business_unit'], 'ODOO')
        self.assertEqual(payload['email'], original['email'])
        self.assertEqual(payload['name'], original['name'])
        self.assertEqual(payload['phone'], original['phone'])
        self.assertEqual(payload['enterprise'], original['company_name'])
        self.assertEqual(ctx['web'], original)
        self.assertEqual(ctx['history'], history)

    def test_form_uses_current_crm_to_complete_company_and_unit_without_linked_prospect(self):
        self.conversation.prospect = SimpleNamespace(
            name='Prospecto incorrecto', email='incorrecto@example.test', enterprise='Otra empresa',
        )
        ctx = self.context('form', {
            'name': 'Nombre recibido', 'email': 'recibido@example.test', 'phone': '5551234567',
        }, response_to_prospect={'crm_update': {
            'unidad_negocio_detectada': 'ODOO', 'contacto': {
                'nombre': 'Nombre del análisis', 'correo': 'modelo@example.test',
                'telefono': '9999999999', 'empresa': 'Empresa detectada en la conversación',
            },
        }})
        payload = self.enqueue(ctx)
        self.assertEqual(payload['business_unit'], 'ODOO')
        self.assertEqual(payload['email'], 'recibido@example.test')
        self.assertEqual(payload['name'], 'Nombre recibido')
        self.assertEqual(payload['phone'], '5551234567')
        self.assertEqual(payload['enterprise'], 'Empresa detectada en la conversación')

    def test_detected_unit_takes_priority_over_unit_received_by_web(self):
        ctx = self.context('web', {
            'email': 'ana@example.test', 'business_unit': 'BPM',
        }, response_to_prospect={'crm_update': {
            'unidad_negocio_detectada': 'ODOO', 'contacto': {},
        }})
        self.assertEqual(self.enqueue(ctx)['business_unit'], 'ODOO')

    def test_marco_form_exports_detected_company_and_unit_with_blank_null_phone(self):
        ctx = self.context('form', {
            'email': 'marco@example.test', 'name': 'Marco', 'phone': '',
            'message': 'Busco implementar Odoo en mi empresa',
        }, response_to_prospect={'crm_update': {
            'unidad_negocio_detectada': 'ODOO', 'escalar_a_humano': True,
            'contacto': {
                'nombre': 'Marco', 'correo': 'modelo-distinto@example.test',
                'empresa': 'Marambas', 'telefono': None,
            },
        }})
        payload = self.enqueue(ctx)
        self.assertEqual(payload['business_unit'], 'ODOO')
        self.assertEqual(payload['name'], 'Marco')
        self.assertEqual(payload['email'], 'marco@example.test')
        self.assertEqual(payload['enterprise'], 'Marambas')
        self.assertEqual(payload['phone'], '')

    def test_current_crm_contact_fills_missing_received_contact_values(self):
        response = {'crm_update': {'contacto': {
            'nombre': 'Nombre identificado', 'correo': 'crm@example.test',
            'telefono': '+525500009999', 'empresa': 'Empresa identificada',
        }}}
        ctx = self.context('whatsapp', {'message': 'Hola'},
                           source_identifier='', response_to_prospect=deepcopy(response))
        payload = self.enqueue(ctx)
        self.assertEqual(payload['name'], 'Nombre identificado')
        self.assertEqual(payload['email'], 'crm@example.test')
        self.assertEqual(payload['phone'], '+525500009999')
        self.assertEqual(payload['enterprise'], 'Empresa identificada')
        self.assertEqual(ctx['response_to_prospect'], response)

    def test_without_generated_crm_step_fails_before_enqueue(self):
        for response in (None, {}, {'crm_update': None}, {'crm_update': []}, {'crm_update': '{}'}):
            with self.subTest(response=response):
                ctx = self.context('form', {'email': 'ana@example.test'}, response_to_prospect=response)
                with patch(TASK_PATH + '.delay') as send:
                    with self.assertRaises(ValueError):
                        steps.register_google_sheet_step(self.agent, ctx)
                send.assert_not_called()
                self.assertFalse(ctx.get('_google_sheets_registration_attempted'))

    def test_null_crm_fields_do_not_fill_values_from_unrelated_prospect(self):
        ctx = self.context('form', {'email': 'ana@example.test'}, response_to_prospect={
            'crm_update': {'contacto': None, 'unidad_negocio_detectada': None},
        })
        payload = self.enqueue(ctx)
        self.assertEqual(payload['business_unit'], 'NO_DETERMINADO')
        self.assertEqual(payload['email'], 'ana@example.test')
        self.assertEqual(payload['name'], '')
        self.assertEqual(payload['phone'], '')
        self.assertEqual(payload['enterprise'], '')

    def test_email_uses_actual_sender_and_does_not_depend_on_qualification(self):
        for escalated in (False, True):
            with self.subTest(escalated=escalated):
                ctx = self.context('email', {
                    'from': {'email': 'remitente@example.test', 'name': 'Remitente'},
                    'body': 'Solo estoy consultando',
                }, response_to_prospect={'crm_update': {
                    'escalar_a_humano': escalated, 'semaforo': 'verde' if escalated else 'gris',
                }})
                payload = self.enqueue(ctx)
                self.assertEqual(payload['business_unit'], 'NO_DETERMINADO')
                self.assertEqual(payload['email'], 'remitente@example.test')
                self.assertEqual(payload['name'], 'Remitente')

    def test_company_field_aliases_are_taken_from_received_payload(self):
        for company_field in ('company_name', 'enterprise', 'empresa'):
            with self.subTest(field=company_field):
                ctx = self.context('form', {
                    'email': 'ana@example.test', 'name': 'Ana', company_field: 'Empresa recibida',
                })
                self.assertEqual(self.enqueue(ctx)['enterprise'], 'Empresa recibida')

    def test_phone_only_whatsapp_can_be_enqueued_with_blank_email(self):
        ctx = self.context('whatsapp', {'message': 'Hola'}, source_identifier='525500001234')
        payload = self.enqueue(ctx)
        self.assertEqual(payload['email'], '')
        self.assertEqual(payload['phone'], '525500001234')
        self.assertEqual(payload['business_unit'], 'NO_DETERMINADO')

    def test_shadow_mode_does_not_enqueue_any_external_write(self):
        self.agent.mode = AgentModel.ModeEnum.SHADOW_TESTING
        ctx = {}
        with patch(TASK_PATH + '.delay') as send:
            steps.register_google_sheet_step(self.agent, ctx)
        send.assert_not_called()
        self.assertEqual(ctx['google_sheet_registration']['reason'], 'shadow_mode')
        self.assertFalse(ctx.get('_google_sheets_registration_attempted'))

    def test_missing_or_unsaved_conversation_fails_before_enqueue(self):
        for conversation in (None, SimpleNamespace(pk=None)):
            with self.subTest(conversation=conversation):
                ctx = self.context('form', {'email': 'ana@example.test'}, conversation=conversation)
                with patch(TASK_PATH + '.delay') as send:
                    with self.assertRaises(ValueError):
                        steps.register_google_sheet_step(self.agent, ctx)
                send.assert_not_called()

    def test_attempt_flag_is_set_before_an_ambiguous_broker_error(self):
        ctx = self.context('web', {'email': 'ana@example.test', 'business_unit': 'ODOO'})

        def fail_after_possible_delivery(payload):
            self.assertTrue(ctx['_google_sheets_registration_attempted'])
            raise RuntimeError('No se confirmó la entrega al broker')

        with patch(TASK_PATH + '.delay', side_effect=fail_after_possible_delivery):
            with self.assertRaises(RuntimeError):
                steps.register_google_sheet_step(self.agent, ctx)


class GoogleSheetRollbackTests(TestCase):
    def setUp(self):
        self.agent = SimpleNamespace(mode=AgentModel.ModeEnum.PRODUCTION, log_event=Mock())
        self.conversation = ConversationModel.objects.create(
            source='form', session_id='ana@example.test',
        )
        self.message = ChatHistoryModel.objects.create(
            conversation=self.conversation, message={'role': 'user', 'content': 'Mensaje inicial'},
        )

    def context(self, attempted=True):
        return {
            'conversation': self.conversation,
            '_conversacion_nueva': True,
            '_marca_historial': 0,
            '_google_sheets_registration_attempted': attempted,
        }

    def test_rollback_keeps_exported_conversation_id_and_retry_reuses_it(self):
        ctx = self.context()
        steps.revertir_turno(ctx)
        self.assertTrue(ConversationModel.objects.filter(pk=self.conversation.pk).exists())
        self.assertFalse(ChatHistoryModel.objects.filter(conversation=self.conversation).exists())
        retry_ctx = {
            'source': 'form',
            'form': {'name': 'Ana', 'email': 'ana@example.test', 'phone': '', 'message': 'Mensaje inicial'},
            'person': {'data': {}}, 'source_identifier': 'ana@example.test',
        }
        steps.verify_existing_conversation_step(self.agent, retry_ctx)
        self.assertEqual(retry_ctx['conversation'].pk, self.conversation.pk)
        self.assertFalse(retry_ctx['_conversacion_nueva'])
        self.assertEqual(ChatHistoryModel.objects.filter(conversation=self.conversation).count(), 1)

    def test_new_conversation_without_export_attempt_keeps_original_rollback_policy(self):
        self.assertEqual(steps.revertir_turno(self.context(attempted=False)), -1)
        self.assertFalse(ConversationModel.objects.filter(pk=self.conversation.pk).exists())

    def test_existing_messages_are_preserved_when_only_current_turn_is_reverted(self):
        later = ChatHistoryModel.objects.create(
            conversation=self.conversation, message={'role': 'user', 'content': 'Turno que falló'},
        )
        ctx = {**self.context(), '_marca_historial': self.message.pk}
        steps.revertir_turno(ctx)
        self.assertTrue(ChatHistoryModel.objects.filter(pk=self.message.pk).exists())
        self.assertFalse(ChatHistoryModel.objects.filter(pk=later.pk).exists())

    def test_generated_response_and_history_are_not_reverted(self):
        ctx = {**self.context(), 'response_to_prospect': {'reply_to_customer': 'Respuesta lista'}}
        self.assertEqual(steps.revertir_turno(ctx), 0)
        self.assertTrue(ConversationModel.objects.filter(pk=self.conversation.pk).exists())
        self.assertTrue(ChatHistoryModel.objects.filter(pk=self.message.pk).exists())


class GoogleSheetTaskRegistrationTests(SimpleTestCase):
    def test_extracted_task_preserves_old_alias_and_celery_message_name(self):
        task = sheets_tasks.register_google_sheet_lead_task
        original_name = 'leadmanager.apps.automations.tasks.register_google_sheet_lead_task'
        self.assertIs(tasks.register_google_sheet_lead_task, task)
        self.assertIs(steps.register_google_sheet_lead_task, task)
        self.assertEqual(task.name, original_name)
        registered = current_app.tasks[original_name]
        self.assertIs(registered, task._get_current_object())
        self.assertEqual(registered.run.__module__, sheets_tasks.__name__)


class GoogleSheetRegistrationTaskTests(TransactionTestCase):
    def setUp(self):
        self.conversation = ConversationModel.objects.create(
            source='form', session_id='tarea-sheets@example.test',
        )
        self.payload = {
            'business_unit': 'NO_DETERMINADO', 'conversation_id': self.conversation.pk,
            'email': 'ana@example.test', 'name': 'Ana', 'phone': '+525500001234',
            'enterprise': 'Empresa de prueba', 'received_at': ARRIVAL.isoformat(),
        }

    def test_task_locks_conversation_and_only_calls_sheets_with_original_arrival(self):
        self.assertFalse(connection.in_atomic_block)

        def registered(**data):
            self.assertTrue(connection.in_atomic_block)
            self.assertEqual(data['received_at'], ARRIVAL)
            return {'sheet_name': 'NO_DETERMINADO', 'row_number': 7}, True

        with patch.object(sheets_tasks, 'register_google_sheet_lead', side_effect=registered) as register, \
                patch.object(tasks, 'run_production_agent') as run_agent, \
                patch.object(tasks, 'create_or_update_lead') as write_odoo, \
                patch.object(ConversationModel.objects, 'select_for_update', wraps=ConversationModel.objects.select_for_update) as lock:
            result = tasks.register_google_sheet_lead_task.run(deepcopy(self.payload))
        register.assert_called_once()
        lock.assert_called_once()
        run_agent.assert_not_called()
        write_odoo.assert_not_called()
        self.assertEqual(result, {
            'conversation_id': self.conversation.pk, 'sheet_name': 'NO_DETERMINADO',
            'row_number': 7, 'created': True,
        })
        self.assertEqual(self.payload['received_at'], ARRIVAL.isoformat())
        self.assertFalse(ProspectModel.objects.exists())

    def test_task_does_not_export_a_conversation_that_was_deleted(self):
        missing_id = self.conversation.pk
        self.conversation.delete()
        with patch.object(sheets_tasks, 'register_google_sheet_lead') as register:
            result = tasks.register_google_sheet_lead_task.run(deepcopy(self.payload))
        register.assert_not_called()
        self.assertEqual(result, {'conversation_id': missing_id, 'skipped': True})

    def test_sheet_errors_retry_only_this_task_with_three_retries(self):
        error = GoogleSheetsError('Fallo temporal de Google Sheets')
        task = tasks.register_google_sheet_lead_task
        with patch.object(sheets_tasks, 'register_google_sheet_lead', side_effect=error) as register, \
                patch.object(task, 'retry', side_effect=Retry('Reintento de Sheets')) as retry, \
                patch.object(tasks, 'run_production_agent') as run_agent, \
                patch.object(tasks, 'create_or_update_lead') as write_odoo:
            with self.assertRaises(Retry):
                task.run(deepcopy(self.payload))
        register.assert_called_once()
        retry.assert_called_once()
        self.assertIs(retry.call_args.kwargs['exc'], error)
        self.assertEqual(retry.call_args.kwargs['max_retries'], 3)
        run_agent.assert_not_called()
        write_odoo.assert_not_called()
        self.assertTrue(task.acks_late)
        self.assertTrue(task.reject_on_worker_lost)

    def test_repeated_task_delegates_idempotence_to_same_conversation_id(self):
        record = {'sheet_name': 'NO_DETERMINADO', 'row_number': 7}
        with patch.object(sheets_tasks, 'register_google_sheet_lead', side_effect=[
            (record, True), (record, False),
        ]) as register:
            first = tasks.register_google_sheet_lead_task.run(deepcopy(self.payload))
            repeated = tasks.register_google_sheet_lead_task.run(deepcopy(self.payload))
        self.assertTrue(first['created'])
        self.assertFalse(repeated['created'])
        self.assertEqual(first['row_number'], repeated['row_number'])
        self.assertEqual(
            [call.kwargs['conversation_id'] for call in register.call_args_list],
            [self.conversation.pk, self.conversation.pk],
        )
