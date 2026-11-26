"""Verifica las correcciones de entrega con LLM y SMTP simulados.

Actualizado tras integrar origin/main e39ace4. Conserva los cinco escenarios
de la investigación y comprueba el comportamiento corregido, sin llamadas externas.
"""

from types import SimpleNamespace
from unittest import mock

from django.test import TestCase, override_settings

from leadmanager.apps.automations.agents.agent_base import Agent, StepExecutionError
from leadmanager.apps.automations.agents.prospector_agent.prospector_steps import (
    generate_history_summary_step, send_response_to_prospect_step,
)
from leadmanager.apps.automations.models import AgentEventModel, AgentRunModel, AgentStepModel
from leadmanager.apps.prospects.models import ConversationModel, EmailMessageModel


@override_settings(CHAT_HISTORY_LIMIT=3)
class ReproduccionEntregaCorreos(TestCase):
    def setUp(self):
        patcher = mock.patch(
            'leadmanager.apps.automations.agents.prospector_agent.prospector_steps.send_email',
            return_value=True,
        )
        self.smtp = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(Agent, 'response_model', return_value=SimpleNamespace(
            output_parsed=SimpleNamespace(model_dump=lambda **kwargs: {
                'reply_to_customer': 'Resumen interno del historial.', 'crm_update': {},
            }),
        ))
        self.llm = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch(
            'leadmanager.apps.automations.agents.prospector_agent.prospector_steps.get_agent_configuration',
            return_value=({}, [{'name': 'NO_DETERMINADO'}]),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.conversation = ConversationModel.objects.create(
            session_id='destino@example.test', source='email',
        )
        self.incoming = EmailMessageModel.objects.create(
            message_id='<entrada@example.test>', email='destino@example.test', status='processing',
        )
        self.ctx = {
            'source': 'email', 'conversation': self.conversation,
            'email': {'from': {'email': 'destino@example.test'}, 'message_id': self.incoming.message_id},
            'response_to_prospect': {'reply_to_customer': 'Respuesta para el cliente.', 'crm_update': {}},
            'history': [{'role': 'user', 'content': 'Información'}] * 3,
        }
        self.agent = Agent('prospector', steps=[generate_history_summary_step, send_response_to_prospect_step])

    def test_alcanzar_el_limite_con_resumen_correcto_si_continua_y_envia_la_respuesta(self):
        self.agent.run(self.ctx)

        self.llm.assert_called_once()
        self.smtp.assert_called_once()
        self.assertEqual(self.smtp.call_args.kwargs['body'], 'Respuesta para el cliente.')
        self.assertEqual(AgentRunModel.objects.get().status, 'success')

    def test_un_error_al_resumir_permite_enviar_y_conserva_el_historial(self):
        self.llm.side_effect = RuntimeError('Fallo simulado del servicio de resumen')
        with self.assertLogs(
            'leadmanager.apps.automations.agents.prospector_agent.prospector_steps',
            level='WARNING',
        ):
            self.agent.run(self.ctx)

        self.smtp.assert_called_once()
        self.assertEqual(self.smtp.call_args.kwargs['body'], 'Respuesta para el cliente.')
        self.assertEqual(len(self.ctx['history']), 3)
        self.assertEqual(AgentRunModel.objects.get().status, 'success')
        self.assertTrue(AgentStepModel.objects.filter(
            step_name='send_response_to_prospect_step', status='success',
        ).exists())
        self.assertTrue(AgentEventModel.objects.filter(
            message='No se generó el resumen del historial; se continúa sin compactar',
        ).exists())

    def test_por_debajo_del_limite_no_resume_y_si_envia(self):
        self.ctx['history'] = self.ctx['history'][:2]
        self.agent.run(self.ctx)

        self.llm.assert_not_called()
        self.smtp.assert_called_once()

    def test_fallo_smtp_en_correo_se_registra_como_fallo_sin_marcar_procesado(self):
        self.ctx['history'] = []
        self.smtp.return_value = False
        with self.assertLogs('leadmanager.apps.automations.agents.agent_base', level='ERROR'):
            with self.assertRaises(StepExecutionError) as caught:
                self.agent.run(self.ctx)

        self.assertEqual(caught.exception.step_name, 'send_response_to_prospect_step')
        self.smtp.assert_called_once()
        self.incoming.refresh_from_db()
        self.assertEqual(self.incoming.status, 'processing')
        self.assertEqual(AgentRunModel.objects.get().status, 'failed')
        self.assertFalse(AgentEventModel.objects.filter(message='Correo enviado al prospecto').exists())

    def test_fallo_smtp_en_formulario_se_registra_como_fallo(self):
        self.ctx.update(source='form', form={'email': 'destino@example.test'}, history=[])
        self.smtp.return_value = False
        with self.assertLogs('leadmanager.apps.automations.agents.agent_base', level='ERROR'):
            with self.assertRaises(StepExecutionError) as caught:
                self.agent.run(self.ctx)

        self.assertEqual(caught.exception.step_name, 'send_response_to_prospect_step')
        self.smtp.assert_called_once()
        self.assertEqual(AgentRunModel.objects.get().status, 'failed')
        self.assertFalse(AgentEventModel.objects.filter(
            message='Correo enviado al prospecto (contacto por formulario)',
        ).exists())
