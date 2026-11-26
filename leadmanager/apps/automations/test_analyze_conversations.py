"""Orquestación de los lotes semanales, sin base de datos ni inferencia."""

from datetime import datetime, timezone as datetime_timezone
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from . import tasks
from .models import WeeklyAnalysisBatchModel


class AnalyzeConversationsTaskTests(SimpleTestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 10, 12, tzinfo=datetime_timezone.utc)
        patches = {
            'clock': mock.patch.object(tasks.timezone, 'now', return_value=self.now),
            'enabled': mock.patch.object(tasks, 'analyzer_enabled', return_value=True),
            'prepare': mock.patch.object(tasks, 'prepare_weekly_batches', return_value=[]),
            'run_batch': mock.patch.object(tasks, 'run_weekly_batch'),
            'retry': mock.patch.object(tasks, 'retry_due_batches'),
        }
        for name, patcher in patches.items():
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)

    def test_tasks_are_registered_with_expected_names(self):
        self.assertEqual(tasks.analyze_conversations.name, 'analyze_conversations')
        self.assertEqual(tasks.retry_conversation_analyses.name, 'retry_conversation_analyses')

    def test_prepares_previous_mexico_calendar_week_and_returns_empty_totals(self):
        result = tasks.analyze_conversations.run()

        zone = ZoneInfo('America/Mexico_City')
        self.prepare.assert_called_once_with(
            datetime(2026, 11, 2, tzinfo=zone), datetime(2026, 11, 9, tzinfo=zone),
        )
        self.assertEqual(result['inicio'], '2026-11-02T00:00:00-06:00')
        self.assertEqual(result['fin_exclusivo'], '2026-11-09T00:00:00-06:00')
        self.assertEqual(result['zona_horaria'], 'America/Mexico_City')
        for key in (
            'total_conversaciones', 'lotes_total', 'lotes_completados',
            'analizadas', 'omitidas', 'fallidas', 'anotaciones_creadas',
        ):
            with self.subTest(counter=key):
                self.assertEqual(result[key], 0)
        self.run_batch.assert_not_called()

    def test_utc_monday_still_uses_mexico_sunday_for_week_boundaries(self):
        self.clock.return_value = datetime(2026, 11, 9, 2, tzinfo=datetime_timezone.utc)

        result = tasks.analyze_conversations.run()

        self.assertEqual(result['inicio'], '2026-10-26T00:00:00-06:00')
        self.assertEqual(result['fin_exclusivo'], '2026-11-02T00:00:00-06:00')
        zone = ZoneInfo('America/Mexico_City')
        self.prepare.assert_called_once_with(
            datetime(2026, 10, 26, tzinfo=zone), datetime(2026, 11, 2, tzinfo=zone),
        )

    def test_disabled_analyzer_does_not_prepare_or_execute_batches(self):
        self.enabled.return_value = False

        result = tasks.analyze_conversations.run()

        self.assertEqual(result['motivo_omision'], 'analizador_inactivo')
        self.assertEqual(result['lotes_total'], 0)
        self.assertEqual(result['analizadas'], 0)
        self.prepare.assert_not_called()
        self.run_batch.assert_not_called()

    def test_aggregates_two_batches_and_counts_only_successful_batch_as_complete(self):
        self.prepare.return_value = [
            SimpleNamespace(pk=71, conversation_ids=[11, 12]),
            SimpleNamespace(pk=83, conversation_ids=[21, 22, 23, 24]),
        ]
        self.run_batch.side_effect = [
            {
                'status': WeeklyAnalysisBatchModel.StatusEnum.WAITING_RETRY,
                'analizadas': 1, 'omitidas': 0, 'fallidas': 1, 'anotaciones_creadas': 0,
            },
            {
                'status': WeeklyAnalysisBatchModel.StatusEnum.SUCCESS,
                'analizadas': 3, 'omitidas': 1, 'fallidas': 0, 'anotaciones_creadas': 1,
            },
        ]

        result = tasks.analyze_conversations.run()

        self.assertEqual(self.run_batch.call_args_list, [mock.call(71), mock.call(83)])
        self.assertEqual(result['lotes_total'], 2)
        self.assertEqual(result['lotes_completados'], 1)
        self.assertEqual(result['total_conversaciones'], 6)
        self.assertEqual(result['analizadas'], 4)
        self.assertEqual(result['omitidas'], 1)
        self.assertEqual(result['fallidas'], 1)
        self.assertEqual(result['anotaciones_creadas'], 1)

    def test_retry_task_delegates_without_rebuilding_weekly_window(self):
        expected = {
            'procesadas': 2, 'analizadas': 1, 'omitidas': 0,
            'fallidas': 1, 'anotaciones_creadas': 1,
        }
        self.retry.return_value = expected

        result = tasks.retry_conversation_analyses.run()

        self.assertIs(result, expected)
        self.retry.assert_called_once_with()
        self.prepare.assert_not_called()
        self.run_batch.assert_not_called()
        self.clock.assert_not_called()
