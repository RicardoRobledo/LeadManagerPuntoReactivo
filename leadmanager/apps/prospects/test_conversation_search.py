"""Búsqueda por correo en el listado de conversaciones reales y de pruebas."""

import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from leadmanager.apps.automations.models import (
    AgentModel, TestConversationModel, TestMessageModel,
)

from .models import ChatHistoryModel, ConversationModel, ProspectModel


class ConversationSearchTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        clock = patch('leadmanager.apps.prospects.date_ranges.timezone.now', return_value=self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.client.force_login(get_user_model().objects.create_user(username='busqueda'))
        self.url = reverse('app_prospects:conversations')
        self.email = 'ana@example.test'

    def conversation(self, *, source='web', session_id=None, prospect=None, color='amarillo'):
        row = ConversationModel.objects.create(
            source=source,
            session_id=session_id or f'sess_{ConversationModel.objects.count()}',
            prospect=prospect,
        )
        self.message(row, 'assistant', {
            'reply_to_customer': '¿En qué podemos ayudarte?',
            'crm_update': {'semaforo': color},
        })
        return row

    def message(self, conversation, role, content):
        return ChatHistoryModel.objects.create(
            conversation=conversation, message={'role': role, 'content': content},
        )

    def assert_results(self, expected, **params):
        response = self.client.get(self.url, {'search_email': self.email, **params})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {row.pk for row in response.context['page_obj']},
            {row.pk for row in expected},
        )
        self.assertEqual(response.context['conversations_count'], len(expected))
        return response

    def test_correo_de_sesion_sin_prospecto(self):
        for source in ('email', 'form'):
            with self.subTest(source=source):
                email = f'{source}@example.test'
                row = self.conversation(source=source, session_id=email)
                self.assert_results([row], search_email=email)

    def test_correo_de_sesion_con_prospecto_incorrecto(self):
        wrong = ProspectModel.objects.create(name='Prueba anterior', email='prueba@example.test')
        row = self.conversation(source='form', session_id=self.email, prospect=wrong)
        self.assert_results([row])

    def test_correo_del_prospecto_sin_coincidencia_en_mensajes(self):
        prospect = ProspectModel.objects.create(name='Ana', email=self.email)
        row = self.conversation(prospect=prospect)
        self.conversation()
        self.assert_results([row])

    def test_ignora_mayusculas_y_espacios_exteriores(self):
        row = self.conversation()
        self.message(row, 'user', f'correo:{self.email}')
        response = self.assert_results([row], search_email=f'  {self.email.upper()}  ')
        self.assertEqual(response.context['search_prospect'], self.email.upper())
        self.assertContains(response, 'Los correos también se buscan en los mensajes.')
        self.assertContains(response, 'La búsqueda respeta el periodo y los demás filtros seleccionados.')

    def test_nombre_y_telefono_no_se_buscan_en_el_historial(self):
        prospect = ProspectModel.objects.create(name='Ana Torres', phone='5551234567')
        linked = self.conversation(prospect=prospect)
        mentioned = self.conversation()
        self.message(mentioned, 'user', 'Hablé con Ana Torres, teléfono 5551234567')
        for term in ('Torres', '5551234567'):
            with self.subTest(term=term):
                self.assert_results([linked], search_email=term)

    def test_correo_web_en_texto_o_crm_serializado_u_objeto(self):
        for index, content in enumerate((
            f'correo:{self.email}, mensaje:Quiero información',
            {'crm_update': {'semaforo': 'amarillo', 'contacto': {'correo': self.email}}},
            json.dumps({'crm_update': {
                'semaforo': 'amarillo', 'contacto': {'correo': self.email},
            }}),
        )):
            with self.subTest(format=index):
                row = self.conversation()
                self.message(row, 'user' if index == 0 else 'assistant', content)
        self.assert_results(list(ConversationModel.objects.all()))

    def test_varios_mensajes_coincidentes_no_duplican_filas_ni_conteos(self):
        row = self.conversation()
        self.message(row, 'user', f'correo:{self.email}')
        self.message(row, 'user', f'Confirmo mi correo: {self.email}')
        self.conversation()
        response = self.assert_results([row])
        self.assertEqual(response.context['page_obj'].paginator.count, 1)
        self.assertEqual(response.context['in_progress_count'], 1)

    def test_correo_y_busqueda_de_mensaje_pueden_coincidir_en_mensajes_distintos(self):
        row = self.conversation()
        self.message(row, 'user', f'correo:{self.email}')
        self.message(row, 'user', 'Necesito cotización de kioscos')
        self.assert_results([row], search_message='kioscos')
        self.assert_results([], search_message='inexistente')

    def test_busqueda_respeta_periodo_y_permite_ampliarlo(self):
        row = self.conversation(source='form', session_id=self.email)
        earlier = self.now - timedelta(days=3)
        ConversationModel.objects.filter(pk=row.pk).update(created_at=earlier)
        self.assert_results([])
        self.assert_results(
            [row], interval='custom',
            date_from=timezone.localtime(earlier).date().isoformat(),
            date_to=timezone.localtime(self.now).date().isoformat(),
        )

    def test_busqueda_respeta_canal_y_semaforo(self):
        row = self.conversation(source='web', color='amarillo')
        self.message(row, 'user', f'correo:{self.email}')
        self.assert_results([row], source='web', semaforo='amarillo')
        self.assert_results([], source='form')
        self.assert_results([], semaforo='verde')

    def test_conversacion_sin_evaluacion_sigue_excluida(self):
        ConversationModel.objects.create(source='form', session_id=self.email)
        self.assert_results([])

    def test_historial_de_pruebas_busca_sin_mezclar_mensajes_reales(self):
        real = self.conversation()
        self.message(real, 'user', 'correo:real@example.test')
        agent = AgentModel.objects.create(
            key='busqueda-shadow', name='Pruebas', role='Asistente',
            description='Prueba de búsqueda', mode=AgentModel.ModeEnum.SHADOW_TESTING,
        )
        shadow = TestConversationModel.objects.create(agent=agent, conversation=real)
        for message in (
            {'role': 'user', 'content': f'correo:{self.email}'},
            {'role': 'assistant', 'content': {
                'reply_to_customer': 'Respuesta de prueba',
                'crm_update': {'semaforo': 'amarillo'},
            }},
        ):
            TestMessageModel.objects.create(test_conversation=shadow, message=message)

        self.assert_results([shadow], mode='shadow_testing')
        self.assert_results([], mode='production')
        self.assert_results([], mode='shadow_testing', search_email='real@example.test')
        self.assert_results([real], mode='production', search_email='real@example.test')
