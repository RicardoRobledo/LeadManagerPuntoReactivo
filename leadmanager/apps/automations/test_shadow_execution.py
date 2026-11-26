"""Pruebas del aislamiento y despacho de agentes en sombra, sin APIs externas."""

from copy import deepcopy
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory

from leadmanager.apps.api.agents.views import WebChatView
from leadmanager.apps.prospects.models import (
    ChatHistoryModel, ConversationModel, ConversationStateEnum, ProspectModel,
)

from .agent_configuration import default_prompt_config
from .agents.agent_base import Agent
from .agents.prospector_agent.shadow_steps import SHADOW_STEPS
from .models import (
    AgentChannelAssignment, AgentEventModel, AgentModel, AgentRunModel, PendingRunModel,
    TestConversationModel,
)
from .services.agent_selection import get_agents_for_channel
from .services.agent_execution import capture_shadow_input_step, run_production_agent
from .tasks import process_shadow_run


class ShadowAgentFixtures(TestCase):
    def create_agent(self, key, *, mode=AgentModel.ModeEnum.PRODUCTION,
                     active=False, category=AgentModel.CategoryEnum.PROSPECTING,
                     channel='web', channel_active=False):
        agent = AgentModel.objects.create(
            key=key, name=key, role='Asistente', description='Agente de prueba',
            category=category, mode=mode, is_active=active,
            prompt={**default_prompt_config(), 'name': key},
        )
        AgentChannelAssignment.objects.create(
            agent=agent, channel=channel, active=channel_active,
        )
        return agent


class AgentSelectionTests(ShadowAgentFixtures):
    def test_selects_active_production_and_inactive_shadows_for_same_channel(self):
        production = self.create_agent('live-agent', active=True, channel_active=True)
        shadow = self.create_agent('shadow-agent', mode=AgentModel.ModeEnum.SHADOW_TESTING)
        self.create_agent('other-channel', mode=AgentModel.ModeEnum.SHADOW_TESTING,
                          channel='email')
        self.create_agent('inactive-production')
        self.create_agent('analysis-agent', mode=AgentModel.ModeEnum.SHADOW_TESTING,
                          category=AgentModel.CategoryEnum.ANALYSIS)

        selected, shadows = get_agents_for_channel('web')

        self.assertEqual(selected.pk, production.pk)
        self.assertEqual([item.pk for item in shadows], [shadow.pk])

    def test_active_assignment_must_be_for_requested_channel(self):
        inconsistent = self.create_agent('wrong-channel-active', active=True)
        AgentChannelAssignment.objects.create(
            agent=inconsistent, channel='email', active=True,
        )
        production = self.create_agent('web-production', active=True, channel_active=True)

        selected, _ = get_agents_for_channel('web')

        self.assertEqual(selected.pk, production.pk)

    def test_shadow_never_substitutes_missing_production(self):
        self.create_agent('only-shadow', mode=AgentModel.ModeEnum.SHADOW_TESTING)

        with self.assertRaises(ValueError):
            get_agents_for_channel('web')

    def test_production_uses_selected_agent_key_and_configuration(self):
        production = self.create_agent('custom-production', active=True, channel_active=True)
        shadow = self.create_agent('shadow-agent', mode=AgentModel.ModeEnum.SHADOW_TESTING)
        seen = {}

        def inspect_configuration(agent, ctx):
            seen['name'] = agent.name
            seen['configuration'] = ctx['_agent_configuration']['name']
            seen['shadows'] = [item.pk for item in ctx['_shadow_agents']]

        with mock.patch(
            'leadmanager.apps.automations.agents.prospector_agent.prospector.PRODUCTION_STEPS',
            [inspect_configuration],
        ):
            ctx = run_production_agent({'source': 'web', 'history': []})

        self.assertEqual(seen['name'], production.key)
        self.assertEqual(seen['configuration'], production.name)
        self.assertEqual(seen['shadows'], [shadow.pk])
        self.assertEqual(ctx['_agent_run'].agent_name, production.key)
        self.assertEqual(ctx['_agent_run'].mode, AgentModel.ModeEnum.PRODUCTION)


@override_settings(CELERY_SHADOW_QUEUE='shadow-tests')
class ShadowDispatchTests(ShadowAgentFixtures):
    def setUp(self):
        self.production = self.create_agent('live-agent', active=True, channel_active=True)
        self.shadow = self.create_agent('shadow-agent', mode=AgentModel.ModeEnum.SHADOW_TESTING)
        self.conversation = ConversationModel.objects.create(session_id='dispatch', source='web')
        self.run = AgentRunModel.objects.create(
            agent_name=self.production.key, source='web', conversation=self.conversation,
        )
        self.ctx = {
            'source': 'web', 'web': {'message': 'Mensaje original', 'session_id': 'dispatch'},
            'person': {'data': {'name': 'Original'}},
            'history': [{'role': 'user', 'content': 'Mensaje original'}],
            'conversation': self.conversation, '_agent_run': self.run,
            '_shadow_agents': [self.shadow],
        }
        self.agent = Agent(name=self.production.key)

    def test_dispatches_separate_tasks_without_waiting_and_freezes_input(self):
        second = self.create_agent('second-shadow', mode=AgentModel.ModeEnum.SHADOW_TESTING)
        self.ctx['_shadow_agents'].append(second)
        source = ChatHistoryModel.objects.create(
            conversation=self.conversation, message={'role': 'user', 'content': 'Mensaje original'},
        )
        patchpoint = 'leadmanager.apps.automations.tasks.process_shadow_run.apply_async'

        with mock.patch(patchpoint) as dispatch:
            with self.captureOnCommitCallbacks(execute=True):
                capture_shadow_input_step(self.agent, self.ctx)

        runs = list(AgentRunModel.objects.filter(production_run=self.run).order_by('pk'))
        self.assertEqual(len(runs), 2)
        self.assertEqual({item.agent_name for item in runs}, {self.shadow.key, second.key})
        self.assertEqual(dispatch.call_count, 2)
        for run in runs:
            self.assertEqual(run.status, AgentRunModel.StatusEnum.QUEUED)
            dispatch.assert_any_call(args=[run.pk], queue='shadow-tests', retry=False)
            self.assertEqual(run.input_snapshot['history'], [{'role': 'user', 'content': 'Mensaje original'}])
            self.assertEqual(run.input_snapshot['source_message_id'], source.pk)
            self.assertEqual(run.conversation_id, self.conversation.pk)
        dispatch.return_value.get.assert_not_called()
        dispatch.return_value.wait.assert_not_called()

        self.ctx['history'].append({'role': 'assistant', 'content': 'Respuesta de producción'})
        self.ctx['web']['message'] = 'Mensaje modificado'
        self.ctx['person']['data']['name'] = 'Modificado'
        for run in runs:
            run.refresh_from_db()
            self.assertEqual(len(run.input_snapshot['history']), 1)
            self.assertEqual(run.input_snapshot['web']['message'], 'Mensaje original')
            self.assertEqual(run.input_snapshot['person']['data']['name'], 'Original')

    def test_capture_is_idempotent_for_same_production_run_and_agent(self):
        with mock.patch('leadmanager.apps.automations.tasks.process_shadow_run.apply_async') as dispatch:
            with self.captureOnCommitCallbacks(execute=True):
                capture_shadow_input_step(self.agent, self.ctx)
                capture_shadow_input_step(self.agent, self.ctx)

        self.assertEqual(AgentRunModel.objects.filter(production_run=self.run).count(), 1)
        dispatch.assert_called_once()

    def test_broker_failure_does_not_fail_production(self):
        with mock.patch('leadmanager.apps.automations.tasks.process_shadow_run.apply_async',
                        side_effect=OSError('Broker temporalmente no disponible')):
            with self.captureOnCommitCallbacks(execute=True):
                capture_shadow_input_step(self.agent, self.ctx)

        self.run.refresh_from_db()
        self.assertEqual(self.run.status, AgentRunModel.StatusEnum.RUNNING)
        shadow_run = AgentRunModel.objects.get(production_run=self.run)
        self.assertEqual(shadow_run.status, AgentRunModel.StatusEnum.FAILED)


class ShadowWorkerTests(ShadowAgentFixtures):
    def setUp(self):
        self.shadow = self.create_agent('shadow-agent', mode=AgentModel.ModeEnum.SHADOW_TESTING)
        self.conversation = ConversationModel.objects.create(session_id='worker', source='web')
        self.production_run = AgentRunModel.objects.create(
            agent_name='live-agent', source='web', conversation=self.conversation,
            status=AgentRunModel.StatusEnum.SUCCESS,
        )
        self.run = AgentRunModel.objects.create(
            agent_name=self.shadow.key, source='web', conversation=self.conversation,
            production_run=self.production_run,
            mode=AgentModel.ModeEnum.SHADOW_TESTING,
            status=AgentRunModel.StatusEnum.QUEUED,
            input_snapshot={
                'source': 'web', 'web': {'message': 'Original', 'session_id': 'worker'},
                'history': [{'role': 'user', 'content': 'Original'}],
                'person': {'data': {}}, 'conversation_id': self.conversation.pk,
                '_agent_configuration': deepcopy(self.shadow.prompt),
                '_agent_units': list(self.shadow.business_units.values('id', 'name', 'description')),
            },
        )

    def test_duplicate_delivery_does_not_execute_shadow_twice(self):
        executed = []

        def calculate(agent, ctx):
            executed.append(agent.name)
            if len(executed) == 1:
                # Simula otra entrega mientras la primera todavía está corriendo.
                process_shadow_run.run(self.run.pk)
            self.assertEqual(ctx['history'], self.run.input_snapshot['history'])
            ctx['_shadow_result'] = {'reply_to_customer': 'Respuesta interna'}

        with mock.patch('leadmanager.apps.automations.tasks.SHADOW_STEPS', (calculate,)):
            process_shadow_run.run(self.run.pk)
            process_shadow_run.run(self.run.pk)

        self.run.refresh_from_db()
        self.production_run.refresh_from_db()
        self.assertEqual(executed, [self.shadow.key])
        self.assertEqual(self.run.status, AgentRunModel.StatusEnum.SUCCESS)
        self.assertEqual(self.run.result, {'reply_to_customer': 'Respuesta interna'})
        self.assertEqual(self.run.steps.count(), 1)
        self.assertEqual(self.production_run.status, AgentRunModel.StatusEnum.SUCCESS)

    @override_settings(OPENAI_MAX_RETRIES=1)
    def test_shadow_turn_is_saved_only_in_test_conversation(self):
        source = ChatHistoryModel.objects.create(
            conversation=self.conversation, message={'role': 'user', 'content': 'Original'},
        )
        self.run.input_snapshot = {**self.run.input_snapshot, 'source_message_id': source.pk}
        self.run.save(update_fields=['input_snapshot'])
        real_history = list(ChatHistoryModel.objects.values())
        answer = {'reply_to_customer': 'Respuesta de pruebas', 'crm_update': {'semaforo': 'amarillo'}}
        search = SimpleNamespace(
            document_ids=[], enterprise=None,
            model_dump=mock.Mock(return_value={'document_ids': [], 'enterprise': None}),
        )
        steps_path = 'leadmanager.apps.automations.agents.prospector_agent.prospector_steps.'

        with mock.patch.object(Agent, 'response_model', side_effect=[
            SimpleNamespace(output_parsed=search),
            SimpleNamespace(output_parsed=SimpleNamespace(score=1, reason='Sin datos extra')),
            SimpleNamespace(output_parsed=SimpleNamespace(model_dump=mock.Mock(return_value=answer))),
        ]), mock.patch(steps_path + 'send_email') as send, \
                mock.patch(steps_path + 'create_or_update_lead') as lead:
            process_shadow_run.run(self.run.pk)

        send.assert_not_called()
        lead.assert_not_called()
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, AgentRunModel.StatusEnum.SUCCESS)
        self.assertEqual(self.run.result, answer)
        self.assertEqual(list(ChatHistoryModel.objects.values()), real_history)
        self.assertEqual(ConversationModel.objects.count(), 1)

        test_conversation = TestConversationModel.objects.get()
        self.assertEqual(test_conversation.agent, self.shadow)
        self.assertEqual(test_conversation.conversation, self.conversation)
        messages = list(test_conversation.messages.values_list('message', 'source_message_id', 'run_id'))
        self.assertEqual(messages, [
            ({'role': 'user', 'content': 'Original'}, source.pk, self.run.pk),
            ({'role': 'assistant', 'content': answer}, source.pk, self.run.pk),
        ])

    def test_disabled_shadow_is_not_executed(self):
        self.shadow.mode = AgentModel.ModeEnum.PRODUCTION
        self.shadow.save(update_fields=['mode'])
        calculate = mock.Mock(__name__='calculate')

        with mock.patch('leadmanager.apps.automations.tasks.SHADOW_STEPS', (calculate,)):
            process_shadow_run.run(self.run.pk)

        calculate.assert_not_called()
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, AgentRunModel.StatusEnum.FAILED)

    def test_shadow_step_failure_leaves_production_successful(self):
        def failing(agent, ctx):
            raise RuntimeError('Fallo aislado de pruebas')

        with mock.patch('leadmanager.apps.automations.tasks.SHADOW_STEPS', (failing,)):
            process_shadow_run.run(self.run.pk)

        self.run.refresh_from_db()
        self.production_run.refresh_from_db()
        self.assertEqual(self.run.status, AgentRunModel.StatusEnum.FAILED)
        self.assertEqual(self.run.steps.get().error_message, 'Fallo aislado de pruebas')
        self.assertEqual(self.production_run.status, AgentRunModel.StatusEnum.SUCCESS)


class WebChatShadowIntegrationTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.conversation = ConversationModel.objects.create(
            session_id='parallel-web-session', source='web',
        )
        self.payload = {
            'email': 'prospect@example.com', 'business_unit': '',
            'company_name': '', 'message': 'Otro mensaje del prospecto',
            'session_id': self.conversation.session_id,
            'preferred_contact_method': '',
        }

    def post_message(self):
        request = self.factory.post('/web-chat/', self.payload, format='json')
        return WebChatView.as_view(throttle_classes=[])(request)

    def test_running_shadow_does_not_block_next_web_message(self):
        AgentRunModel.objects.create(
            agent_name='shadow-agent', mode=AgentModel.ModeEnum.SHADOW_TESTING,
            conversation=self.conversation, source='web',
            status=AgentRunModel.StatusEnum.RUNNING,
        )

        with mock.patch('leadmanager.apps.api.agents.views.process_pending_run.delay') as dispatch:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.post_message()

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data['status'], 'accepted')
        pending = PendingRunModel.objects.get(identifier=self.conversation.session_id)
        self.assertEqual(pending.payload['_conversation_id'], self.conversation.pk)
        self.assertEqual(pending.payload['message'], self.payload['message'])
        dispatch.assert_called_once_with(pending.pk)
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.web_message_count, 1)

    def test_running_production_still_prevents_duplicate_web_turn(self):
        AgentRunModel.objects.create(
            agent_name='live-agent', mode=AgentModel.ModeEnum.PRODUCTION,
            conversation=self.conversation, source='web',
            status=AgentRunModel.StatusEnum.RUNNING,
        )

        with mock.patch('leadmanager.apps.api.agents.views.process_pending_run.delay') as dispatch:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.post_message()

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data['status'], 'processing')
        self.assertFalse(PendingRunModel.objects.exists())
        dispatch.assert_not_called()
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.web_message_count, 0)


@override_settings(OPENAI_MAX_RETRIES=1)
class ShadowStepIsolationTests(ShadowAgentFixtures):
    def test_shadow_without_steps_is_failed_instead_of_successful(self):
        ctx = {'source': 'web'}
        agent = Agent(
            name='empty-shadow', mode=AgentModel.ModeEnum.SHADOW_TESTING,
            shadow_steps=[],
        )

        with self.assertRaisesMessage(ValueError, 'no tiene pasos configurados'):
            agent.run(ctx)

        run = AgentRunModel.objects.get(pk=ctx['_agent_run'].pk)
        self.assertEqual(run.status, AgentRunModel.StatusEnum.FAILED)
        self.assertIsNotNone(run.finished_at)
        self.assertFalse(run.steps.exists())
        self.assertEqual(run.result, {})

    def test_real_shadow_steps_only_write_execution_logs(self):
        shadow = self.create_agent('shadow-agent', mode=AgentModel.ModeEnum.SHADOW_TESTING)
        prospect = ProspectModel.objects.create(
            email='prospect@example.com', name='Prospecto original', person_id=123,
        )
        conversation = ConversationModel.objects.create(
            session_id='shadow-isolation', source='web',
            state=ConversationStateEnum.OPEN, prospect=prospect,
        )
        ChatHistoryModel.objects.create(
            conversation=conversation,
            message={'role': 'user', 'content': 'Quiero información'},
        )
        original_conversation = ConversationModel.objects.values().get(pk=conversation.pk)
        original_prospect = ProspectModel.objects.values().get(pk=prospect.pk)
        original_history = list(conversation.messages.values())
        answer = {
            'reply_to_customer': 'Respuesta interna de pruebas',
            'reply_to_customer_esp': 'Respuesta interna de pruebas',
            'crm_update': {'semaforo': 'amarillo', 'escalar_a_humano': False},
        }
        search = SimpleNamespace(
            document_ids=[], enterprise=None,
            model_dump=mock.Mock(return_value={'document_ids': [], 'enterprise': None}),
        )
        validation = SimpleNamespace(score=1, reason='Sin información adicional')
        response = SimpleNamespace(model_dump=mock.Mock(return_value=answer))
        ctx = {
            'source': 'web', 'web': {'email': prospect.email, 'message': 'Quiero información'},
            'person': {'data': {}, 'db_exists': False},
            'history': [{'role': 'user', 'content': 'Quiero información'}],
            'conversation': conversation,
            '_agent_configuration': deepcopy(shadow.prompt),
            '_agent_units': list(shadow.business_units.values('id', 'name', 'description')),
            '_production_run_id': 91,
        }
        agent = Agent(name=shadow.key, mode=shadow.mode, shadow_steps=SHADOW_STEPS)
        steps_path = 'leadmanager.apps.automations.agents.prospector_agent.prospector_steps.'

        with mock.patch.object(Agent, 'response_model', side_effect=[
            SimpleNamespace(output_parsed=search),
            SimpleNamespace(output_parsed=validation),
            SimpleNamespace(output_parsed=response),
        ]) as model, mock.patch(steps_path + 'send_email') as send, \
                mock.patch(steps_path + 'find_or_create_company') as company, \
                mock.patch(steps_path + 'create_or_update_lead') as lead, \
                mock.patch(steps_path + 'find_person_by_source') as find_person:
            agent.run(ctx)

        self.assertEqual(model.call_count, 3)
        send.assert_not_called()
        company.assert_not_called()
        lead.assert_not_called()
        find_person.assert_not_called()
        self.assertEqual(ctx['_shadow_result'], answer)
        ctx['_agent_run'].refresh_from_db()
        self.assertEqual(ctx['_agent_run'].status, AgentRunModel.StatusEnum.SUCCESS)
        self.assertEqual(ctx['_agent_run'].mode, AgentModel.ModeEnum.SHADOW_TESTING)
        self.assertEqual(ctx['_agent_run'].result, answer)
        self.assertEqual(ConversationModel.objects.values().get(pk=conversation.pk), original_conversation)
        self.assertEqual(ProspectModel.objects.values().get(pk=prospect.pk), original_prospect)
        self.assertEqual(list(conversation.messages.values()), original_history)
        self.assertTrue(AgentEventModel.objects.filter(
            run=ctx['_agent_run'], data__mode=AgentModel.ModeEnum.SHADOW_TESTING,
        ).exists())
