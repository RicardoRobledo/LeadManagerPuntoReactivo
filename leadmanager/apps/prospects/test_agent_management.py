from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
import json
from django.test import Client, TestCase
from django.core.signals import request_finished
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, transaction
from django.urls import reverse
from html.parser import HTMLParser
from tempfile import TemporaryDirectory
from unittest.mock import patch
from django.core.files.uploadedfile import SimpleUploadedFile

from leadmanager.apps.automations.models import AgentModel, AgentChannelAssignment, LearningAnnotationModel
from leadmanager.apps.automations.agent_channels import _conflicts
from leadmanager.apps.automations.services.conversation_reminders import reminder_agent_names, reminder_email_content



def close_file_response(response):
    """
    Cierra el archivo de un FileResponse sin cerrar la conexión a la base.

    response.close() dispara la señal request_finished, y Django responde
    cerrando la conexión. Dentro de las pruebas eso deja sin base a todas
    las que corren después ("connection already closed"). El archivo sí se
    tiene que cerrar: en Windows no se puede borrar la carpeta temporal con
    la imagen abierta.
    """
    request_finished.disconnect(close_old_connections)
    try:
        response.close()
    finally:
        request_finished.connect(close_old_connections)

class AgentAnnotationManagementTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='annotation-manager')
        self.client.force_login(self.user)
        self.agent = AgentModel.objects.create(
            key='agent-notes', name='Ana', role='Prospección', description='Agente de prueba',
        )
        self.annotation = LearningAnnotationModel.objects.create(
            agent=self.agent, observation='Faltó confirmar el plazo.',
            suggestion='Confirmar el plazo de entrega cuando falte.', is_active=False,
        )
        self.configuration_url = reverse('app_prospects:agent_configuration', args=[self.agent.key])
        self.status_url = reverse('app_prospects:agent_annotation_status', args=[self.agent.key, self.annotation.pk])

    def add_page_of_newer_annotations(self):
        from datetime import timedelta

        LearningAnnotationModel.objects.filter(pk=self.annotation.pk).update(
            created_at=self.annotation.created_at - timedelta(days=1),
        )
        LearningAnnotationModel.objects.bulk_create([
            LearningAnnotationModel(agent=self.agent, suggestion=f'Sugerencia reciente {number}.', is_active=False)
            for number in range(30)
        ])

    def add_active_annotations(self):
        return [
            LearningAnnotationModel.objects.create(
                agent=self.agent, suggestion=f'Regla activa {number}.', is_active=True,
            )
            for number in range(10)
        ]

    def assert_annotations_tab(self, response, *, selected):
        status_url = self.status_url

        class Markup(HTMLParser):
            def __init__(self):
                super().__init__()
                self.elements = {}
                self.navigation = []
                self.tab_navigation = None
                self.form_depth = 0
                self.status_form_depths = []

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                element_id = attrs.get('id')
                if element_id in ('promptStepButton-annotations', 'agentPromptForm', 'anotaciones'):
                    self.elements[element_id] = (tag, attrs)
                if tag == 'nav':
                    self.navigation.append(attrs.get('aria-label'))
                if element_id == 'promptStepButton-annotations' and self.navigation:
                    self.tab_navigation = self.navigation[-1]
                if tag == 'form':
                    if attrs.get('action') == status_url:
                        self.status_form_depths.append(self.form_depth)
                    self.form_depth += 1

            def handle_endtag(self, tag):
                if tag == 'nav':
                    self.navigation.pop()
                if tag == 'form':
                    self.form_depth -= 1

        self.assertEqual(response.status_code, 200)
        markup = Markup()
        markup.feed(response.content.decode())
        tag, button = markup.elements['promptStepButton-annotations']
        self.assertEqual(tag, 'button')
        self.assertEqual(markup.tab_navigation, 'Secciones de configuración')
        self.assertEqual(button['aria-controls'], 'anotaciones')
        self.assertEqual(button['aria-current'], 'step' if selected else 'false')
        self.assertEqual('hidden' in markup.elements['agentPromptForm'][1], selected)
        self.assertEqual('hidden' in markup.elements['anotaciones'][1], not selected)
        self.assertEqual(markup.status_form_depths, [0])

    def test_configuration_displays_only_the_selected_agents_annotations(self):
        other_agent = AgentModel.objects.create(
            key='other-agent', name='Luis', role='Prospección', description='Otro agente',
        )
        other_note = LearningAnnotationModel.objects.create(agent=other_agent, suggestion='Sugerencia de otro agente.')

        response = self.client.get(self.configuration_url)

        self.assert_annotations_tab(response, selected=False)
        panel = response.content.decode().split('id="anotaciones"', 1)[1]
        table, close_table, _ = panel.partition('</table>')
        self.assertIn('<table', table)
        self.assertTrue(close_table)
        for label in ('ID', 'Creada', 'Actualizada', 'Estado', 'Ver detalle'):
            self.assertRegex(table, rf'>\s*{label}\s*<')
        row, detail_start, detail = table.partition('<template')
        self.assertTrue(detail_start)
        full_id = str(self.annotation.pk)
        self.assertRegex(row, rf'>\s*{full_id[:8]}…\s*<')
        self.assertNotRegex(row, rf'>\s*{full_id}\s*<')
        self.assertNotIn(self.annotation.observation, row)
        self.assertEqual(row.count('<time datetime="'), 2)
        detail = detail.partition('</template>')[0]
        # El modal ya no muestra el id: solo la tabla lo lleva, abreviado.
        self.assertNotRegex(detail, rf'>\s*{full_id}\s*<')
        self.assertNotIn('ID de la anotación', detail)
        for label in ('Agente destinatario', 'Creada', 'Actualizada', 'Estado'):
            self.assertRegex(detail, rf'>\s*{label}\s*<')
        self.assertRegex(detail, rf'>\s*{self.agent.name}\s*<')
        self.assertRegex(detail, r'>\s*Desactivada\s*<')
        self.assertEqual(detail.count('<time datetime="'), 2)
        self.assertIn(self.annotation.observation, detail)
        self.assertIn(self.annotation.suggestion, detail)
        self.assertIn(self.status_url, detail)
        self.assertNotContains(response, other_note.suggestion)

    def test_paginating_annotations_opens_the_tab_and_keeps_prompt_hidden(self):
        self.add_page_of_newer_annotations()

        first_page = self.client.get(self.configuration_url, {'annotation_page': '1'})
        self.assertContains(first_page, 'href="?annotation_page=2#anotaciones"')

        response = self.client.get(self.configuration_url, {'annotation_page': '2'})

        self.assert_annotations_tab(response, selected=True)
        self.assertEqual(response.context['annotation_page'].number, 2)
        self.assertContains(response, 'href="?annotation_page=1#anotaciones"')
        self.assertContains(response, self.annotation.suggestion)
        self.assertNotContains(response, 'Sugerencia reciente 0.')

    def annotation_url(self, action, annotation=None, agent=None):
        return reverse(f'app_prospects:agent_annotation_{action}', args=[
            (agent or self.agent).key, (annotation or self.annotation).pk,
        ])

    def test_detail_offers_edit_and_delete(self):
        response = self.client.get(self.configuration_url, {'annotation_page': '1'})
        self.assertContains(response, self.annotation_url('update'))
        self.assertContains(response, self.annotation_url('delete'))
        self.assertContains(response, 'name="suggestion"')
        self.assertContains(response, 'Sí, eliminar')

    def test_edit_suggestions_keeps_observations_page_and_filter(self):
        response = self.client.post(self.annotation_url('update'), {
            'suggestion': '  Confirmar fecha y presupuesto.  ',
            'annotation_page': '2', 'annotation_status': 'active',
        })
        self.assertRedirects(
            response, self.configuration_url + '?annotation_page=2&annotation_status=active#anotaciones',
            fetch_redirect_response=False,
        )
        self.annotation.refresh_from_db()
        self.assertEqual(self.annotation.suggestion, 'Confirmar fecha y presupuesto.')
        self.assertEqual(self.annotation.observation, 'Faltó confirmar el plazo.')
        self.assertFalse(self.annotation.is_active)

    def test_edit_rejects_empty_or_too_long_suggestions(self):
        original = self.annotation.suggestion
        for value, message in (('   ', 'no pueden quedar vacías'), ('x' * 4001, 'hasta 4000 caracteres')):
            with self.subTest(length=len(value)):
                response = self.client.post(self.annotation_url('update'), {'suggestion': value}, follow=True)
                self.assertContains(response, message)
                self.annotation.refresh_from_db()
                self.assertEqual(self.annotation.suggestion, original)

    def test_delete_removes_only_that_annotation(self):
        other = LearningAnnotationModel.objects.create(agent=self.agent, suggestion='Se queda.')
        response = self.client.post(self.annotation_url('delete'), {'annotation_page': '1'})
        self.assertRedirects(response, self.configuration_url + '?annotation_page=1#anotaciones',
                             fetch_redirect_response=False)
        self.assertFalse(LearningAnnotationModel.objects.filter(pk=self.annotation.pk).exists())
        self.assertTrue(LearningAnnotationModel.objects.filter(pk=other.pk).exists())

    def test_edit_and_delete_reject_other_agent_get_and_anonymous(self):
        other_agent = AgentModel.objects.create(key='agent-other', name='Otro', role='Prospección', description='Otro')
        for action in ('update', 'delete'):
            with self.subTest(action=action):
                self.assertEqual(self.client.post(self.annotation_url(action, agent=other_agent),
                                                  {'suggestion': 'Cambio'}).status_code, 404)
                self.assertEqual(self.client.get(self.annotation_url(action)).status_code, 405)
        self.client.logout()
        for action in ('update', 'delete'):
            self.assertEqual(self.client.post(self.annotation_url(action), {'suggestion': 'Cambio'}).status_code, 302)
        self.annotation.refresh_from_db()
        self.assertEqual(self.annotation.suggestion, 'Confirmar el plazo de entrega cuando falte.')

    def test_table_shows_id_and_twelve_hour_dates(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from django.utils import timezone

        afternoon = datetime(2026, 11, 10, 15, 5, tzinfo=ZoneInfo('America/Mexico_City'))
        LearningAnnotationModel.objects.filter(pk=self.annotation.pk).update(created_at=afternoon)

        response = self.client.get(self.configuration_url, {'annotation_page': '1'})

        twelve_hours = timezone.localtime(afternoon).strftime('%d/%m/%Y') + ' 3:05'
        self.assertContains(response, twelve_hours)
        self.assertNotContains(response, '10/11/2026 15:05')
        self.assertContains(response, str(self.annotation.pk)[:8])
        self.assertContains(response, str(self.annotation.pk))

    def test_filter_shows_all_or_only_active_annotations(self):
        active = LearningAnnotationModel.objects.create(
            agent=self.agent, suggestion='Activa y vigente.', is_active=True,
        )

        everything = self.client.get(self.configuration_url, {'annotation_page': '1'})
        self.assertEqual({item.pk for item in everything.context['annotation_page']},
                         {self.annotation.pk, active.pk})
        self.assertIsNone(everything.context['annotation_status'])

        only_active = self.client.get(self.configuration_url, {'annotation_status': 'active'})
        self.assertEqual([item.pk for item in only_active.context['annotation_page']], [active.pk])
        self.assertEqual(only_active.context['annotation_status'], 'active')
        self.assertTrue(only_active.context['show_annotations'])
        self.assertContains(only_active, 'name="annotation_status" value="active"')

        # Desactivar desde el filtro conserva el filtro al volver.
        url = reverse('app_prospects:agent_annotation_status', args=[self.agent.key, active.pk])
        response = self.client.post(url, {'is_active': '0', 'annotation_page': '1', 'annotation_status': 'active'})
        self.assertRedirects(
            response, self.configuration_url + '?annotation_page=1&annotation_status=active#anotaciones',
            fetch_redirect_response=False,
        )
        empty = self.client.get(self.configuration_url, {'annotation_status': 'active'})
        self.assertContains(empty, 'Este agente no tiene anotaciones activas.')

    def test_config_form_activates_and_deactivates_annotation(self):
        self.add_page_of_newer_annotations()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.get(self.configuration_url).status_code, 200)
        self.assertEqual(client.post(self.status_url, {'is_active': '1'}).status_code, 403)

        for value, expected in [('1', True), ('0', False)]:
            with self.subTest(is_active=value):
                response = client.post(self.status_url, {'is_active': value, 'annotation_page': '2'},
                                       HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value, follow=True)
                self.assertRedirects(response, self.configuration_url + '?annotation_page=2#anotaciones',
                                     fetch_redirect_response=False)
                self.assert_annotations_tab(response, selected=True)
                self.assertEqual(response.context['annotation_page'].number, 2)
                self.assertContains(response, 'name="annotation_page" value="2"')
                self.annotation.refresh_from_db()
                self.assertEqual(self.annotation.is_active, expected)

    def test_activation_at_capacity_requires_condensing_and_preserves_page_and_filter(self):
        active = self.add_active_annotations()

        response = self.client.post(self.status_url, {
            'is_active': '1', 'annotation_page': '2', 'annotation_status': 'active',
        })

        self.assertRedirects(
            response, self.configuration_url + '?annotation_page=2&annotation_status=active#anotaciones',
            fetch_redirect_response=False,
        )
        self.annotation.refresh_from_db()
        self.assertFalse(self.annotation.is_active)
        self.assertEqual(self.agent.learning_annotations.filter(is_active=True).count(), 10)
        self.assertTrue(any('resum' in str(message).lower()
                            for message in get_messages(response.wsgi_request)))

        configuration = self.client.get(self.configuration_url, {'annotation_page': '1'})
        self.assertEqual(configuration.context['active_annotation_count'], 10)
        self.assertEqual(configuration.context['max_active_annotations'], 10)
        self.assertTrue(configuration.context['annotation_capacity_reached'])

        existing_url = self.annotation_url('status', active[0])
        self.assertEqual(self.client.post(existing_url, {'is_active': '1'}).status_code, 302)
        self.assertEqual(self.agent.learning_annotations.filter(is_active=True).count(), 10)

    def test_summarizing_becomes_available_with_two_active_annotations(self):
        LearningAnnotationModel.objects.create(agent=self.agent, suggestion='Primera regla.', is_active=True)

        single = self.client.get(self.configuration_url, {'annotation_page': '1'})

        self.assertFalse(single.context['annotation_can_summarize'])
        self.assertFalse(single.context['annotation_capacity_reached'])
        self.assertNotContains(single, 'data-annotation-condense ')

        LearningAnnotationModel.objects.create(agent=self.agent, suggestion='Segunda regla.', is_active=True)

        multiple = self.client.get(self.configuration_url, {'annotation_page': '1'})

        self.assertEqual(multiple.context['active_annotation_count'], 2)
        self.assertTrue(multiple.context['annotation_can_summarize'])
        self.assertFalse(multiple.context['annotation_capacity_reached'])
        self.assertContains(multiple, 'data-annotation-condense ')

    def test_model_enforces_capacity_for_creation_and_allows_other_agents(self):
        self.add_active_annotations()
        overflow = LearningAnnotationModel(agent=self.agent, suggestion='Regla adicional.', is_active=True)

        with self.assertRaises(ValidationError):
            overflow.full_clean()
        with self.assertRaises(ValidationError):
            overflow.save()

        LearningAnnotationModel.objects.create(agent=self.agent, suggestion='Pendiente.', is_active=False)
        other_agent = AgentModel.objects.create(
            key='other-agent', name='Luis', role='Prospección', description='Otro agente',
        )
        other = LearningAnnotationModel.objects.create(agent=other_agent, suggestion='Regla propia.', is_active=True)
        self.assertTrue(other.is_active)
        self.assertEqual(self.agent.learning_annotations.filter(is_active=True).count(), 10)

    def test_deactivating_at_capacity_frees_space_for_another_annotation(self):
        active = self.add_active_annotations()

        self.client.post(self.annotation_url('status', active[0]), {'is_active': '0'})
        self.client.post(self.status_url, {'is_active': '1'})

        active[0].refresh_from_db()
        self.annotation.refresh_from_db()
        self.assertFalse(active[0].is_active)
        self.assertTrue(self.annotation.is_active)
        self.assertEqual(self.agent.learning_annotations.filter(is_active=True).count(), 10)

    def test_admin_bulk_activation_over_limit_rolls_back_all_changes(self):
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save(update_fields=['is_staff', 'is_superuser'])
        for number in range(9):
            LearningAnnotationModel.objects.create(
                agent=self.agent, suggestion=f'Regla activa {number}.', is_active=True,
            )
        other_pending = LearningAnnotationModel.objects.create(
            agent=self.agent, suggestion='Otra regla pendiente.', is_active=False,
        )
        url = reverse('admin:automations_learningannotationmodel_changelist')

        response = self.client.post(url, {
            '_save': 'Guardar',
            'form-TOTAL_FORMS': '2',
            'form-INITIAL_FORMS': '2',
            'form-MIN_NUM_FORMS': '0',
            'form-MAX_NUM_FORMS': '1000',
            'form-0-id': str(self.annotation.pk),
            'form-0-is_active': 'on',
            'form-1-id': str(other_pending.pk),
            'form-1-is_active': 'on',
        })

        self.assertRedirects(response, url, fetch_redirect_response=False)
        self.assertTrue(any('resum' in str(message).lower()
                            for message in get_messages(response.wsgi_request)))
        self.assertEqual(self.agent.learning_annotations.filter(is_active=True).count(), 9)
        self.annotation.refresh_from_db()
        other_pending.refresh_from_db()
        self.assertFalse(self.annotation.is_active)
        self.assertFalse(other_pending.is_active)

    def test_condense_is_authenticated_post_with_csrf_and_preserves_navigation(self):
        self.add_active_annotations()
        url = reverse('app_prospects:agent_annotations_condense', args=[self.agent.key])
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        client.get(self.configuration_url)
        csrf_token = client.cookies['csrftoken'].value

        with patch('leadmanager.apps.prospects.views.condense_active_annotations', return_value=self.annotation) as condense:
            self.assertEqual(client.get(url).status_code, 405)
            self.assertEqual(client.post(url).status_code, 403)
            response = client.post(url, {'annotation_page': '2', 'annotation_status': 'active'},
                                   HTTP_X_CSRFTOKEN=csrf_token)
            self.assertRedirects(
                response, self.configuration_url + '?annotation_page=2&annotation_status=active#anotaciones',
                fetch_redirect_response=False,
            )
            condense.assert_called_once_with(self.agent.pk)

            condense.reset_mock()
            client.logout()
            client.cookies['csrftoken'] = csrf_token
            self.assertEqual(client.post(url, HTTP_X_CSRFTOKEN=csrf_token).status_code, 302)
            missing_url = reverse('app_prospects:agent_annotations_condense', args=['missing-agent'])
            self.assertEqual(self.client.post(missing_url).status_code, 404)
            condense.assert_not_called()

    def test_failed_condensation_keeps_existing_annotations(self):
        active = self.add_active_annotations()
        original_ids = {annotation.pk for annotation in active}
        url = reverse('app_prospects:agent_annotations_condense', args=[self.agent.key])

        with self.assertLogs('leadmanager.apps.prospects.views', level='ERROR'), patch(
            'leadmanager.apps.prospects.views.condense_active_annotations',
            side_effect=RuntimeError('Fallo simulado del modelo'),
        ):
            response = self.client.post(url, {'annotation_page': '1'})

        self.assertRedirects(response, self.configuration_url + '?annotation_page=1#anotaciones',
                             fetch_redirect_response=False)
        self.assertEqual(set(self.agent.learning_annotations.filter(is_active=True).values_list('pk', flat=True)),
                         original_ids)
        self.assertTrue(any(message.level_tag == 'error' for message in get_messages(response.wsgi_request)))

    def test_rejects_wrong_agent_invalid_state_get_and_anonymous_updates(self):
        other_agent = AgentModel.objects.create(
            key='other-agent', name='Luis', role='Prospección', description='Otro agente',
        )
        wrong_url = reverse('app_prospects:agent_annotation_status', args=[other_agent.key, self.annotation.pk])
        self.assertEqual(self.client.post(wrong_url, {'is_active': '1'}).status_code, 404)
        self.assertEqual(self.client.post(self.status_url, {'is_active': 'invalid'}).status_code, 400)
        self.assertEqual(self.client.get(self.status_url).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(self.status_url, {'is_active': '1'}).status_code, 302)
        self.annotation.refresh_from_db()
        self.assertFalse(self.annotation.is_active)


class AgentManagementTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='agent-manager', password='test')
        self.client.force_login(self.user)

    def prompt_data(self, agent):
        url = reverse('app_automations:agent_prompts', args=[agent.key])
        prompt = self.client.get(url).json()['prompt']
        data = {}
        for key, value in prompt.items():
            if isinstance(value, bool):
                if value:
                    data[key] = 'on'
            elif isinstance(value, list):
                data[key] = json.dumps(value)
            else:
                data[key] = value
        return url, data

    def test_create_configure_activate_and_delete(self):
        response = self.client.post(reverse('app_prospects:agent_create'), {'name': 'Ana'})
        agent = AgentModel.objects.get(name='Ana')
        self.assertFalse(agent.is_active)
        self.assertEqual(agent.mode, AgentModel.ModeEnum.PRODUCTION)
        self.assertEqual(agent.prompt['name'], 'Ana')
        self.assertEqual(
            agent.description,
            'Atiende consultas, identifica necesidades y califica prospectos '
            'para canalizarlos con el especialista adecuado.',
        )
        self.assertRedirects(response, reverse('app_prospects:agent_configuration', args=[agent.key]))
        self.assertContains(self.client.get(reverse('app_prospects:agents')), 'Inactivo')
        self.client.post(reverse('app_prospects:agent_channels', args=[agent.key]), {'channels': ['web']})
        self.client.post(reverse('app_prospects:agent_status', args=[agent.key]), {'is_active': '1'})
        agent.refresh_from_db()
        self.assertTrue(agent.is_active)
        self.client.post(reverse('app_prospects:agent_status', args=[agent.key]), {'is_active': '0'})
        agent.refresh_from_db()
        self.assertFalse(agent.is_active)
        self.client.post(reverse('app_prospects:agent_delete', args=[agent.key]))
        self.assertFalse(AgentModel.objects.filter(pk=agent.pk).exists())

    def test_new_agent_gets_its_own_copy_of_the_base_business_units(self):
        from leadmanager.apps.automations.agent_configuration import sofia_defaults
        from leadmanager.apps.automations.models import AgentBusinessUnitModel

        # Sofía con unidades distintas a las base: el agente nuevo no las toma.
        sofia = AgentModel.objects.create(key='prospector', name='Sofía', role='Prospección', description='Agente')
        AgentBusinessUnitModel.objects.create(agent=sofia, name='SOLO DE SOFIA', description='Editada a mano')

        self.client.post(reverse('app_prospects:agent_create'), {'name': 'Ana'})
        ana = AgentModel.objects.get(name='Ana')

        base = {unit['name']: unit['description'] for unit in sofia_defaults()['business_units']}
        copies = dict(ana.business_units.values_list('name', 'description'))
        self.assertEqual(copies, base)
        self.assertNotIn('SOLO DE SOFIA', copies)
        self.assertEqual(
            sorted(ana.prompt['business_units']),
            sorted(ana.business_units.values_list('pk', flat=True)),
        )

        # Editar la copia no cambia las de Sofía ni las de otros agentes.
        ana.business_units.filter(name='ODOO').update(description='Solo para Ana')
        self.client.post(reverse('app_prospects:agent_create'), {'name': 'Luis'})
        luis = AgentModel.objects.get(name='Luis')
        self.assertEqual(luis.business_units.get(name='ODOO').description, base['ODOO'])
        self.assertFalse(sofia.business_units.filter(name='ODOO').exists())

    def test_invalid_name_and_builtin_deletion(self):
        self.assertEqual(self.client.post(reverse('app_prospects:agent_create'), {'name': '  '}).status_code, 400)
        agent = AgentModel.objects.create(key='prospector', name='Sofía', role='Prospección', description='Agente')
        self.assertEqual(self.client.post(reverse('app_prospects:agent_delete', args=[agent.key])).status_code, 403)
        self.assertTrue(AgentModel.objects.filter(pk=agent.pk).exists())

    def test_filter_agents_by_name_and_category(self):
        AgentModel.objects.create(key='sales', name='Ana', role='Prospección', description='Agente')
        AgentModel.objects.create(key='analysis', name='María', role='Análisis', description='Agente', category=AgentModel.CategoryEnum.ANALYSIS)
        url = reverse('app_prospects:agents')
        response = self.client.get(url, {'category': AgentModel.CategoryEnum.ANALYSIS})
        self.assertEqual([agent.name for agent in response.context['agents']], ['María'])
        response = self.client.get(url, {'search': 'ana', 'category': AgentModel.CategoryEnum.PROSPECTING})
        self.assertEqual([agent.name for agent in response.context['agents']], ['Ana'])
        response = self.client.get(url, {'search': 'nadie'})
        self.assertContains(response, 'No se encontraron agentes')

    def test_save_new_agent_name_with_learning_disabled(self):
        self.client.post(reverse('app_prospects:agent_create'), {'name': 'Ana'})
        agent = AgentModel.objects.get(name='Ana')
        agent.mode = AgentModel.ModeEnum.SHADOW_TESTING
        agent.save(update_fields=['mode'])
        url, data = self.prompt_data(agent)
        data['name'] = 'Andrea'
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200, response.content.decode())
        agent.refresh_from_db()
        self.assertEqual(agent.name, 'Andrea')
        self.assertEqual(agent.mode, AgentModel.ModeEnum.SHADOW_TESTING)
        self.assertFalse(agent.learning_enabled)

        data.pop('mode')
        self.assertEqual(self.client.post(url, data).status_code, 200)
        agent.refresh_from_db()
        self.assertEqual(agent.mode, AgentModel.ModeEnum.SHADOW_TESTING)

        data['mode'] = 'unknown'
        data['name'] = 'No guardar'
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 400)
        self.assertIn('mode', response.json()['errors'])
        agent.refresh_from_db()
        self.assertEqual(agent.name, 'Andrea')

        data['name'] = 'Andrea'
        data['mode'] = AgentModel.ModeEnum.PRODUCTION
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['prompt']['mode'], AgentModel.ModeEnum.PRODUCTION)
        agent.refresh_from_db()
        self.assertEqual(agent.mode, AgentModel.ModeEnum.PRODUCTION)
        self.assertNotIn('mode', agent.prompt)

    def test_shadow_mode_blocks_production_and_analysis_agents(self):
        cases = [
            ('prospector', False, AgentModel.CategoryEnum.PROSPECTING),
            ('active-production', True, AgentModel.CategoryEnum.PROSPECTING),
            ('analysis', False, AgentModel.CategoryEnum.ANALYSIS),
        ]
        for key, active, category in cases:
            with self.subTest(key=key):
                self.client.post(reverse('app_prospects:agent_create'), {'name': key})
                agent = AgentModel.objects.get(name=key)
                agent.key, agent.is_active, agent.category = key, active, category
                agent.save(update_fields=['key', 'is_active', 'category'])
                url, data = self.prompt_data(agent)
                data.update(mode=AgentModel.ModeEnum.SHADOW_TESTING, name='No guardar')
                response = self.client.post(url, data)
                self.assertEqual(response.status_code, 400)
                self.assertIn('mode', response.json()['errors'])
                agent.refresh_from_db()
                self.assertEqual(agent.mode, AgentModel.ModeEnum.PRODUCTION)
                self.assertEqual(agent.name, key)
                page = self.client.get(reverse('app_prospects:agent_configuration', args=[key]))
                self.assertNotContains(page, 'id="agentModeToggle"')
                self.assertNotContains(page, 'aria-labelledby="agent-mode-heading"')

    def test_inactive_prospecting_agent_can_select_shadow_mode(self):
        self.client.post(reverse('app_prospects:agent_create'), {'name': 'Prueba'})
        agent = AgentModel.objects.get(name='Prueba')
        url, data = self.prompt_data(agent)
        data['mode'] = AgentModel.ModeEnum.SHADOW_TESTING
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200, response.content.decode())
        agent.refresh_from_db()
        self.assertEqual(agent.mode, AgentModel.ModeEnum.SHADOW_TESTING)

    def test_admin_rejects_shadow_for_new_analysis_category(self):
        from leadmanager.apps.automations.admin import AgentAdminForm
        self.client.post(reverse('app_prospects:agent_create'), {'name': 'Prueba'})
        agent = AgentModel.objects.get(name='Prueba')
        form = AgentAdminForm(data={
            'key': agent.key, 'category': AgentModel.CategoryEnum.ANALYSIS,
            'mode': AgentModel.ModeEnum.SHADOW_TESTING,
        }, instance=agent)
        self.assertFalse(form.is_valid())
        self.assertIn('mode', form.errors)

    def test_mutations_require_login(self):
        self.client.logout()
        self.assertEqual(self.client.post(reverse('app_prospects:agent_create'), {'name': 'Ana'}).status_code, 302)
        self.assertFalse(AgentModel.objects.filter(name='Ana').exists())

    def test_channel_conflicts_and_release(self):
        first = AgentModel.objects.create(key='first', name='Uno', role='Prospección', description='Agente', is_active=True)
        second = AgentModel.objects.create(key='second', name='Dos', role='Prospección', description='Agente', is_active=False)
        channels = lambda agent: reverse('app_prospects:agent_channels', args=[agent.key])
        status = lambda agent: reverse('app_prospects:agent_status', args=[agent.key])
        self.assertEqual(self.client.post(channels(first), {'channels': ['web', 'email']}).status_code, 302)
        self.assertEqual(self.client.post(channels(second), {'channels': ['web', 'form']}).status_code, 302)
        response = self.client.post(status(second), {'is_active': '1'}, follow=True)
        self.assertContains(response, 'chatweb')
        self.assertContains(response, 'Asignado a Uno')
        second.refresh_from_db()
        self.assertFalse(second.is_active)
        self.assertFalse(second.channel_assignments.filter(active=True).exists())
        self.client.post(status(first), {'is_active': '0'})
        self.client.post(status(second), {'is_active': '1'})
        second.refresh_from_db()
        self.assertTrue(second.is_active)
        self.assertEqual(set(second.channel_assignments.filter(active=True).values_list('channel', flat=True)), {'web', 'form'})
        response = self.client.post(channels(second), {'channels': ['form', 'email']})
        self.assertEqual(response.status_code, 302)
        self.client.post(status(first), {'is_active': '1'})
        first.refresh_from_db()
        self.assertFalse(first.is_active)
        self.assertEqual(set(first.channel_assignments.values_list('channel', flat=True)), {'web', 'email'})

    def test_active_channel_database_constraint(self):
        first = AgentModel.objects.create(key='first', name='Uno', role='Prospección', description='Agente')
        second = AgentModel.objects.create(key='second', name='Dos', role='Prospección', description='Agente')
        AgentChannelAssignment.objects.create(agent=first, channel='web', active=True)
        with self.assertRaises(IntegrityError), transaction.atomic():
            AgentChannelAssignment.objects.create(agent=second, channel='web', active=True)

    def test_activation_conflict_lookup_filters_for_prospecting(self):
        analyzer = AgentModel.objects.create(key='analysis', name='María', role='Análisis', description='Agente', category=AgentModel.CategoryEnum.ANALYSIS)
        prospect = AgentModel.objects.create(key='prospect', name='Ana', role='Prospección', description='Agente', is_active=False)
        AgentChannelAssignment.objects.create(agent=analyzer, channel='web', active=True)
        self.assertEqual(_conflicts(prospect.pk, ['web'], prospecting_only=True), [])
        self.assertEqual(_conflicts(prospect.pk, ['web'])[0]['agent'], 'María')

    def test_edit_channels_shows_saved_selection(self):
        agent = AgentModel.objects.create(key='first', name='Uno', role='Prospección', description='Agente')
        AgentChannelAssignment.objects.create(agent=agent, channel='web', active=True)
        AgentChannelAssignment.objects.create(agent=agent, channel='email', active=True)
        response = self.client.get(reverse('app_prospects:agent_channels', args=[agent.key]))

        class Inputs(HTMLParser):
            def __init__(self):
                super().__init__()
                self.checked = set()

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == 'input' and attrs.get('name') == 'channels' and 'checked' in attrs:
                    self.checked.add(attrs['value'])

        parser = Inputs()
        parser.feed(response.content.decode())
        self.assertEqual(parser.checked, {'web', 'email'})

    def test_cannot_activate_without_channels_or_remove_all_from_active_agent(self):
        agent = AgentModel.objects.create(key='first', name='Uno', role='Prospección', description='Agente', is_active=False)
        response = self.client.post(reverse('app_prospects:agent_status', args=[agent.key]), {'is_active': '1'}, follow=True)
        self.assertContains(response, 'Selecciona al menos un canal')
        agent.refresh_from_db()
        self.assertFalse(agent.is_active)
        self.client.post(reverse('app_prospects:agent_channels', args=[agent.key]), {'channels': ['web']})
        self.client.post(reverse('app_prospects:agent_status', args=[agent.key]), {'is_active': '1'})
        response = self.client.post(reverse('app_prospects:agent_channels', args=[agent.key]), {})
        self.assertContains(response, 'Selecciona al menos un canal', status_code=400)
        self.assertTrue(agent.channel_assignments.filter(channel='web', active=True).exists())

    def test_agent_avatar_upload_preview_and_removal(self):
        agent = AgentModel.objects.create(key='first', name='Uno', role='Prospección', description='Agente', avatar='imgs/prospector.png')
        url = reverse('app_prospects:agent_avatar', args=[agent.key])
        with TemporaryDirectory() as directory, self.settings(MEDIA_ROOT=directory):
            image = SimpleUploadedFile('avatar.png', b'\x89PNG\r\n\x1a\nexample', content_type='image/png')
            self.client.post(url, {'action': 'upload', 'avatar': image})
            agent.refresh_from_db()
            self.assertTrue(agent.avatar.startswith('agent-avatars/'))
            response = self.client.get(reverse('app_prospects:agent_avatar_file', args=[agent.key]))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['Content-Type'], 'image/png')
            close_file_response(response)
            self.assertContains(self.client.get(reverse('app_prospects:agents')), agent.avatar_url)
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(url, {'action': 'remove'})
            agent.refresh_from_db()
            self.assertEqual(agent.avatar, '')
            self.assertEqual(self.client.get(reverse('app_prospects:agent_avatar_file', args=[agent.key])).status_code, 404)

    def test_agent_avatar_rejects_unsupported_file(self):
        agent = AgentModel.objects.create(key='first', name='Uno', role='Prospección', description='Agente')
        upload = SimpleUploadedFile('avatar.png', b'not an image', content_type='image/png')
        self.client.post(reverse('app_prospects:agent_avatar', args=[agent.key]), {'action': 'upload', 'avatar': upload})
        agent.refresh_from_db()
        self.assertEqual(agent.avatar, '')

    def test_widget_avatar_is_public_and_uses_active_web_assignment(self):
        agent = AgentModel.objects.create(key='first', name='Uno', role='Prospección', description='Agente', avatar='imgs/analizer.jpg')
        assignment = AgentChannelAssignment.objects.create(agent=agent, channel='web', active=True)
        self.client.logout()
        url = reverse('app_pages:widget_avatar')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/svg+xml')
        self.assertEqual(response['Cache-Control'], 'no-store')
        assignment.active = False
        assignment.save(update_fields=['active'])
        response = self.client.get(url)
        self.assertEqual(response['Content-Type'], 'image/svg+xml')

    def test_widget_avatar_serves_uploaded_image_without_login(self):
        agent = AgentModel.objects.create(key='first', name='Uno', role='Prospección', description='Agente')
        AgentChannelAssignment.objects.create(agent=agent, channel='web', active=True)
        with TemporaryDirectory() as directory, self.settings(MEDIA_ROOT=directory):
            image = SimpleUploadedFile('avatar.png', b'\x89PNG\r\n\x1a\nexample', content_type='image/png')
            self.client.post(reverse('app_prospects:agent_avatar', args=[agent.key]), {'action': 'upload', 'avatar': image})
            self.client.logout()
            response = self.client.get(reverse('app_pages:widget_avatar'))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['Content-Type'], 'image/png')
            self.assertEqual(response['Cache-Control'], 'no-store')
            close_file_response(response)

    @patch('leadmanager.apps.pages.views.get_agent_configuration')
    def test_chat_displays_active_web_agent_name(self, configuration):
        configuration.return_value = ({'name': 'Sofía', 'enterprise_name': 'Empresa', 'saludo': 'Hola, soy [bot]'}, [])
        agent = AgentModel.objects.create(key='first', name='Ana', role='Prospección', description='Agente')
        AgentChannelAssignment.objects.create(agent=agent, channel='web', active=True)
        response = self.client.get(reverse('app_pages:webchat'))
        self.assertContains(response, '<div class="agent-name">Ana</div>')
        self.assertContains(response, 'Hola, soy Ana')

        configuration.return_value[0]['saludo'] = 'Hola, soy Sofía, asistente virtual de Estrasol.'
        response = self.client.get(reverse('app_pages:webchat'))
        self.assertContains(response, 'Hola, soy Ana, asistente virtual de Estrasol.')

    @patch('leadmanager.apps.pages.views.get_agent_configuration')
    def test_chat_and_widget_ignore_analysis_agent(self, configuration):
        configuration.return_value = ({'name': 'Sofía', 'enterprise_name': 'Empresa', 'saludo': 'Hola, soy [bot]'}, [])
        agent = AgentModel.objects.create(key='analysis', name='María', role='Análisis', description='Agente', category=AgentModel.CategoryEnum.ANALYSIS)
        AgentChannelAssignment.objects.create(agent=agent, channel='web', active=True)
        self.assertContains(self.client.get(reverse('app_pages:webchat')), '<div class="agent-name">Sofía</div>')
        self.assertEqual(self.client.get(reverse('app_pages:widget_avatar'))['Content-Type'], 'image/svg+xml')

    def test_reminder_uses_agent_name_for_each_active_channel(self):
        AgentModel.objects.create(key='prospector', name='Sofía', role='Prospección', description='Agente')
        agent = AgentModel.objects.create(key='first', name='Ana', role='Prospección', description='Agente')
        AgentChannelAssignment.objects.create(agent=agent, channel='web', active=True)
        names, fallback = reminder_agent_names()
        self.assertIn('soy Ana.', reminder_email_content(names['web'])[1])
        self.assertIn('soy Sofía.', reminder_email_content(names.get('email', fallback))[1])


class AgentDuplicateTests(TestCase):
    """Copiar un agente: la copia es independiente, inactiva y en producción."""

    def setUp(self):
        from leadmanager.apps.automations.agent_configuration import default_prompt_config
        from leadmanager.apps.automations.models import AgentBusinessUnitModel

        self.client.force_login(get_user_model().objects.create_user(username='copia', password='test'))
        self.original = AgentModel.objects.create(
            key='agent-original', name='Valeria', role='Prospección', description='Agente original',
            category=AgentModel.CategoryEnum.PROSPECTING, is_active=True,
            learning_enabled=True, prompt={**default_prompt_config(), 'name': 'Valeria', 'saludo': 'Hola único'},
        )
        AgentBusinessUnitModel.objects.create(agent=self.original, name='ODOO', description='ERP de Valeria')
        AgentChannelAssignment.objects.create(agent=self.original, channel='web', active=True)
        AgentChannelAssignment.objects.create(agent=self.original, channel='email', active=True)
        self.url = reverse('app_prospects:agent_duplicate', args=[self.original.key])

    def duplicate(self):
        response = self.client.post(self.url)
        copy = AgentModel.objects.exclude(pk=self.original.pk).get(name='Copia de Valeria')
        self.assertRedirects(response, reverse('app_prospects:agent_configuration', args=[copy.key]),
                             fetch_redirect_response=False)
        return copy

    def test_copy_is_inactive_production_and_independent(self):
        copy = self.duplicate()
        self.original.refresh_from_db()

        self.assertFalse(copy.is_active)
        self.assertEqual(copy.mode, AgentModel.ModeEnum.PRODUCTION)
        self.assertNotEqual(copy.key, self.original.key)
        self.assertEqual(copy.prompt['name'], 'Copia de Valeria')
        self.assertEqual(copy.prompt['saludo'], 'Hola único')
        self.assertTrue(copy.learning_enabled)
        self.assertEqual((copy.role, copy.description, copy.category),
                         (self.original.role, self.original.description, self.original.category))

        # Unidades: mismas, pero propias.
        self.assertEqual(
            dict(copy.business_units.values_list('name', 'description')),
            dict(self.original.business_units.values_list('name', 'description')),
        )
        self.assertFalse(set(copy.business_units.values_list('pk', flat=True))
                         & set(self.original.business_units.values_list('pk', flat=True)))
        self.assertEqual(sorted(copy.prompt['business_units']),
                         sorted(copy.business_units.values_list('pk', flat=True)))
        copy.business_units.filter(name='ODOO').update(description='Cambiada en la copia')
        self.assertEqual(self.original.business_units.get(name='ODOO').description, 'ERP de Valeria')

        # Canales: seleccionados pero sin reservar; el original sigue igual.
        self.assertEqual(
            sorted(copy.channel_assignments.values_list('channel', 'active')),
            [('email', False), ('web', False)],
        )
        self.assertEqual(self.original.channel_assignments.filter(active=True).count(), 2)
        self.assertTrue(self.original.is_active)
        self.assertEqual(self.original.prompt['name'], 'Valeria')

    def test_copy_of_a_test_agent_is_not_in_test_mode(self):
        self.original.mode = AgentModel.ModeEnum.SHADOW_TESTING
        self.original.is_active = False
        self.original.save(update_fields=['mode', 'is_active'])

        copy = self.duplicate()

        self.assertEqual(copy.mode, AgentModel.ModeEnum.PRODUCTION)
        self.assertFalse(copy.is_active)

    def test_copy_gets_its_own_avatar_file(self):
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        with TemporaryDirectory() as directory, self.settings(MEDIA_ROOT=directory):
            self.original.avatar = default_storage.save(
                'agent-avatars/' + 'a' * 32 + '.png', ContentFile(b'\x89PNG\r\n\x1a\nimagen'),
            )
            self.original.save(update_fields=['avatar'])

            copy = self.duplicate()

            self.assertTrue(copy.avatar.startswith('agent-avatars/'))
            self.assertNotEqual(copy.avatar, self.original.avatar)
            with default_storage.open(copy.avatar, 'rb') as image:
                self.assertEqual(image.read(), b'\x89PNG\r\n\x1a\nimagen')

    def test_analysis_agents_cannot_be_copied_and_get_is_not_allowed(self):
        analyzer = AgentModel.objects.create(
            key='conversation_analyzer', name='María', role='Análisis', description='Analiza',
            category=AgentModel.CategoryEnum.ANALYSIS,
        )
        url = reverse('app_prospects:agent_duplicate', args=[analyzer.key])
        self.assertEqual(self.client.post(url).status_code, 404)
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assertEqual(AgentModel.objects.count(), 2)

    def test_copy_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.post(self.url).status_code, 302)
        self.assertFalse(AgentModel.objects.filter(name='Copia de Valeria').exists())

    def test_agents_page_shows_copy_button_only_for_prospecting(self):
        AgentModel.objects.create(
            key='conversation_analyzer', name='María', role='Análisis', description='Analiza',
            category=AgentModel.CategoryEnum.ANALYSIS,
        )
        page = self.client.get(reverse('app_prospects:agents'))
        self.assertContains(page, self.url)
        self.assertNotContains(page, reverse('app_prospects:agent_duplicate', args=['conversation_analyzer']))

    def test_test_agents_have_conversations_button_to_test_side(self):
        self.original.mode = AgentModel.ModeEnum.SHADOW_TESTING
        self.original.is_active = False
        self.original.save(update_fields=['mode', 'is_active'])
        production = AgentModel.objects.create(
            key='agent-real', name='Real', role='Prospección', description='Agente',
            category=AgentModel.CategoryEnum.PROSPECTING,
        )

        page = self.client.get(reverse('app_prospects:agents'))

        test_link = reverse('app_prospects:conversations') + '?mode=shadow_testing'
        self.assertContains(page, f'href="{test_link}"', count=1)
        self.assertNotContains(page, f'{reverse("app_prospects:agent_configuration", args=[production.key])}?mode')


class AgentAnalysesPageTests(TestCase):
    """La página de Análisis muestra id, fecha en 12 horas y filtra por activas."""

    def setUp(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        self.client.force_login(get_user_model().objects.create_user(username='analisis'))
        self.agent = AgentModel.objects.create(
            key='conversation_analyzer', name='María', role='Análisis', description='Analiza',
            category=AgentModel.CategoryEnum.ANALYSIS,
        )
        self.inactive = LearningAnnotationModel.objects.create(agent=self.agent, suggestion='Pendiente.', is_active=False)
        self.active = LearningAnnotationModel.objects.create(agent=self.agent, suggestion='Vigente.', is_active=True)
        LearningAnnotationModel.objects.filter(pk=self.active.pk).update(
            created_at=datetime(2026, 11, 10, 15, 5, tzinfo=ZoneInfo('America/Mexico_City')),
        )
        self.url = reverse('app_prospects:agent_analyses', args=[self.agent.key])

    def test_shows_id_and_twelve_hour_date(self):
        response = self.client.get(self.url)
        self.assertContains(response, str(self.active.pk)[:8])
        self.assertContains(response, '10/11/2026 3:05')
        self.assertNotContains(response, '10/11/2026 15:05')

    def test_filters_all_or_only_active(self):
        everything = self.client.get(self.url)
        self.assertEqual({item.pk for item in everything.context['page_obj']}, {self.active.pk, self.inactive.pk})

        only_active = self.client.get(self.url, {'annotation_status': 'active'})
        self.assertEqual([item.pk for item in only_active.context['page_obj']], [self.active.pk])
        self.assertEqual(only_active.context['annotation_status'], 'active')
