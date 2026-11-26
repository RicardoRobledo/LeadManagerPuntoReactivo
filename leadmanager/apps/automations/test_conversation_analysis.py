"""Seguimiento durable del análisis, con inferencia simulada y sin servicios externos."""

import json
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone as datetime_timezone
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase

from leadmanager.apps.prospects.models import ChatHistoryModel, ConversationModel

from .agent_configuration import default_prompt_config
from .agents.conversation_analyzer_agent.conversation_analyzer_resource_handler import (
    ConversationAnalyzerResourceHandler,
)
from .agents.conversation_analyzer_agent.schemas import ConversationAnalysisSchema
from .models import (
    AgentBusinessUnitModel, AgentModel, AgentRunModel, ConversationAnalysisModel,
    LearningAnnotationModel,
)
from .services import conversation_analysis as service


class TrackedConversationAnalysisTests(TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 10, 12, tzinfo=datetime_timezone.utc)
        clock = mock.patch.object(service.timezone, 'now', side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.analyzer = self.create_agent(
            'conversation_analyzer', category=AgentModel.CategoryEnum.ANALYSIS,
        )
        self.target = self.create_agent('tracked-prospector')
        self.conversation = ConversationModel.objects.create(source='web')
        self.add_message('Necesito información sobre un proyecto.')
        AgentRunModel.objects.create(
            conversation=self.conversation, agent_name=self.target.key,
            mode=AgentModel.ModeEnum.PRODUCTION, source='web',
            status=AgentRunModel.StatusEnum.SUCCESS,
        )
        self.ctx = {
            'conversation_id': self.conversation.pk,
            'agent_id': self.target.pk, 'source': 'web',
        }
        inference = mock.patch(
            'leadmanager.apps.automations.agents.agent_base.Agent.response_model',
            return_value=self.output(),
        )
        self.infer = inference.start()
        self.addCleanup(inference.stop)

    def create_agent(self, key, *, category=AgentModel.CategoryEnum.PROSPECTING):
        return AgentModel.objects.create(
            key=key, name=key, role='Asistente', description='Agente de prueba',
            category=category, is_active=True, learning_enabled=True,
            mode=AgentModel.ModeEnum.PRODUCTION,
            prompt={**default_prompt_config(), 'name': key},
        )

    def add_message(self, content):
        return ChatHistoryModel.objects.create(
            conversation=self.conversation,
            message={'role': 'user', 'content': content},
        )

    @staticmethod
    def output(annotations=None):
        return SimpleNamespace(output_parsed=ConversationAnalysisSchema(
            annotations=[] if annotations is None else annotations,
        ))

    def run_analysis(self):
        return service.run_tracked_analysis(dict(self.ctx))

    def state(self):
        return ConversationAnalysisModel.objects.get(
            conversation=self.conversation, agent=self.target,
        )

    def test_unchanged_success_with_zero_annotations_does_not_repeat_inference(self):
        self.run_analysis()
        first = self.state()

        repeated = self.run_analysis()

        self.assertEqual(first.status, ConversationAnalysisModel.StatusEnum.SUCCESS)
        self.assertEqual(first.fingerprint, first.completed_fingerprint)
        self.assertTrue(first.completed_fingerprint)
        self.assertEqual(first.annotations_created, 0)
        self.assertEqual(first.attempts, 1)
        self.assertIsNotNone(first.last_run_id)
        self.assertTrue(repeated.get('analysis_skip_reason'))
        self.infer.assert_called_once()
        self.assertFalse(LearningAnnotationModel.objects.exists())

    def test_history_configuration_and_units_each_require_fresh_analysis(self):
        self.run_analysis()
        fingerprints = [self.state().completed_fingerprint]

        self.add_message('El proyecto debe entregarse este trimestre.')
        self.run_analysis()
        fingerprints.append(self.state().completed_fingerprint)

        self.target.prompt = {
            **self.target.prompt, 'objective_conversation': 'Confirmar el plazo del proyecto.',
        }
        self.target.save(update_fields=['prompt'])
        self.run_analysis()
        fingerprints.append(self.state().completed_fingerprint)

        AgentBusinessUnitModel.objects.create(
            agent=self.target, name='NUEVO SERVICIO', description='Servicio de prueba.',
        )
        self.run_analysis()
        fingerprints.append(self.state().completed_fingerprint)

        self.assertEqual(self.infer.call_count, 4)
        self.assertEqual(len(set(fingerprints)), 4)
        self.assertEqual(self.state().status, ConversationAnalysisModel.StatusEnum.SUCCESS)

    def test_analyzer_prompt_and_output_schema_changes_invalidate_completed_analysis(self):
        self.run_analysis()
        initial = self.state().completed_fingerprint
        prompt = ConversationAnalyzerResourceHandler.get('analyze_conversation_prompt')
        with mock.patch.object(
            ConversationAnalyzerResourceHandler, 'get', return_value=prompt + '\nNueva regla de análisis.',
        ):
            self.run_analysis()
            prompt_fingerprint = self.state().completed_fingerprint
            schema = deepcopy(ConversationAnalysisSchema.model_json_schema())
            schema['title'] = 'Contrato de análisis actualizado'
            with mock.patch.object(ConversationAnalysisSchema, 'model_json_schema', return_value=schema):
                self.run_analysis()
                schema_fingerprint = self.state().completed_fingerprint

        self.assertEqual(self.infer.call_count, 3)
        self.assertEqual(len({initial, prompt_fingerprint, schema_fingerprint}), 3)

    def test_claim_uses_frozen_evidence_and_new_input_is_analyzed_on_next_run(self):
        prompt = ConversationAnalyzerResourceHandler.get('analyze_conversation_prompt')

        def change_input_during_inference(**kwargs):
            state = self.state()
            self.assertEqual(state.status, ConversationAnalysisModel.StatusEnum.PROCESSING)
            self.assertIsNotNone(state.claim_token)
            self.assertGreater(state.lease_until, self.now)
            evidence = json.loads(kwargs['input'][0]['content'])
            self.assertEqual(len(evidence['messages']), 1)
            self.assertEqual(kwargs['system_prompt'], prompt)
            self.add_message('Información recibida durante la inferencia.')
            self.target.prompt = {
                **self.target.prompt, 'objective_conversation': 'Objetivo actualizado durante el análisis.',
            }
            self.target.save(update_fields=['prompt'])
            return self.output()

        self.infer.side_effect = change_input_during_inference
        with mock.patch.object(
            ConversationAnalyzerResourceHandler, 'get',
            side_effect=[prompt, prompt + '\nCambio posterior a la reserva.'],
        ) as get_prompt:
            self.run_analysis()
            get_prompt.assert_called_once_with('analyze_conversation_prompt')
        first = self.state().completed_fingerprint
        self.infer.side_effect = None

        self.run_analysis()

        self.assertEqual(self.infer.call_count, 2)
        self.assertNotEqual(first, self.state().completed_fingerprint)
        evidence = json.loads(self.infer.call_args.kwargs['input'][0]['content'])
        self.assertEqual(len(evidence['messages']), 2)
        self.assertIn(
            'Objetivo actualizado durante el análisis.',
            evidence['prospector_context']['formatted_prompt'],
        )

    def test_failed_analysis_waits_for_cooldown_and_retries_outside_weekly_window(self):
        ChatHistoryModel.objects.filter(conversation=self.conversation).update(
            created_at=self.now - timedelta(days=60),
            updated_at=self.now - timedelta(days=60),
        )
        self.infer.side_effect = [RuntimeError('Fallo de inferencia'), self.output()]
        with self.assertRaisesRegex(Exception, 'Fallo de inferencia'):
            self.run_analysis()
        failed = self.state()
        self.assertEqual(failed.status, ConversationAnalysisModel.StatusEnum.FAILED)
        self.assertEqual(failed.attempts, 1)
        self.assertEqual(failed.next_attempt_at, self.now + timedelta(minutes=15))
        self.assertIn('Fallo de inferencia', failed.last_error)
        self.assertFalse(failed.completed_fingerprint)

        waiting = self.run_analysis()
        self.assertTrue(waiting.get('analysis_skip_reason'))
        self.assertEqual(self.infer.call_count, 1)
        self.now += timedelta(minutes=16)
        service.retry_due_analyses()

        recovered = self.state()
        self.assertEqual(self.infer.call_count, 2)
        self.assertEqual(recovered.status, ConversationAnalysisModel.StatusEnum.SUCCESS)
        self.assertEqual(recovered.attempts, 2)
        self.assertIsNone(recovered.next_attempt_at)
        self.assertEqual(recovered.last_error, '')

    def test_pending_analysis_is_recovered_after_its_weekly_window_has_passed(self):
        historical_date = self.now - timedelta(days=60)
        ConversationModel.objects.filter(pk=self.conversation.pk).update(
            created_at=historical_date, updated_at=historical_date,
        )
        ChatHistoryModel.objects.filter(conversation=self.conversation).update(
            created_at=historical_date, updated_at=historical_date,
        )
        ConversationAnalysisModel.objects.create(
            conversation=self.conversation, agent=self.target,
            status=ConversationAnalysisModel.StatusEnum.PENDING,
        )

        result = service.retry_due_analyses()

        self.infer.assert_called_once()
        self.assertEqual(result['analizadas'], 1)
        self.assertEqual(self.state().status, ConversationAnalysisModel.StatusEnum.SUCCESS)
        self.assertEqual(self.state().attempts, 1)
        self.assertTrue(self.state().completed_fingerprint)

    def test_unchanged_input_stops_retrying_after_three_failed_attempts(self):
        self.infer.side_effect = RuntimeError('Fallo persistente de inferencia')
        for attempt in range(3):
            with self.subTest(attempt=attempt), self.assertRaisesRegex(Exception, 'Fallo persistente'):
                self.run_analysis()
            self.now += timedelta(minutes=16)

        service.retry_due_analyses()
        omitted = self.run_analysis()

        self.assertEqual(self.infer.call_count, 3)
        self.assertTrue(omitted.get('analysis_skip_reason'))
        self.assertEqual(self.state().attempts, 3)
        self.assertEqual(self.state().status, ConversationAnalysisModel.StatusEnum.FAILED)
        self.assertIsNone(self.state().next_attempt_at)

    def test_disabled_learning_or_analyzer_and_mixed_authors_never_call_inference(self):
        self.target.learning_enabled = False
        self.target.save(update_fields=['learning_enabled'])
        self.assertTrue(self.run_analysis().get('analysis_skip_reason'))
        self.target.learning_enabled = True
        self.target.save(update_fields=['learning_enabled'])
        self.analyzer.is_active = False
        self.analyzer.save(update_fields=['is_active'])
        self.assertTrue(self.run_analysis().get('analysis_skip_reason'))
        self.analyzer.is_active = True
        self.analyzer.save(update_fields=['is_active'])
        other = self.create_agent('second-author')
        AgentRunModel.objects.create(
            conversation=self.conversation, agent_name=other.key,
            mode=AgentModel.ModeEnum.PRODUCTION, source='web',
            status=AgentRunModel.StatusEnum.SUCCESS,
        )

        self.assertIsNone(service.get_analysis_target(self.conversation))
        self.assertTrue(self.run_analysis().get('analysis_skip_reason'))
        self.infer.assert_not_called()

    def test_live_lease_omits_duplicate_and_expired_lease_can_be_recovered(self):
        self.run_analysis()
        state = self.state()
        previous_token = uuid.uuid4()
        ConversationAnalysisModel.objects.filter(pk=state.pk).update(
            status=ConversationAnalysisModel.StatusEnum.PROCESSING,
            completed_fingerprint='', claim_token=previous_token,
            lease_until=self.now + timedelta(minutes=30),
        )
        self.infer.reset_mock()

        self.assertTrue(self.run_analysis().get('analysis_skip_reason'))
        self.infer.assert_not_called()
        ConversationAnalysisModel.objects.filter(pk=state.pk).update(
            lease_until=self.now - timedelta(seconds=1),
        )

        service.retry_due_analyses()

        self.infer.assert_called_once()
        recovered = self.state()
        self.assertEqual(recovered.status, ConversationAnalysisModel.StatusEnum.SUCCESS)
        self.assertEqual(recovered.attempts, 2)
        self.assertIsNone(recovered.claim_token)
        self.assertIsNone(recovered.lease_until)

    def test_preparation_failure_is_persisted_for_later_recovery(self):
        with mock.patch.object(
            service, 'build_analysis_evidence', side_effect=ValueError('Falló la preparación del contexto'),
        ):
            with self.assertRaisesRegex(Exception, 'preparación'):
                self.run_analysis()

        state = self.state()
        self.assertEqual(state.status, ConversationAnalysisModel.StatusEnum.FAILED)
        self.assertEqual(state.attempts, 1)
        self.assertIn('preparación', state.last_error)
        self.assertIsNotNone(state.next_attempt_at)
        self.infer.assert_not_called()
