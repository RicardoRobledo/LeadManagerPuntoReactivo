"""Lotes semanales y consolidación final, con inferencia simulada."""

import json
import uuid
from datetime import date, datetime, timedelta, timezone as datetime_timezone
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase

from leadmanager.apps.prospects.models import ChatHistoryModel, ConversationModel

from .agent_configuration import default_prompt_config
from .agents.conversation_analyzer_agent.schemas import (
    ConversationAnalysisSchema, FinalAnalysisSchema,
)
from .models import (
    AgentModel, AgentRunModel, ConversationAnalysisModel, LearningAnnotationModel,
    WeeklyAnalysisBatchModel,
)
from .services import weekly_analysis as service
from . import tasks


class WeeklyAnalysisBatchTests(TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 10, 12, tzinfo=datetime_timezone.utc)
        self.start = datetime(2026, 11, 2, 6, tzinfo=datetime_timezone.utc)
        self.end = self.start + timedelta(days=7)
        clock = mock.patch.object(service.timezone, 'now', side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.create_agent('conversation_analyzer', category=AgentModel.CategoryEnum.ANALYSIS)
        self.target = self.create_agent('weekly-prospector')
        self.responses = []
        self.phases = []
        self.individual_inputs = []
        self.empty_individual_results = False
        self.fail_individual = False
        self.fail_final = False
        inference = mock.patch(
            'leadmanager.apps.automations.agents.agent_base.Agent.response_model',
            side_effect=self.respond,
        )
        self.infer = inference.start()
        self.addCleanup(inference.stop)

    def create_agent(self, key, *, category=AgentModel.CategoryEnum.PROSPECTING,
                     active=True, learning=True, mode=AgentModel.ModeEnum.PRODUCTION):
        return AgentModel.objects.create(
            key=key, name=key, role='Asistente', description='Agente de prueba',
            category=category, is_active=active, learning_enabled=learning, mode=mode,
            prompt={**default_prompt_config(), 'name': key},
        )

    def create_conversation(self, number, *, target=None, at=None, with_run=True):
        conversation = ConversationModel.objects.create(source='web')
        message = ChatHistoryModel.objects.create(
            conversation=conversation,
            message={'role': 'user', 'content': f'Conversación {number}'},
        )
        timestamp = self.start + timedelta(days=1) if at is None else at
        ChatHistoryModel.objects.filter(pk=message.pk).update(
            created_at=timestamp, updated_at=timestamp,
        )
        if with_run:
            self.add_run(conversation, self.target if target is None else target)
        return conversation

    def add_run(self, conversation, target):
        AgentRunModel.objects.create(
            conversation=conversation, agent_name=target.key,
            mode=AgentModel.ModeEnum.PRODUCTION, source='web',
            status=AgentRunModel.StatusEnum.SUCCESS,
        )

    def prepare(self, *, start=None, end=None):
        batches = service.prepare_weekly_batches(start or self.start, end or self.end)
        return next(batch for batch in batches if batch.agent_id == self.target.pk)

    @staticmethod
    def individual_output(*, empty=False):
        annotations = [] if empty else [{
            'observaciones': 'No quedó claro el plazo de implementación.',
            'sugerencias': 'Preguntar por el plazo cuando falte ese dato.',
        }]
        return SimpleNamespace(output_parsed=ConversationAnalysisSchema(annotations=annotations))

    @staticmethod
    def final_output():
        return SimpleNamespace(output_parsed=FinalAnalysisSchema(
            observaciones='Se repite la falta de claridad sobre el plazo de implementación.',
            sugerencias='Confirmar el plazo únicamente cuando todavía no esté indicado.',
        ))

    def respond(self, **kwargs):
        is_final = kwargs['text_format'] is FinalAnalysisSchema
        self.phases.append('final' if is_final else 'individual')
        if not is_final:
            self.assertIs(kwargs['text_format'], ConversationAnalysisSchema)
            payload = json.loads(kwargs['input'][0]['content'])
            self.individual_inputs.append(payload['messages'][0]['content'])
        if self.responses:
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response
        if is_final and self.fail_final:
            raise RuntimeError('Fallo de consolidación final')
        if not is_final and self.fail_individual:
            raise RuntimeError('Fallo de análisis individual')
        return self.final_output() if is_final else self.individual_output(empty=self.empty_individual_results)

    def item(self, batch, conversation):
        return ConversationAnalysisModel.objects.get(batch=batch, conversation=conversation)

    def test_failure_pauses_batch_and_retry_resumes_at_failed_conversation(self):
        conversations = [self.create_conversation(number) for number in range(1, 4)]
        batch = self.prepare()
        self.responses = [self.individual_output(), RuntimeError('Falló la segunda conversación')]

        service.run_weekly_batch(batch.pk)

        batch.refresh_from_db()
        self.assertEqual(batch.status, WeeklyAnalysisBatchModel.StatusEnum.WAITING_RETRY)
        self.assertEqual(self.individual_inputs, ['Conversación 1', 'Conversación 2'])
        self.assertEqual(self.item(batch, conversations[0]).status, ConversationAnalysisModel.StatusEnum.SUCCESS)
        self.assertEqual(self.item(batch, conversations[1]).status, ConversationAnalysisModel.StatusEnum.FAILED)
        self.assertEqual(self.item(batch, conversations[2]).status, ConversationAnalysisModel.StatusEnum.PENDING)
        self.assertEqual(self.item(batch, conversations[2]).attempts, 0)
        self.assertEqual(batch.next_attempt_at, self.now + timedelta(minutes=15))
        self.assertFalse(LearningAnnotationModel.objects.exists())

        self.now += timedelta(minutes=16)
        service.retry_due_batches()

        batch.refresh_from_db()
        self.assertEqual(self.individual_inputs, [
            'Conversación 1', 'Conversación 2', 'Conversación 2', 'Conversación 3',
        ])
        self.assertEqual(self.phases, ['individual'] * 4 + ['final'])
        self.assertEqual(batch.status, WeeklyAnalysisBatchModel.StatusEnum.SUCCESS)
        self.assertEqual(self.item(batch, conversations[0]).attempts, 1)
        self.assertEqual(self.item(batch, conversations[1]).attempts, 2)
        self.assertEqual(self.item(batch, conversations[2]).attempts, 1)
        self.assertTrue(self.item(batch, conversations[0]).result)
        annotation = LearningAnnotationModel.objects.get()
        self.assertEqual(batch.annotation_id, annotation.pk)
        self.assertEqual(annotation.agent_id, self.target.pk)
        self.assertFalse(annotation.is_active)

    def test_third_individual_failure_blocks_remaining_items_and_final_analysis(self):
        first = self.create_conversation(1)
        following = self.create_conversation(2)
        batch = self.prepare()
        self.fail_individual = True

        service.run_weekly_batch(batch.pk)
        for _ in range(2):
            self.now += timedelta(minutes=16)
            service.retry_due_batches()
        self.now += timedelta(minutes=16)
        service.retry_due_batches()
        service.run_weekly_batch(batch.pk)

        batch.refresh_from_db()
        self.assertEqual(self.individual_inputs, ['Conversación 1'] * 3)
        self.assertEqual(self.item(batch, first).attempts, 3)
        self.assertEqual(self.item(batch, following).attempts, 0)
        self.assertEqual(batch.status, WeeklyAnalysisBatchModel.StatusEnum.FAILED)
        self.assertIsNone(batch.next_attempt_at)
        self.assertNotIn('final', self.phases)
        self.assertFalse(LearningAnnotationModel.objects.exists())

    def test_final_retry_reuses_completed_intermediates_and_publishes_once(self):
        conversations = [self.create_conversation(number) for number in (1, 2)]
        batch = self.prepare()
        self.responses = [
            self.individual_output(), self.individual_output(),
            RuntimeError('Falló la consolidación'),
        ]

        service.run_weekly_batch(batch.pk)

        batch.refresh_from_db()
        self.assertEqual(batch.phase, 'final')
        self.assertEqual(batch.final_attempts, 1)
        self.assertEqual(batch.status, WeeklyAnalysisBatchModel.StatusEnum.WAITING_RETRY)
        self.assertTrue(batch.final_input)
        self.assertFalse(LearningAnnotationModel.objects.exists())
        self.assertTrue(all(self.item(batch, conversation).result for conversation in conversations))
        self.now += timedelta(minutes=16)

        service.retry_due_batches()
        service.run_weekly_batch(batch.pk)

        batch.refresh_from_db()
        self.assertEqual(self.phases, ['individual', 'individual', 'final', 'final'])
        self.assertEqual(batch.final_attempts, 2)
        self.assertEqual(batch.status, WeeklyAnalysisBatchModel.StatusEnum.SUCCESS)
        self.assertEqual(LearningAnnotationModel.objects.count(), 1)
        self.assertTrue(batch.final_result)
        self.assertIsNotNone(batch.annotation_id)

    def test_final_analysis_stops_after_three_attempts_without_repeating_individuals(self):
        conversation = self.create_conversation(1)
        batch = self.prepare()
        self.fail_final = True

        service.run_weekly_batch(batch.pk)
        for _ in range(2):
            self.now += timedelta(minutes=16)
            service.retry_due_batches()
        self.now += timedelta(minutes=16)
        service.retry_due_batches()

        batch.refresh_from_db()
        self.assertEqual(self.phases, ['individual', 'final', 'final', 'final'])
        self.assertEqual(self.item(batch, conversation).attempts, 1)
        self.assertEqual(batch.final_attempts, 3)
        self.assertEqual(batch.status, WeeklyAnalysisBatchModel.StatusEnum.FAILED)
        self.assertIsNone(batch.next_attempt_at)
        self.assertFalse(LearningAnnotationModel.objects.exists())

    def test_prepare_keeps_original_membership_and_does_not_duplicate_same_week(self):
        first = self.create_conversation(1)
        batch = self.prepare()
        late = self.create_conversation(2)

        repeated = self.prepare()

        batch.refresh_from_db()
        self.assertEqual(repeated.pk, batch.pk)
        self.assertEqual(batch.conversation_ids, [first.pk])
        self.assertEqual(WeeklyAnalysisBatchModel.objects.filter(agent=self.target).count(), 1)
        self.assertEqual(ConversationAnalysisModel.objects.filter(batch=batch).count(), 1)
        self.assertFalse(ConversationAnalysisModel.objects.filter(batch=batch, conversation=late).exists())

    def test_completed_batch_is_reused_without_repeating_analyses(self):
        self.create_conversation(1)
        batch = self.prepare()
        service.run_weekly_batch(batch.pk)
        batch.refresh_from_db()
        previous_id = batch.annotation_id

        outcome = service.run_weekly_batch(batch.pk)

        batch.refresh_from_db()
        self.assertEqual(outcome['status'], batch.StatusEnum.SUCCESS)
        self.assertEqual(self.phases, ['individual', 'final'])
        self.assertEqual(batch.annotation_id, previous_id)
        self.assertEqual(LearningAnnotationModel.objects.count(), 1)

    def test_weekly_task_analyzes_previous_week_and_reuses_it_when_repeated(self):
        # Lunes 9 de noviembre a las 06:00 en Ciudad de México.
        self.now = datetime(2026, 11, 9, 12, tzinfo=datetime_timezone.utc)
        inside = self.create_conversation(1)
        # Lunes 01:00 en Ciudad de México: pertenece a la semana en curso.
        self.create_conversation(2, at=datetime(2026, 11, 9, 7, tzinfo=datetime_timezone.utc))

        first = tasks.analyze_conversations()
        second = tasks.analyze_conversations()

        batch = WeeklyAnalysisBatchModel.objects.get(agent=self.target)
        self.assertEqual(first['inicio'], '2026-11-02T00:00:00-06:00')
        self.assertEqual(first['fin_exclusivo'], '2026-11-09T00:00:00-06:00')
        self.assertEqual((batch.week_start, batch.week_end), (date(2026, 11, 2), date(2026, 11, 9)))
        self.assertEqual(batch.conversation_ids, [inside.pk])
        self.assertEqual((first['analizadas'], first['anotaciones_creadas']), (1, 1))
        self.assertEqual((second['analizadas'], second['anotaciones_creadas']), (0, 0))
        self.assertEqual(self.phases, ['individual', 'final'])
        self.assertEqual(LearningAnnotationModel.objects.count(), 1)
        self.assertFalse(batch.annotation.is_active)

    def test_pending_batch_is_resumed_after_its_week_has_passed(self):
        historical_start = self.start - timedelta(days=56)
        historical_end = historical_start + timedelta(days=7)
        conversation = self.create_conversation(1, at=historical_start + timedelta(days=2))
        batch = self.prepare(start=historical_start, end=historical_end)

        service.retry_due_batches()

        batch.refresh_from_db()
        self.assertEqual(batch.status, WeeklyAnalysisBatchModel.StatusEnum.SUCCESS)
        self.assertEqual(self.item(batch, conversation).status, ConversationAnalysisModel.StatusEnum.SUCCESS)
        self.assertEqual(self.phases, ['individual', 'final'])
        self.assertEqual(LearningAnnotationModel.objects.count(), 1)

    def test_live_batch_lease_excludes_duplicate_and_expired_lease_is_recovered(self):
        conversation = self.create_conversation(1)
        batch = self.prepare()
        WeeklyAnalysisBatchModel.objects.filter(pk=batch.pk).update(
            status=WeeklyAnalysisBatchModel.StatusEnum.RUNNING,
            claim_token=uuid.uuid4(), lease_until=self.now + timedelta(minutes=30),
        )
        batch.items.update(
            status=ConversationAnalysisModel.StatusEnum.PROCESSING, attempts=1,
            claim_token=uuid.uuid4(), lease_until=self.now + timedelta(minutes=35),
        )

        service.run_weekly_batch(batch.pk)

        self.infer.assert_not_called()
        self.now += timedelta(minutes=31)
        service.retry_due_batches()

        batch.refresh_from_db()
        self.assertEqual(batch.status, WeeklyAnalysisBatchModel.StatusEnum.SUCCESS)
        self.assertEqual(self.phases, ['individual', 'final'])
        self.assertEqual(self.item(batch, conversation).attempts, 2)
        self.assertIsNone(batch.claim_token)
        self.assertIsNone(batch.lease_until)

    def test_retry_consolidation_resumes_at_failed_block(self):
        for number in range(1, 4):
            self.create_conversation(number)
        batch = self.prepare()
        individual = SimpleNamespace(output_parsed=ConversationAnalysisSchema(annotations=[{
            'observaciones': 'Faltó confirmar el plazo. ' * 25,
            'sugerencias': 'Confirmar el plazo cuando falte.',
        }]))
        self.responses = [individual] * 3 + [self.final_output(), RuntimeError('Falló el segundo bloque')]

        with mock.patch.object(service, 'FINAL_CHUNK_LIMIT', 1000):
            service.run_weekly_batch(batch.pk)
            batch.refresh_from_db()
            self.assertEqual(batch.status, batch.StatusEnum.WAITING_RETRY)
            self.assertEqual(batch.final_input['index'], 1)
            self.assertFalse(LearningAnnotationModel.objects.exists())
            self.now += timedelta(minutes=16)
            service.retry_due_batches()

        batch.refresh_from_db()
        self.assertEqual(batch.status, batch.StatusEnum.SUCCESS)
        self.assertEqual(batch.final_attempts, 2)
        self.assertEqual(self.phases, ['individual'] * 3 + ['final'] * 5)
        self.assertEqual(LearningAnnotationModel.objects.count(), 1)
        stages = [json.loads(call.kwargs['input'][0]['content']) for call in self.infer.call_args_list[3:]]
        self.assertEqual(stages[1]['results'], stages[2]['results'])
        self.assertEqual(stages[-1]['stage'], 'final')
        self.assertEqual(len(stages[-1]['results']), 3)

    def test_empty_intermediate_results_are_stored_and_still_reach_final_analysis(self):
        conversation = self.create_conversation(1)
        batch = self.prepare()
        self.empty_individual_results = True

        service.run_weekly_batch(batch.pk)

        batch.refresh_from_db()
        self.assertEqual(self.item(batch, conversation).result, {'annotations': []})
        self.assertEqual(self.item(batch, conversation).status, ConversationAnalysisModel.StatusEnum.SUCCESS)
        self.assertEqual(self.phases, ['individual', 'final'])
        self.assertEqual(batch.status, WeeklyAnalysisBatchModel.StatusEnum.SUCCESS)
        self.assertEqual(LearningAnnotationModel.objects.count(), 1)

    def test_prepare_excludes_ineligible_mixed_and_out_of_range_conversations(self):
        valid = self.create_conversation(1)
        inactive = self.create_agent('inactive-prospector', active=False)
        self.create_conversation(2, target=inactive)
        disabled = self.create_agent('disabled-prospector', learning=False)
        self.create_conversation(3, target=disabled)
        shadow = self.create_agent('shadow-prospector', mode=AgentModel.ModeEnum.SHADOW_TESTING)
        self.create_conversation(4, target=shadow)
        mixed = self.create_conversation(5)
        self.add_run(mixed, disabled)
        self.create_conversation(6, with_run=False)
        self.create_conversation(7, at=self.start - timedelta(seconds=1))
        self.create_conversation(8, at=self.end)

        batches = service.prepare_weekly_batches(self.start, self.end)

        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0].agent_id, self.target.pk)
        self.assertEqual(batches[0].conversation_ids, [valid.pk])
        self.assertEqual(ConversationAnalysisModel.objects.filter(batch=batches[0]).count(), 1)
        self.infer.assert_not_called()

    def test_each_agent_gets_its_own_weekly_final_result(self):
        self.create_conversation(1)
        other = self.create_agent('other-weekly-prospector')
        self.create_conversation(2, target=other)
        batches = service.prepare_weekly_batches(self.start, self.end)

        for batch in batches:
            service.run_weekly_batch(batch.pk)

        self.assertEqual(len(batches), 2)
        self.assertEqual(LearningAnnotationModel.objects.count(), 2)
        self.assertEqual(set(LearningAnnotationModel.objects.values_list('agent_id', flat=True)), {
            self.target.pk, other.pk,
        })
        self.assertEqual(WeeklyAnalysisBatchModel.objects.filter(
            status=WeeklyAnalysisBatchModel.StatusEnum.SUCCESS,
        ).count(), 2)
