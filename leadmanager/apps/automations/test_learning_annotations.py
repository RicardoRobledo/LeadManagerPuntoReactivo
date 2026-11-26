"""Condensación de anotaciones con inferencia simulada y reemplazo atómico."""

from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase

from .agents.conversation_analyzer_agent.schemas import FinalAnalysisSchema
from .models import AgentModel, LearningAnnotationModel
from .services import learning_annotations as service


class AnnotationCondensationTests(TestCase):
    def setUp(self):
        self.agent = AgentModel.objects.create(
            key='condensation-agent', name='Ana', role='Prospección', description='Prueba',
        )
        self.originals = [
            LearningAnnotationModel.objects.create(
                agent=self.agent, observation=f'Hallazgo {index}.',
                suggestion=f'Confirmar el dato {index} cuando falte.', is_active=True,
            )
            for index in range(service.MAX_ACTIVE_ANNOTATIONS)
        ]
        self.inactive = LearningAnnotationModel.objects.create(
            agent=self.agent, suggestion='Sugerencia pendiente.', is_active=False,
        )
        self.reply = SimpleNamespace(output_parsed=FinalAnalysisSchema(
            observaciones='Se documentaron datos pendientes de confirmar.',
            sugerencias='Confirmar los datos requeridos cuando falten.',
        ))
        inference = patch.object(service.Agent, 'response_model', return_value=self.reply)
        self.infer = inference.start()
        self.addCleanup(inference.stop)

    def assert_originals_preserved(self):
        self.assertEqual(
            set(self.agent.learning_annotations.filter(is_active=True).values_list('pk', flat=True)),
            {annotation.pk for annotation in self.originals},
        )
        self.assertEqual(self.agent.learning_annotations.count(), service.MAX_ACTIVE_ANNOTATIONS + 1)

    def test_replaces_only_the_agents_active_annotations_with_one_active_summary(self):
        other_agent = AgentModel.objects.create(key='other-notes', name='Luis', role='Asistente')
        other_note = LearningAnnotationModel.objects.create(agent=other_agent, suggestion='Otra regla.')

        summary = service.condense_active_annotations(self.agent.pk)

        self.assertTrue(summary.is_active)
        self.assertEqual(summary.agent_id, self.agent.pk)
        self.assertEqual(summary.observation, self.reply.output_parsed.observaciones)
        self.assertEqual(summary.suggestion, self.reply.output_parsed.sugerencias)
        self.assertFalse(LearningAnnotationModel.objects.filter(pk__in=[row.pk for row in self.originals]).exists())
        self.inactive.refresh_from_db()
        self.assertFalse(self.inactive.is_active)
        self.assertEqual(self.inactive.suggestion, 'Sugerencia pendiente.')
        self.assertTrue(LearningAnnotationModel.objects.filter(pk=other_note.pk).exists())
        self.assertEqual(self.agent.learning_annotations.filter(is_active=True).count(), 1)

    def test_api_failure_preserves_all_original_annotations(self):
        self.infer.side_effect = RuntimeError('Servicio de IA no disponible.')

        with self.assertRaises(RuntimeError):
            service.condense_active_annotations(self.agent.pk)

        self.assert_originals_preserved()

    def test_empty_or_invalid_model_result_preserves_all_original_annotations(self):
        for parsed in (None, {'observaciones': ' ', 'sugerencias': ' '}):
            with self.subTest(parsed=parsed):
                self.infer.return_value = SimpleNamespace(output_parsed=parsed)
                with self.assertRaises(ValidationError):
                    service.condense_active_annotations(self.agent.pk)
                self.assert_originals_preserved()

    def test_edit_during_inference_rejects_the_stale_summary(self):
        changed = self.originals[0]

        def edit_during_response(**kwargs):
            LearningAnnotationModel.objects.filter(pk=changed.pk).update(suggestion='Regla editada.')
            return self.reply

        self.infer.side_effect = edit_during_response

        with self.assertRaisesMessage(ValidationError, 'cambiaron durante el resumen'):
            service.condense_active_annotations(self.agent.pk)

        self.assert_originals_preserved()
        changed.refresh_from_db()
        self.assertEqual(changed.suggestion, 'Regla editada.')

    def test_configuration_change_during_inference_rejects_the_stale_summary(self):
        def change_configuration(**kwargs):
            AgentModel.objects.filter(pk=self.agent.pk).update(prompt={})
            return self.reply

        self.infer.side_effect = change_configuration

        with self.assertRaisesMessage(ValidationError, 'cambiaron durante el resumen'):
            service.condense_active_annotations(self.agent.pk)

        self.assert_originals_preserved()

    def test_failed_summary_activation_rolls_back_the_creation_and_deletion(self):
        save = LearningAnnotationModel.save

        def fail_activation(annotation, *args, **kwargs):
            if annotation.is_active:
                raise ValidationError('No se pudo activar el resumen.')
            return save(annotation, *args, **kwargs)

        with patch.object(LearningAnnotationModel, 'save', new=fail_activation):
            with self.assertRaisesMessage(ValidationError, 'No se pudo activar el resumen'):
                service.condense_active_annotations(self.agent.pk)

        self.assert_originals_preserved()

    def test_repeated_request_after_success_preserves_the_summary(self):
        summary = service.condense_active_annotations(self.agent.pk)
        self.infer.reset_mock()

        with self.assertRaises(ValidationError):
            service.condense_active_annotations(self.agent.pk)

        self.infer.assert_not_called()
        self.assertTrue(LearningAnnotationModel.objects.filter(pk=summary.pk, is_active=True).exists())

    def test_zero_or_one_active_annotation_preserves_state_and_skips_inference(self):
        for active_count in (0, 1):
            with self.subTest(active_count=active_count):
                self.agent.learning_annotations.update(is_active=False)
                if active_count:
                    self.agent.learning_annotations.filter(pk=self.originals[0].pk).update(is_active=True)
                before = list(self.agent.learning_annotations.order_by('pk').values())

                with self.assertRaises(ValidationError):
                    service.condense_active_annotations(self.agent.pk)

                self.infer.assert_not_called()
                self.assertEqual(list(self.agent.learning_annotations.order_by('pk').values()), before)

    def test_exactly_two_active_annotations_are_summarized_preserving_inactive_ones(self):
        self.agent.learning_annotations.filter(pk__in=[row.pk for row in self.originals[2:]]).update(
            is_active=False,
        )
        inactive_before = list(self.agent.learning_annotations.filter(is_active=False).order_by('pk').values())

        summary = service.condense_active_annotations(self.agent.pk)

        self.assertTrue(summary.is_active)
        self.assertEqual(summary.suggestion, self.reply.output_parsed.sugerencias)
        self.assertFalse(self.agent.learning_annotations.filter(pk__in=[row.pk for row in self.originals[:2]]).exists())
        self.assertEqual(list(self.agent.learning_annotations.filter(is_active=False).order_by('pk').values()),
                         inactive_before)
        self.assertEqual(self.agent.learning_annotations.filter(is_active=True).count(), 1)
        self.assertEqual(self.agent.learning_annotations.count(), len(inactive_before) + 1)
