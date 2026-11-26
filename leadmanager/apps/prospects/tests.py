import json

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    ProspectModel,
    ConversationModel,
    ConversationEscalationModel,
    ChatHistoryModel,
    SellerModel,
)


__author__ = 'Ricardo'
__version__ = '0.1'


class ConversationsViewTests(TestCase):
    """
    La tabla lista CONVERSACIONES. El estado (open/closed) de cada una
    viene del campo ``ConversationModel.state``.
    """

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='tester',
            password='test-pass-123',
        )
        self.client.force_login(self.user)

        self.url = reverse('app_prospects:conversations')

        # timezone.now() siempre cae dentro del rango de "hoy", así que
        # sirve como fecha dentro del intervalo por defecto.
        self.now = timezone.now()

        self.lead_counter = 0

    # ==========================================
    # AYUDANTES
    # ==========================================

    def create_prospect(self, name='Ana', phone='5551234567', email=None):
        self.lead_counter += 1

        return ProspectModel.objects.create(
            name=name,
            phone=phone,
            email=email,
            person_id=self.lead_counter,
        )

    def create_conversation(self, session_id, source='whatsapp',
                            prospect=None, state='open', created_at=None):
        conversation = ConversationModel.objects.create(
            session_id=session_id,
            source=source,
            state=state,
            prospect=prospect or self.create_prospect(),
        )

        # created_at usa auto_now_add, así que no acepta un valor al
        # crear: se fija después con update(), que se salta el campo.
        self.set_created_at(
            ConversationModel,
            conversation.pk,
            created_at or self.now,
        )

        conversation.refresh_from_db()
        return conversation

    def create_message(self, conversation, role='user', content=''):
        return ChatHistoryModel.objects.create(
            conversation=conversation,
            message={'role': role, 'content': content},
        )

    def set_created_at(self, model, pk, value):
        model.objects.filter(pk=pk).update(created_at=value)

    def get_rows(self, **params):
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, 200)
        return response, list(response.context['page_obj'])

    # ==========================================
    # LISTADO Y FILTRO DE ESTADO
    # ==========================================

    def test_all_lista_una_fila_por_conversacion(self):
        self.create_conversation('sesion-a')
        self.create_conversation('sesion-b')

        _, rows = self.get_rows(state='all')

        self.assertEqual(len(rows), 2)

        for row in rows:
            self.assertIsInstance(row, ConversationModel)

    def test_filtro_por_estado_open(self):
        self.create_conversation('abierta', state='open')
        self.create_conversation('cerrada', state='closed')

        _, rows = self.get_rows(state='open')

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].session_id, 'abierta')

    def test_filtro_por_estado_closed(self):
        self.create_conversation('abierta', state='open')
        self.create_conversation('cerrada', state='closed')

        _, rows = self.get_rows(state='closed')

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].session_id, 'cerrada')

    def test_state_invalido_regresa_a_all(self):
        self.create_conversation('sesion-a')

        response, rows = self.get_rows(state='cualquier-cosa')

        self.assertEqual(response.context['state'], 'all')
        self.assertEqual(len(rows), 1)
        self.assertIsInstance(rows[0], ConversationModel)

    def test_ordena_del_mas_reciente_al_mas_antiguo(self):
        older = self.create_conversation(
            'older',
            created_at=self.now - timedelta(hours=5),
        )
        newer = self.create_conversation(
            'newer',
            created_at=self.now - timedelta(hours=1),
        )

        _, rows = self.get_rows()

        self.assertEqual([row.pk for row in rows], [newer.pk, older.pk])

    def test_filtra_por_origen(self):
        self.create_conversation('sesion-wa', source='whatsapp')
        self.create_conversation('sesion-web', source='web')

        _, rows = self.get_rows(source='web')

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].session_id, 'sesion-web')

    def test_busca_por_nombre_del_prospecto(self):
        ana = self.create_prospect(name='Ana Torres', phone='5550000001')
        luis = self.create_prospect(name='Luis Pérez', phone='5550000002')

        self.create_conversation('sesion-ana', prospect=ana)
        self.create_conversation('sesion-luis', prospect=luis)

        _, rows = self.get_rows(search_email='Torres')

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].session_id, 'sesion-ana')

    def test_busca_por_email(self):
        prospect = self.create_prospect(
            name='Ana Torres',
            phone='5550000001',
            email='ana@estrasol.mx',
        )
        self.create_conversation('sesion-a', prospect=prospect)

        _, rows = self.get_rows(search_email='ana@estrasol.mx')

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].session_id, 'sesion-a')

    def test_busca_por_contenido(self):
        conversation = self.create_conversation('sesion-a')
        self.create_message(
            conversation,
            role='user',
            content='quiero una cotización',
        )

        _, rows = self.get_rows(search_message='cotización')

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].session_id, 'sesion-a')

    # ==========================================
    # CONTADORES
    # ==========================================

    def test_contadores(self):
        self.create_conversation('abierta-1', state='open')
        self.create_conversation('abierta-2', state='open')
        self.create_conversation('cerrada-1', state='closed')

        response, _ = self.get_rows()

        self.assertEqual(response.context['conversations_count'], 3)
        self.assertEqual(response.context['open_count'], 2)
        self.assertEqual(response.context['closed_count'], 1)

    def test_contadores_no_dependen_del_filtro(self):
        self.create_conversation('abierta', state='open')
        self.create_conversation('cerrada', state='closed')

        for state in ('all', 'open', 'closed'):
            with self.subTest(state=state):
                response = self.client.get(self.url, {'state': state})

                self.assertEqual(
                    response.context['conversations_count'],
                    2,
                )
                self.assertEqual(
                    response.context['open_count'],
                    1,
                )
                self.assertEqual(
                    response.context['closed_count'],
                    1,
                )

    # ==========================================
    # ESTADO, CONTACTO Y ÚLTIMO MENSAJE
    # ==========================================

    def test_renderiza_el_estado_open_y_closed(self):
        self.create_conversation('abierta', state='open')
        self.create_conversation('cerrada', state='closed')

        response = self.client.get(self.url)

        self.assertContains(response, 'Closed')
        self.assertContains(response, 'Open')

    def test_contacto_cae_al_prospecto(self):
        prospect = self.create_prospect(
            name='Julio Contreras',
            phone='5214941061934',
        )
        self.create_conversation('sesion-a', prospect=prospect)

        _, rows = self.get_rows()

        self.assertEqual(rows[0].contact_name, 'Julio Contreras')
        self.assertEqual(rows[0].contact_phone, '5214941061934')

    def test_contacto_desde_el_ultimo_mensaje(self):
        conversation = self.create_conversation('sesion-a')

        self.create_message(
            conversation,
            role='assistant',
            content=json.dumps({
                'reply_to_customer': 'Hola',
                'crm_update': {
                    'contacto': {
                        'nombre': 'Alicia de la Torre',
                        'telefono': '5213333333',
                    },
                },
            }),
        )

        _, rows = self.get_rows()

        self.assertEqual(rows[0].contact_name, 'Alicia de la Torre')
        self.assertEqual(rows[0].contact_phone, '5213333333')

    def test_ultimo_mensaje_y_role(self):
        conversation = self.create_conversation('sesion-a')

        self.create_message(
            conversation,
            role='assistant',
            content='Hola, ¿en qué te ayudo?',
        )

        _, rows = self.get_rows()

        self.assertEqual(rows[0].last_role, 'assistant')
        self.assertEqual(rows[0].last_message, 'Hola, ¿en qué te ayudo?')

    # ==========================================
    # AUTENTICACIÓN
    # ==========================================

    def test_requiere_login(self):
        self.client.logout()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)


class ConversationDetailViewTests(TestCase):
    """
    El modal debe mostrar el conversation_content del escalamiento
    pedido, sin mezclarlo con datos de otro momento de la sesión.
    """

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='tester',
            password='test-pass-123',
        )
        self.client.force_login(self.user)

        self.prospect = ProspectModel.objects.create(
            name='Julio Contreras',
            phone='5214941061934',
            person_id=1,
        )

        self.conversation = ConversationModel.objects.create(
            session_id='5214941061934',
            source='whatsapp',
            prospect=self.prospect,
        )

        # Dos escalamientos de la misma sesión con datos distintos.
        self.first = ConversationEscalationModel.objects.create(
            session=self.conversation,
            conversation_content={
                'crm_update': {
                    'score': 10,
                    'unidad_negocio_detectada': 'Primera unidad',
                },
            },
        )

        self.second = ConversationEscalationModel.objects.create(
            session=self.conversation,
            conversation_content={
                'crm_update': {
                    'score': 55,
                    'unidad_negocio_detectada': 'Segunda unidad',
                },
            },
        )

        now = timezone.now()

        ConversationEscalationModel.objects.filter(pk=self.first.pk).update(
            created_at=now - timedelta(hours=2),
        )
        ConversationEscalationModel.objects.filter(pk=self.second.pk).update(
            created_at=now,
        )

    def detail_url(self, escalation=None):
        url = reverse(
            'app_prospects:conversation',
            kwargs={'conversation_id': self.conversation.id},
        )

        if escalation is not None:
            url = f'{url}?escalation={escalation.pk}'

        return url

    def test_devuelve_el_escalamiento_pedido(self):
        response = self.client.get(self.detail_url(self.first))

        self.assertEqual(response.status_code, 200)

        crm = response.json()['crm']

        self.assertEqual(crm['escalation_id'], self.first.pk)
        self.assertEqual(crm['score'], 10)
        self.assertEqual(crm['unidad_negocio_detectada'], 'Primera unidad')

    def test_cada_escalamiento_devuelve_sus_propios_datos(self):
        first = self.client.get(self.detail_url(self.first)).json()['crm']
        second = self.client.get(self.detail_url(self.second)).json()['crm']

        self.assertNotEqual(first['score'], second['score'])
        self.assertEqual(second['score'], 55)
        self.assertEqual(second['unidad_negocio_detectada'], 'Segunda unidad')

    def test_sin_parametro_devuelve_el_mas_reciente(self):
        response = self.client.get(self.detail_url())

        crm = response.json()['crm']

        self.assertEqual(crm['escalation_id'], self.second.pk)

    def test_escalamiento_inexistente_devuelve_404(self):
        url = reverse(
            'app_prospects:conversation',
            kwargs={'conversation_id': self.conversation.id},
        )

        response = self.client.get(f'{url}?escalation=999999')

        self.assertEqual(response.status_code, 404)

    def test_no_mezcla_datos_del_ultimo_mensaje_de_ia(self):
        """
        El merge genérico rellenaba huecos con el último mensaje de la
        sesión, metiendo datos que no eran de ese escalamiento.
        """
        ChatHistoryModel.objects.create(
            conversation=self.conversation,
            message={
                'role': 'assistant',
                'content': json.dumps({
                    'crm_update': {
                        'score': 90,
                        'presupuesto': 'dato de otro momento',
                    },
                }),
            },
        )

        crm = self.client.get(self.detail_url(self.first)).json()['crm']

        self.assertEqual(crm['score'], 10)
        self.assertNotIn('presupuesto', crm)

    def test_contacto_se_arma_desde_los_campos_planos(self):
        """
        El escalamiento guarda contact_name / partner_name / email_from
        en la raíz, pero el panel lateral espera un objeto "contacto".
        """
        ConversationEscalationModel.objects.filter(pk=self.first.pk).update(
            conversation_content={
                'name': 'Pablo - Tucocinamax',
                'contact_name': 'Pablo',
                'partner_name': 'Tucocinamax',
                'email_from': 'julcon@gmail.com',
            },
        )

        crm = self.client.get(self.detail_url(self.first)).json()['crm']

        self.assertEqual(crm['contacto']['nombre'], 'Pablo')
        self.assertEqual(crm['contacto']['empresa'], 'Tucocinamax')
        self.assertEqual(crm['contacto']['correo'], 'julcon@gmail.com')

    def test_contacto_cae_al_nombre_cuando_no_hay_contact_name(self):
        ConversationEscalationModel.objects.filter(pk=self.first.pk).update(
            conversation_content={'name': 'Alicia de la Torre - Tucasa'},
        )

        crm = self.client.get(self.detail_url(self.first)).json()['crm']

        self.assertEqual(
            crm['contacto']['nombre'],
            'Alicia de la Torre - Tucasa',
        )

    def test_contacto_ya_existente_no_se_sobrescribe(self):
        ConversationEscalationModel.objects.filter(pk=self.first.pk).update(
            conversation_content={
                'contact_name': 'Ignorado',
                'contacto': {
                    'nombre': 'Nombre original',
                    'empresa': 'Empresa original',
                    'correo': 'original@correo.com',
                },
            },
        )

        crm = self.client.get(self.detail_url(self.first)).json()['crm']

        self.assertEqual(crm['contacto']['nombre'], 'Nombre original')
        self.assertEqual(crm['contacto']['empresa'], 'Empresa original')

    def test_devuelve_los_mensajes_de_la_conversacion(self):
        """El modal debe seguir mostrando el chat en modo escalado."""
        ChatHistoryModel.objects.create(
            conversation=self.conversation,
            message={'role': 'user', 'content': 'Hola, me pueden ayudar?'},
        )
        ChatHistoryModel.objects.create(
            conversation=self.conversation,
            message={'role': 'assistant', 'content': 'Claro que sí.'},
        )

        payload = self.client.get(self.detail_url(self.first)).json()

        self.assertEqual(len(payload['conversation']), 2)
        self.assertEqual(
            payload['conversation'][0]['content'],
            'Hola, me pueden ayudar?',
        )

    def test_no_se_cruzan_mensajes_de_conversaciones_con_la_misma_sesion(self):
        """
        Dos conversaciones pueden compartir session_id (una abierta y
        una cerrada). El historial debe salir solo de la pedida.
        """
        cerrada = ConversationModel.objects.create(
            session_id=self.conversation.session_id,
            source='whatsapp',
            state='closed',
        )

        ChatHistoryModel.objects.create(
            conversation=cerrada,
            message={'role': 'user', 'content': 'mensaje de la cerrada'},
        )
        ChatHistoryModel.objects.create(
            conversation=self.conversation,
            message={'role': 'user', 'content': 'mensaje de la abierta'},
        )

        url = reverse(
            'app_prospects:conversation',
            kwargs={'conversation_id': self.conversation.id},
        )

        payload = self.client.get(url).json()

        self.assertEqual(len(payload['conversation']), 1)
        self.assertEqual(
            payload['conversation'][0]['content'],
            'mensaje de la abierta',
        )

    def test_no_escalada_usa_crm_update_del_historial_si_ai_data_es_null(self):
        conversation = ConversationModel.objects.create(
            session_id='sin-escalamiento',
            source='email',
        )

        ChatHistoryModel.objects.create(
            conversation=conversation,
            message={
                'role': 'assistant',
                'content': json.dumps({
                    'reply_to_customer': 'Resumen para el cliente',
                    'conversation_state': 'calificacion',
                    'crm_update': {
                        'unidad_negocio_detectada': 'odoo',
                        'score_comercial': 73,
                        'resumen_empresa': 'Restaurante de una sucursal.',
                    },
                }),
            },
        )

        url = reverse(
            'app_prospects:conversation',
            kwargs={'conversation_id': conversation.id},
        )

        payload = self.client.get(url).json()
        crm = payload['crm']

        self.assertFalse(payload['is_qualified'])
        self.assertFalse(crm['is_qualified'])
        self.assertEqual(crm['conversation_state'], 'calificacion')
        self.assertEqual(crm['unidad_negocio_detectada'], 'odoo')
        self.assertEqual(crm['score_comercial'], 73)
        self.assertEqual(
            crm['resumen_empresa'],
            'Restaurante de una sucursal.',
        )

    def test_escalamiento_de_otra_sesion_no_es_accesible(self):
        other_conversation = ConversationModel.objects.create(
            session_id='otra-sesion',
            source='email',
            prospect=ProspectModel.objects.create(
                name='Otro',
                phone='5550000000',
                person_id=2,
            ),
        )

        foreign = ConversationEscalationModel.objects.create(
            session=other_conversation,
            conversation_content={'crm_update': {'score': 1}},
        )

        response = self.client.get(self.detail_url(foreign))

        self.assertEqual(response.status_code, 404)



class ConversationsModeTests(TestCase):
    """
    Conversaciones tiene dos lados: Producción lista las conversaciones
    reales y Pruebas las de los agentes en modo shadow, que viven en su
    propia tabla. Nunca se mezclan.
    """

    def setUp(self):
        from leadmanager.apps.automations.models import (
            AgentModel, AgentRunModel, TestConversationModel, TestMessageModel,
        )

        self.client.force_login(get_user_model().objects.create_user(
            username='modo', password='test-pass-123',
        ))
        self.url = reverse('app_prospects:conversations')
        analisis = json.dumps({
            'reply_to_customer': 'Respuesta de Sofía',
            'crm_update': {'semaforo': 'amarillo'},
        })

        self.real = ConversationModel.objects.create(session_id='solo-real', source='web')
        self.con_pruebas = ConversationModel.objects.create(
            session_id='con-pruebas', source='email', utm_source='google',
        )
        for conversation in (self.real, self.con_pruebas):
            ChatHistoryModel.objects.create(
                conversation=conversation,
                message={'role': 'assistant', 'content': analisis},
            )

        self.valeria = AgentModel.objects.create(
            key='agent-valeria', name='Valeria', role='Asistente', description='Pruebas',
            mode=AgentModel.ModeEnum.SHADOW_TESTING, is_active=False,
        )
        run = AgentRunModel.objects.create(
            agent_name=self.valeria.key, source='email', conversation=self.con_pruebas,
            mode=AgentModel.ModeEnum.SHADOW_TESTING,
        )
        self.prueba = TestConversationModel.objects.create(
            agent=self.valeria, conversation=self.con_pruebas,
        )
        TestMessageModel.objects.create(
            test_conversation=self.prueba, run=run,
            message={'role': 'user', 'content': 'Quiero un ecommerce'},
        )
        TestMessageModel.objects.create(
            test_conversation=self.prueba, run=run,
            message={'role': 'assistant', 'content': {
                'reply_to_customer': 'Respuesta de Valeria',
                'crm_update': {'semaforo': 'verde', 'contacto': {'nombre': 'Pepe'}},
            }},
        )

    def rows(self, **params):
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, 200)
        return response, list(response.context['page_obj'])

    def test_produccion_es_el_modo_por_defecto_y_lista_solo_reales(self):
        response, rows = self.rows()
        self.assertEqual(response.context['mode'], 'production')
        self.assertEqual(sorted(row.session_id for row in rows), ['con-pruebas', 'solo-real'])
        self.assertNotContains(response, 'Respuesta de Valeria')

    def test_pruebas_lista_solo_conversaciones_de_prueba(self):
        response, rows = self.rows(mode='shadow_testing')
        self.assertEqual([row.pk for row in rows], [self.prueba.pk])
        row = rows[0]
        self.assertEqual(row.assistant_name, 'Valeria')
        self.assertEqual(row.last_message, 'Respuesta de Valeria')
        self.assertEqual(row.semaforo, 'verde')
        self.assertEqual(row.contact_name, 'Pepe')
        self.assertEqual(row.source, 'email')
        self.assertEqual(response.context['conversations_count'], 1)
        self.assertEqual(response.context['escalated_count'], 1)
        self.assertNotContains(response, 'Respuesta de Sofía')

    def test_pruebas_aplica_los_mismos_filtros(self):
        _, rows = self.rows(mode='shadow_testing', source='web')
        self.assertEqual(rows, [])
        _, rows = self.rows(mode='shadow_testing', utm_source='google', semaforo='verde')
        self.assertEqual([row.pk for row in rows], [self.prueba.pk])
        _, rows = self.rows(mode='shadow_testing', semaforo='amarillo')
        self.assertEqual(rows, [])
        _, rows = self.rows(mode='shadow_testing', search_message='ecommerce')
        self.assertEqual([row.pk for row in rows], [self.prueba.pk])

    def test_modo_invalido_regresa_a_produccion(self):
        response, rows = self.rows(mode='otro')
        self.assertEqual(response.context['mode'], 'production')
        self.assertEqual(len(rows), 2)

    def test_modal_de_prueba_muestra_las_respuestas_del_agente_de_pruebas(self):
        url = reverse('app_prospects:test_conversation', args=[self.prueba.pk])
        payload = self.client.get(url).json()

        self.assertEqual(payload['test_agent_name'], 'Valeria')
        self.assertEqual(payload['real_conversation_id'], self.con_pruebas.pk)
        self.assertEqual(
            [(item['type'], item['content']) for item in payload['conversation']],
            [('user', 'Quiero un ecommerce'), ('assistant', 'Respuesta de Valeria')],
        )
        self.assertEqual(payload['crm']['semaforo'], 'verde')
        self.assertEqual(payload['attribution']['utm_source'], 'google')

    def test_modal_real_no_incluye_la_conversacion_de_prueba(self):
        url = reverse('app_prospects:conversation', args=[self.con_pruebas.pk])
        payload = self.client.get(url).json()

        self.assertNotIn('test_agent_name', payload)
        self.assertNotIn('Respuesta de Valeria', json.dumps(payload))

    def test_modal_de_prueba_inexistente_da_404(self):
        url = reverse('app_prospects:test_conversation', args=[999999])
        self.assertEqual(self.client.get(url).status_code, 404)
