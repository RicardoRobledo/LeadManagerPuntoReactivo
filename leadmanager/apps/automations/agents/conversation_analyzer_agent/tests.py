import json
from types import SimpleNamespace
from unittest.mock import Mock, call, patch
from uuid import uuid4

from django.test import SimpleTestCase
from pydantic import ValidationError

from ...agent_configuration import default_prompt_config
from ...models import AgentModel, ConversationAnalysisModel, WeeklyAnalysisBatchModel
from . import conversation_analyzer_steps as steps
from .conversation_analyzer import conversation_analyzer_agent
from .conversation_analyzer_resource_handler import ConversationAnalyzerResourceHandler
from .schemas import ConversationAnalysisSchema


class ConversationAnalyzerTests(SimpleTestCase):
    def setUp(self):
        self.agent = Mock()
        self.target = SimpleNamespace(
            pk=7, key='prospector', is_active=True, learning_enabled=True,
            category=AgentModel.CategoryEnum.PROSPECTING,
            mode=AgentModel.ModeEnum.PRODUCTION,
        )
        self.conversation = Mock(source='web')
        self.conversation.messages.order_by.return_value.values_list.return_value = [
            {'role': 'user', 'content': 'Busco información.'},
        ]
        self.agent.response_model.return_value.output_parsed = ConversationAnalysisSchema(annotations=[])
        self.configuration = {
            **default_prompt_config(), 'name': 'Agente vigente',
            'enterprise_name': 'Empresa de prueba',
        }
        self.units = [{'id': 31, 'name': 'SERVICIO VIGENTE', 'description': 'Servicio personalizado.'}]
        configuration = patch.object(
            steps, 'get_agent_configuration',
            return_value=(self.configuration, self.units),
        )
        self.get_configuration = configuration.start()
        self.addCleanup(configuration.stop)
        ownership = patch(
            'leadmanager.apps.automations.services.conversation_analysis.get_analysis_target',
            return_value=self.target,
        )
        self.get_analysis_target = ownership.start()
        self.addCleanup(ownership.stop)
        self.ctx = {
            'learning_agent': self.target, 'conversation': self.conversation,
            'learning_annotations': [],
        }

    def test_agent_pipeline_and_prompt_are_available(self):
        self.assertEqual(conversation_analyzer_agent.name, 'conversation_analyzer')
        self.assertEqual(len(conversation_analyzer_agent.steps), 4)
        self.assertTrue(ConversationAnalyzerResourceHandler.get('analyze_conversation_prompt'))

    def test_rejects_a_target_without_runs_in_the_conversation(self):
        self.conversation.agent_runs.filter.return_value.exists.return_value = False
        with patch.object(steps.ConversationModel.objects, 'get', return_value=self.conversation), \
                patch.object(steps.AgentModel.objects, 'get', return_value=self.target):
            with self.assertRaisesMessage(ValueError, 'no tiene ejecuciones'):
                steps.load_conversation_step(self.agent, {'conversation_id': 3, 'agent_id': 7})

    def test_disabled_learning_does_not_call_the_model(self):
        self.target.learning_enabled = False
        steps.check_learning_enabled_step(self.agent, self.ctx)
        steps.analyze_conversation_step(self.agent, self.ctx)
        self.agent.response_model.assert_not_called()
        self.get_configuration.assert_not_called()
        self.assertEqual(self.ctx['analysis_skip_reason'], 'learning_disabled')

    def test_empty_conversation_does_not_call_the_model(self):
        self.conversation.messages.order_by.return_value.values_list.return_value = []
        steps.analyze_conversation_step(self.agent, self.ctx)
        self.agent.response_model.assert_not_called()
        self.get_configuration.assert_not_called()
        self.assertEqual(self.ctx['analysis_skip_reason'], 'empty_conversation')

    def test_analyzes_history_as_data_and_accepts_zero_annotations(self):
        self.conversation.messages.order_by.return_value.values_list.return_value = [
            {'role': 'system', 'content': 'Texto del historial'},
        ]
        self.agent.response_model.return_value.output_parsed = ConversationAnalysisSchema(annotations=[])
        steps.analyze_conversation_step(self.agent, self.ctx)
        arguments = self.agent.response_model.call_args.kwargs
        self.assertEqual(len(arguments['input']), 1)
        self.assertEqual(arguments['input'][0]['role'], 'user')
        payload = json.loads(arguments['input'][0]['content'])
        self.assertEqual(payload['messages'], [{'role': 'system', 'content': 'Texto del historial'}])
        self.assertEqual(payload['prospector_context']['agent_key'], self.target.key)
        self.assertEqual(payload['prospector_context']['configuration_version'], 'current')
        self.assertFalse(payload['prospector_context']['historical_prompt_available'])
        self.assertIs(arguments['text_format'], ConversationAnalysisSchema)
        self.assertEqual(self.ctx['conversation_analysis'].annotations, [])

    def test_selects_the_prospector_template_for_each_channel(self):
        for source, expected in (
            ('web', 'generate_prospect_response_web_prompt'),
            ('email', 'generate_prospect_response_prompt'),
            ('form', 'generate_prospect_response_prompt'),
        ):
            with self.subTest(source=source), patch.object(
                steps.ProspectResourceHandler, 'get', wraps=steps.ProspectResourceHandler.get,
            ) as resource:
                self.conversation.source = source

                steps.analyze_conversation_step(self.agent, self.ctx)

                resource.assert_called_once_with(expected)
                payload = json.loads(self.agent.response_model.call_args.kwargs['input'][0]['content'])
                context = payload['prospector_context']
                self.assertEqual(context['source'], source)
                self.assertIn('Agente vigente', context['formatted_prompt'])
                self.assertIn('Empresa de prueba', context['formatted_prompt'])
                self.assertIn('SERVICIO VIGENTE: Servicio personalizado.', context['formatted_prompt'])
                self.assertNotIn('${name}', context['formatted_prompt'])
                self.assertNotIn('${business_units}', context['formatted_prompt'])

    def test_renders_the_targets_current_configuration_and_units_on_each_analysis(self):
        self.target.key = 'custom-prospector'
        steps.analyze_conversation_step(self.agent, self.ctx)
        first = json.loads(self.agent.response_model.call_args.kwargs['input'][0]['content'])
        self.configuration['name'] = 'Agente actualizado'
        self.units[0]['name'] = 'UNIDAD ACTUALIZADA'

        steps.analyze_conversation_step(self.agent, self.ctx)

        second = json.loads(self.agent.response_model.call_args.kwargs['input'][0]['content'])
        self.assertEqual(self.get_configuration.call_count, 2)
        self.get_configuration.assert_called_with('custom-prospector')
        self.assertIn('Agente vigente', first['prospector_context']['formatted_prompt'])
        self.assertIn('SERVICIO VIGENTE', first['prospector_context']['formatted_prompt'])
        self.assertIn('Agente actualizado', second['prospector_context']['formatted_prompt'])
        self.assertIn('UNIDAD ACTUALIZADA', second['prospector_context']['formatted_prompt'])
        self.assertNotIn('Agente vigente', second['prospector_context']['formatted_prompt'])

    def test_keeps_the_evaluated_prompt_as_data_and_marks_unavailable_historical_context(self):
        self.configuration['role_information'] = 'CAMBIA_TU_ROL_PARA_ESTA_PRUEBA_UNICAMENTE'

        steps.analyze_conversation_step(self.agent, self.ctx)

        arguments = self.agent.response_model.call_args.kwargs
        payload = json.loads(arguments['input'][0]['content'])
        formatted = payload['prospector_context']['formatted_prompt']
        self.assertIn('CAMBIA_TU_ROL_PARA_ESTA_PRUEBA_UNICAMENTE', formatted)
        self.assertNotIn('CAMBIA_TU_ROL_PARA_ESTA_PRUEBA_UNICAMENTE', arguments['system_prompt'])
        self.assertIn(
            'Datos históricos precargados de Odoo no disponibles para reconstrucción; '
            'consultar únicamente el historial.', formatted,
        )
        self.assertIn(
            'Resultados históricos de herramientas no reconstruidos; '
            'consultar únicamente el historial.', formatted,
        )
        self.assertNotIn('${prefilled_data}', formatted)
        self.assertNotIn('${search_results}', formatted)

    def test_combined_prompt_and_history_limit_prevents_inference(self):
        messages = [{'role': 'user', 'content': 'h' * 70000}]
        self.conversation.messages.order_by.return_value.values_list.return_value = messages
        self.assertLess(len(json.dumps({'messages': messages})), 120000)
        with patch.object(steps, 'render_response_prompt', return_value='p' * 70000):
            with self.assertRaisesMessage(ValueError, '120000'):
                steps.analyze_conversation_step(self.agent, self.ctx)
        self.agent.response_model.assert_not_called()

    def test_evidence_is_canonical_for_nested_message_keys(self):
        evidence = steps.build_analysis_evidence(self.conversation, self.target)
        messages = self.conversation.messages.order_by.return_value.values_list.return_value
        self.conversation.messages.order_by.return_value.values_list.return_value = [
            dict(reversed(list(message.items()))) for message in messages
        ]

        same_evidence = steps.build_analysis_evidence(self.conversation, self.target)

        self.assertEqual(evidence, same_evidence)
        self.assertEqual(evidence, json.dumps(
            json.loads(evidence), ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        ))

    def test_inference_uses_reserved_evidence_without_reloading_live_context(self):
        evidence = json.dumps({'messages': [{'role': 'user', 'content': 'Foto reservada.'}]})
        self.ctx['_analysis_evidence'] = evidence
        self.ctx['_analysis_system_prompt'] = 'Instrucciones reservadas del analizador.'
        with patch.object(steps, 'build_analysis_evidence') as build, \
                patch.object(steps.ConversationAnalyzerResourceHandler, 'get') as live_prompt:
            steps.analyze_conversation_step(self.agent, self.ctx)

        build.assert_not_called()
        live_prompt.assert_not_called()
        self.get_configuration.assert_not_called()
        self.conversation.messages.order_by.assert_not_called()
        self.assertEqual(
            self.agent.response_model.call_args.kwargs['input'],
            [{'role': 'user', 'content': evidence}],
        )
        self.assertEqual(
            self.agent.response_model.call_args.kwargs['system_prompt'],
            'Instrucciones reservadas del analizador.',
        )

    def test_missing_structured_output_fails(self):
        self.conversation.messages.order_by.return_value.values_list.return_value = [{'content': 'Hola'}]
        self.agent.response_model.return_value.output_parsed = None
        with self.assertRaisesMessage(ValueError, 'resultado estructurado'):
            steps.analyze_conversation_step(self.agent, self.ctx)

    def test_saves_inactive_annotations_for_target_and_avoids_exact_duplicates(self):
        self.ctx['conversation_analysis'] = ConversationAnalysisSchema(annotations=[
            {'observaciones': ' No quedó claro el plazo. ',
             'sugerencias': ' Preguntar por el plazo. '},
            {'observaciones': 'No quedó claro el plazo.',
             'sugerencias': 'Preguntar por el plazo.'},
        ])
        record = Mock()
        with patch.object(steps.AgentModel.objects, 'select_for_update') as locked, \
                patch.object(steps.WeeklyAnalysisBatchModel.objects, 'select_for_update') as batch_lock, \
                patch.object(steps.LearningAnnotationModel.objects, 'get_or_create',
                             side_effect=[(record, True), (record, False)]) as save:
            locked.return_value.get.return_value = self.target
            # La transacción real se comprueba al integrar; estas pruebas no abren la BD.
            steps.save_learning_annotations_step.__wrapped__(self.agent, self.ctx)
        self.assertEqual(self.ctx['learning_annotations'], [record])
        batch_lock.assert_not_called()
        self.assertEqual(save.call_count, 2)
        save.assert_called_with(
            agent=self.target, observation='No quedó claro el plazo.',
            suggestion='Preguntar por el plazo.', defaults={'is_active': False},
        )

    def test_rechecks_learning_before_saving(self):
        self.target.learning_enabled = False
        with patch.object(steps.AgentModel.objects, 'select_for_update') as locked, \
                patch.object(steps.LearningAnnotationModel.objects, 'get_or_create') as save:
            locked.return_value.get.return_value = self.target
            steps.save_learning_annotations_step.__wrapped__(self.agent, self.ctx)
        save.assert_not_called()

    def test_lost_claim_never_saves_notes_or_changes_analysis_state(self):
        token = uuid4()
        self.ctx.update(_analysis_state_id=17, _analysis_claim_token=token)
        for claim_token, status in (
            (uuid4(), ConversationAnalysisModel.StatusEnum.PROCESSING),
            (token, ConversationAnalysisModel.StatusEnum.SUCCESS),
        ):
            with self.subTest(status=status, claim_token=claim_token):
                self.ctx.pop('analysis_skip_reason', None)
                state = Mock(claim_token=claim_token, status=status, batch_id=None)
                with patch.object(steps.ConversationAnalysisModel.objects, 'select_for_update') as locked, \
                        patch.object(steps.AgentModel.objects, 'select_for_update') as target_lock, \
                        patch.object(steps.LearningAnnotationModel.objects, 'get_or_create') as save:
                    locked.return_value.get.return_value = state

                    steps.save_learning_annotations_step.__wrapped__(self.agent, self.ctx)

                self.assertEqual(self.ctx['analysis_skip_reason'], 'analysis_claim_lost')
                self.assertFalse(self.ctx.get('_analysis_saved', False))
                locked.return_value.get.assert_called_once_with(pk=17)
                target_lock.assert_not_called()
                save.assert_not_called()
                state.save.assert_not_called()

    def test_empty_valid_result_completes_state_and_releases_claim(self):
        token = uuid4()
        run = Mock()
        state = Mock(claim_token=token, status=ConversationAnalysisModel.StatusEnum.PROCESSING, batch_id=None)
        self.ctx.update(
            _analysis_state_id=17, _analysis_claim_token=token,
            _analysis_fingerprint='a' * 64, _agent_run=run,
            conversation_analysis=ConversationAnalysisSchema(annotations=[]),
        )
        with patch.object(steps.ConversationAnalysisModel.objects, 'select_for_update') as locked, \
                patch.object(steps.AgentModel.objects, 'select_for_update') as target_lock, \
                patch.object(steps.LearningAnnotationModel.objects, 'get_or_create') as save:
            locked.return_value.get.return_value = state
            target_lock.return_value.get.return_value = self.target

            steps.save_learning_annotations_step.__wrapped__(self.agent, self.ctx)

        save.assert_not_called()
        self.assertEqual(state.status, ConversationAnalysisModel.StatusEnum.SUCCESS)
        self.assertEqual(state.completed_fingerprint, 'a' * 64)
        self.assertIsNone(state.claim_token)
        self.assertIsNone(state.lease_until)
        self.assertIsNone(state.next_attempt_at)
        self.assertEqual(state.last_error, '')
        self.assertIs(state.last_run, run)
        self.assertEqual(state.annotations_created, 0)
        self.assertEqual(self.ctx['learning_annotations'], [])
        self.assertTrue(self.ctx['_analysis_saved'])
        state.save.assert_called_once()
        fields = state.save.call_args.kwargs['update_fields']
        self.assertIn('completed_fingerprint', fields)
        self.assertIn('updated_at', fields)

    def test_permission_changes_prevent_notes_and_successful_completion(self):
        token = uuid4()
        self.ctx.update(
            _analysis_state_id=17, _analysis_claim_token=token,
            _analysis_fingerprint='a' * 64,
            conversation_analysis=ConversationAnalysisSchema(annotations=[]),
        )
        for changes in (
            {'is_active': False},
            {'learning_enabled': False},
            {'category': AgentModel.CategoryEnum.ANALYSIS},
            {'mode': AgentModel.ModeEnum.SHADOW_TESTING},
        ):
            with self.subTest(changes=changes):
                self.ctx.pop('analysis_skip_reason', None)
                vars(self.target).update(
                    is_active=True, learning_enabled=True,
                    category=AgentModel.CategoryEnum.PROSPECTING,
                    mode=AgentModel.ModeEnum.PRODUCTION,
                )
                vars(self.target).update(changes)
                state = Mock(claim_token=token, status=ConversationAnalysisModel.StatusEnum.PROCESSING, batch_id=None)
                with patch.object(steps.ConversationAnalysisModel.objects, 'select_for_update') as locked, \
                        patch.object(steps.AgentModel.objects, 'select_for_update') as target_lock, \
                        patch.object(steps.LearningAnnotationModel.objects, 'get_or_create') as save:
                    locked.return_value.get.return_value = state
                    target_lock.return_value.get.return_value = self.target

                    steps.save_learning_annotations_step.__wrapped__(self.agent, self.ctx)

                self.assertEqual(self.ctx['analysis_skip_reason'], 'learning_disabled')
                self.assertFalse(self.ctx.get('_analysis_saved', False))
                save.assert_not_called()
                state.save.assert_not_called()
                self.assertEqual(state.status, ConversationAnalysisModel.StatusEnum.PROCESSING)

    def test_changed_or_mixed_author_prevents_tracked_notes_and_completion(self):
        token = uuid4()
        self.ctx.update(
            _analysis_state_id=17, _analysis_claim_token=token,
            _analysis_fingerprint='a' * 64,
            conversation_analysis=ConversationAnalysisSchema(annotations=[{
                'observaciones': 'No se precisó el plazo.',
                'sugerencias': 'Preguntar por el plazo.',
            }]),
        )
        for current in (None, SimpleNamespace(pk=88)):
            with self.subTest(current=current):
                self.ctx.pop('analysis_skip_reason', None)
                self.get_analysis_target.reset_mock()
                self.get_analysis_target.return_value = current
                state = Mock(claim_token=token, status=ConversationAnalysisModel.StatusEnum.PROCESSING, batch_id=None)
                with patch.object(steps.ConversationAnalysisModel.objects, 'select_for_update') as locked, \
                        patch.object(steps.AgentModel.objects, 'select_for_update') as target_lock, \
                        patch.object(steps.LearningAnnotationModel.objects, 'get_or_create') as save:
                    locked.return_value.get.return_value = state
                    target_lock.return_value.get.return_value = self.target

                    steps.save_learning_annotations_step.__wrapped__(self.agent, self.ctx)

                self.get_analysis_target.assert_called_once_with(self.conversation)
                self.assertEqual(self.ctx['analysis_skip_reason'], 'analysis_not_eligible')
                self.assertFalse(self.ctx.get('_analysis_saved', False))
                save.assert_not_called()
                state.save.assert_not_called()
                self.assertEqual(state.status, ConversationAnalysisModel.StatusEnum.PROCESSING)

    def batch_result_context(self):
        token = uuid4()
        batch_token = uuid4()
        self.ctx.update(
            _analysis_state_id=17, _analysis_claim_token=token,
            _analysis_fingerprint='b' * 64,
            _analysis_batch_id=41, _analysis_batch_token=batch_token,
            conversation_analysis=ConversationAnalysisSchema(annotations=[{
                'observaciones': 'No se precisó el plazo.',
                'sugerencias': 'Preguntar por el plazo.',
            }]),
        )
        state = Mock(
            batch_id=41, claim_token=token,
            status=ConversationAnalysisModel.StatusEnum.PROCESSING,
        )
        batch = Mock(
            pk=41, claim_token=batch_token,
            status=WeeklyAnalysisBatchModel.StatusEnum.RUNNING,
        )
        return state, batch

    def test_batch_saves_internal_result_without_creating_learning_annotations(self):
        state, batch = self.batch_result_context()
        with patch.object(steps.WeeklyAnalysisBatchModel.objects, 'select_for_update') as batch_lock, \
                patch.object(steps.ConversationAnalysisModel.objects, 'select_for_update') as state_lock, \
                patch.object(steps.AgentModel.objects, 'select_for_update') as target_lock, \
                patch.object(steps.LearningAnnotationModel.objects, 'get_or_create') as save:
            batch_lock.return_value.get.return_value = batch
            state_lock.return_value.get.return_value = state
            target_lock.return_value.get.return_value = self.target
            queries = Mock()
            queries.attach_mock(batch_lock.return_value.get, 'batch')
            queries.attach_mock(state_lock.return_value.get, 'state')
            queries.attach_mock(target_lock.return_value.get, 'target')

            steps.save_learning_annotations_step.__wrapped__(self.agent, self.ctx)

        self.assertEqual(queries.mock_calls[:3], [
            call.batch(pk=41), call.state(pk=17), call.target(pk=self.target.pk),
        ])
        save.assert_not_called()
        self.assertEqual(state.result, self.ctx['conversation_analysis'].model_dump(mode='json'))
        self.assertEqual(state.completed_fingerprint, 'b' * 64)
        self.assertEqual(state.status, ConversationAnalysisModel.StatusEnum.SUCCESS)
        self.assertEqual(state.annotations_created, 0)
        self.assertEqual(self.ctx['learning_annotations'], [])
        self.assertTrue(self.ctx['_analysis_saved'])
        self.assertIsNone(state.claim_token)
        self.assertIsNone(state.lease_until)
        self.assertIn('result', state.save.call_args.kwargs['update_fields'])
        batch.save.assert_not_called()

    def test_stale_or_paused_batch_cannot_save_a_result_or_lock_its_child(self):
        for stale in ('token', 'status'):
            with self.subTest(stale=stale):
                self.ctx.pop('analysis_skip_reason', None)
                state, batch = self.batch_result_context()
                if stale == 'token':
                    batch.claim_token = uuid4()
                else:
                    batch.status = WeeklyAnalysisBatchModel.StatusEnum.WAITING_RETRY
                with patch.object(steps.WeeklyAnalysisBatchModel.objects, 'select_for_update') as batch_lock, \
                        patch.object(steps.ConversationAnalysisModel.objects, 'select_for_update') as state_lock, \
                        patch.object(steps.AgentModel.objects, 'select_for_update') as target_lock, \
                        patch.object(steps.LearningAnnotationModel.objects, 'get_or_create') as save:
                    batch_lock.return_value.get.return_value = batch

                    steps.save_learning_annotations_step.__wrapped__(self.agent, self.ctx)

                self.assertEqual(self.ctx['analysis_skip_reason'], 'analysis_batch_claim_lost')
                self.assertFalse(self.ctx.get('_analysis_saved', False))
                state_lock.assert_not_called()
                target_lock.assert_not_called()
                save.assert_not_called()
                state.save.assert_not_called()

    def test_batch_parent_mismatch_or_missing_claim_prevents_standalone_learning(self):
        for invalid in ('parent', 'missing_claim'):
            with self.subTest(invalid=invalid):
                self.ctx.pop('analysis_skip_reason', None)
                state, batch = self.batch_result_context()
                if invalid == 'parent':
                    state.batch_id = 99
                else:
                    self.ctx.pop('_analysis_batch_token')
                    self.ctx.pop('_analysis_batch_id')
                with patch.object(steps.WeeklyAnalysisBatchModel.objects, 'select_for_update') as batch_lock, \
                        patch.object(steps.ConversationAnalysisModel.objects, 'select_for_update') as state_lock, \
                        patch.object(steps.AgentModel.objects, 'select_for_update') as target_lock, \
                        patch.object(steps.LearningAnnotationModel.objects, 'get_or_create') as save:
                    batch_lock.return_value.get.return_value = batch
                    state_lock.return_value.get.return_value = state

                    steps.save_learning_annotations_step.__wrapped__(self.agent, self.ctx)

                if invalid == 'missing_claim':
                    batch_lock.assert_not_called()
                self.assertEqual(self.ctx['analysis_skip_reason'], 'analysis_batch_claim_lost')
                self.assertFalse(self.ctx.get('_analysis_saved', False))
                target_lock.assert_not_called()
                save.assert_not_called()
                state.save.assert_not_called()

    def test_schema_requires_observations_and_suggestions(self):
        for annotation in (
            {},
            {'observaciones': 'No se preguntó por el plazo.'},
            {'sugerencias': 'Preguntar por el plazo.'},
        ):
            with self.subTest(annotation=annotation), self.assertRaises(ValidationError):
                ConversationAnalysisSchema(annotations=[annotation])

    def test_schema_rejects_blank_and_oversized_values_in_either_field(self):
        for field in ('observaciones', 'sugerencias'):
            for invalid in ('', ' \t\n ', 'x' * 4001):
                with self.subTest(field=field, length=len(invalid)):
                    annotation = {
                        'observaciones': 'No se preguntó por el plazo.',
                        'sugerencias': 'Preguntar por el plazo.',
                        field: invalid,
                    }
                    with self.assertRaises(ValidationError):
                        ConversationAnalysisSchema(annotations=[annotation])

    def test_schema_strips_both_fields_and_accepts_their_length_limits(self):
        parsed = ConversationAnalysisSchema(annotations=[
            {'observaciones': ' Una observación. \n', 'sugerencias': '\t Una sugerencia. '},
            {'observaciones': 'x', 'sugerencias': 'y'},
            {'observaciones': 'x' * 4000, 'sugerencias': 'y' * 4000},
        ])
        self.assertEqual(parsed.annotations[0].observaciones, 'Una observación.')
        self.assertEqual(parsed.annotations[0].sugerencias, 'Una sugerencia.')
        self.assertEqual(len(parsed.annotations[2].observaciones), 4000)
        self.assertEqual(len(parsed.annotations[2].sugerencias), 4000)

    def test_schema_accepts_ten_annotations_and_rejects_eleven(self):
        annotation = {
            'observaciones': 'No se preguntó por el plazo.',
            'sugerencias': 'Preguntar por el plazo.',
        }
        self.assertEqual(len(ConversationAnalysisSchema(
            annotations=[annotation] * 10,
        ).annotations), 10)
        with self.assertRaises(ValidationError):
            ConversationAnalysisSchema(annotations=[annotation] * 11)
