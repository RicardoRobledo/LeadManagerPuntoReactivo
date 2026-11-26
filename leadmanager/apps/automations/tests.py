from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from leadmanager.apps.prospects.models import (
    ConversationModel,
    ConversationStateEnum,
    ConversationSourceEnum,
    EmailReminderEnum,
)

from .models import AgentRunModel, AgentStepModel, AgentEventModel
from .tasks import send_reminders_to_stale_conversations


class SeedAgentsTests(TestCase):
    def test_registers_both_agents_with_valid_configuration(self):
        from .agent_catalog import seed_agents
        from .agent_configuration import validate_prompt_config
        from .models import AgentModel

        results = seed_agents()
        self.assertEqual({agent.key for agent, _ in results}, {'prospector', 'conversation_analyzer'})
        analyzer = AgentModel.objects.get(key='conversation_analyzer')
        self.assertEqual(analyzer.name, 'María')
        self.assertEqual(analyzer.avatar, '')
        self.assertTrue(analyzer.is_active)
        self.assertFalse(analyzer.learning_enabled)
        self.assertEqual(analyzer.channels, [])
        self.assertEqual(analyzer.prompt['reminder_schedule'], [])
        self.assertEqual(analyzer.prompt['escalation_rules'], [])
        validate_prompt_config(analyzer.prompt)

    def test_repeated_seed_preserves_existing_analyzer_and_prospector(self):
        from .agent_catalog import seed_agents
        from .models import AgentModel

        seed_agents()
        analyzer = AgentModel.objects.get(key='conversation_analyzer')
        config = {**analyzer.prompt, 'name': 'Analizador personalizado',
                  'role_information': 'Instrucciones editadas por el administrador.'}
        analyzer.prompt = config
        analyzer.is_active = False
        analyzer.save()
        analyzer.refresh_from_db()
        original_config = analyzer.prompt
        prospector = AgentModel.objects.get(key='prospector')
        prospector.prompt = {**prospector.prompt, 'name': 'Prospector personalizado'}
        prospector.learning_enabled = True
        prospector.mode = AgentModel.ModeEnum.SHADOW_TESTING
        prospector.save()
        prospector.refresh_from_db()
        prospector_config = prospector.prompt

        results = seed_agents()
        self.assertTrue(all(not created for _, created in results))
        self.assertEqual(AgentModel.objects.count(), 2)
        analyzer.refresh_from_db()
        prospector.refresh_from_db()
        self.assertEqual(analyzer.prompt, original_config)
        self.assertEqual(analyzer.name, 'Analizador personalizado')
        self.assertFalse(analyzer.is_active)
        self.assertEqual(prospector.prompt, prospector_config)
        self.assertTrue(prospector.learning_enabled)
        self.assertEqual(prospector.mode, AgentModel.ModeEnum.SHADOW_TESTING)

    def test_command_reports_both_agents(self):
        from io import StringIO
        from django.core.management import call_command

        output = StringIO()
        call_command('seed_agents', stdout=output)
        self.assertIn('Sofía: creado', output.getvalue())
        self.assertIn('María: creado', output.getvalue())


class AgentRunsViewTests(TestCase):

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='tester',
            password='test-pass-123',
        )
        self.client.force_login(self.user)

        self.url = reverse('app_automations:runs')

    def create_run(self, agent_name='prospector', source='email',
                   status=AgentRunModel.StatusEnum.SUCCESS):
        return AgentRunModel.objects.create(
            agent_name=agent_name,
            source=source,
            status=status,
        )

    def test_lista_renderiza_y_muestra_runs(self):
        self.create_run()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Automations Traceability')
        self.assertContains(response, 'prospector')

    def test_filtra_por_status(self):
        self.create_run(
            source='email',
            status=AgentRunModel.StatusEnum.SUCCESS,
        )
        self.create_run(
            source='whatsapp',
            status=AgentRunModel.StatusEnum.FAILED,
        )

        response = self.client.get(self.url, {'status': 'failed'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'whatsapp')
        self.assertNotContains(response, 'email')

    def test_requiere_login(self):
        self.client.logout()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)


class AgentRunDetailViewTests(TestCase):

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='tester',
            password='test-pass-123',
        )
        self.client.force_login(self.user)

    def test_detalle_renderiza_steps_y_eventos(self):
        run = AgentRunModel.objects.create(
            agent_name='prospector',
            source='email',
            status=AgentRunModel.StatusEnum.SUCCESS,
        )
        step = AgentStepModel.objects.create(
            run=run,
            step_name='search_information_step',
            order=1,
            status=AgentStepModel.StatusEnum.SUCCESS,
        )
        AgentEventModel.objects.create(
            run=run,
            step=step,
            message='Seller ID obtenido: 14',
        )

        response = self.client.get(
            reverse('app_automations:run_detail', args=[run.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'search_information_step')
        self.assertContains(response, 'Seller ID obtenido: 14')

    def test_detalle_run_inexistente_devuelve_404(self):
        response = self.client.get(
            reverse('app_automations:run_detail', args=[9999])
        )

        self.assertEqual(response.status_code, 404)


class SendRemindersToStaleConversationsTests(TestCase):

    @mock.patch(
        'leadmanager.apps.automations.tasks.send_reminder_email'
    )
    def test_envia_y_avanza_fase_en_conversaciones_viejas(self, mock_send):
        mock_send.return_value = True

        vieja = ConversationModel.objects.create(
            session_id='viejo@example.com',
            source=ConversationSourceEnum.EMAIL,
            state=ConversationStateEnum.OPEN,
        )
        vieja.created_at = timezone.now() - timedelta(hours=49)
        vieja.save(update_fields=['created_at'])

        result = send_reminders_to_stale_conversations.run()

        self.assertEqual(result['procesados'], 1)
        mock_send.assert_called_once()

        vieja.refresh_from_db()
        self.assertEqual(
            vieja.email_reminder_phase, EmailReminderEnum.FIRST)
        self.assertEqual(vieja.state, ConversationStateEnum.OPEN)

    @mock.patch(
        'leadmanager.apps.automations.tasks.send_reminder_email'
    )
    def test_ignora_recientes_whatsapp_y_sin_email(self, mock_send):
        reciente = ConversationModel.objects.create(
            session_id='reciente@example.com',
            source=ConversationSourceEnum.EMAIL,
            state=ConversationStateEnum.OPEN,
        )

        whatsapp = ConversationModel.objects.create(
            session_id='+5215555555555',
            source=ConversationSourceEnum.WHATSAPP,
            state=ConversationStateEnum.OPEN,
        )
        whatsapp.created_at = timezone.now() - timedelta(hours=49)
        whatsapp.save(update_fields=['created_at'])

        sin_email = ConversationModel.objects.create(
            session_id=None,
            source=ConversationSourceEnum.EMAIL,
            state=ConversationStateEnum.OPEN,
        )
        sin_email.created_at = timezone.now() - timedelta(hours=49)
        sin_email.save(update_fields=['created_at'])

        result = send_reminders_to_stale_conversations.run()

        self.assertEqual(result['procesados'], 0)
        mock_send.assert_not_called()
