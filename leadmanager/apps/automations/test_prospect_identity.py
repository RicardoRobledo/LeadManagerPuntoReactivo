"""Identidad del prospecto al abrir y continuar conversaciones, con Odoo simulado."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import TestCase

from leadmanager.apps.prospects.models import (
    ChatHistoryModel, ConversationModel, ConversationStateEnum, ProspectModel,
)

from .agents.prospector_agent import prospector_steps as steps


class ProspectConversationIdentityTests(TestCase):
    def setUp(self):
        self.unrelated = ProspectModel.objects.create(
            id=19, name='Murillo', email='tatacos@gmail.com',
            enterprise='Tatacos', person_id=None,
        )
        self.agent = SimpleNamespace(name='prospector', log_event=Mock())

    def context(self, source, *, email='diana@gig.example', session_id='diana-web-session'):
        message = 'Necesito información sobre un proyecto para mi empresa.'
        payloads = {
            'email': {'from': {'email': email}, 'body': message},
            'form': {
                'name': 'Diana Díaz', 'email': email, 'phone': '5551234567',
                'subject': 'Información', 'message': message,
            },
            'web': {
                'name': 'Diana Díaz', 'email': email, 'company_name': 'GIG',
                'session_id': session_id, 'business_unit': 'Kioscos digitales',
                'preferred_contact_method': 'email', 'message': message,
            },
            'whatsapp': {'phone': '525551234567', 'message': message},
        }
        return {'source': source, source: payloads[source], 'history': []}

    def process_identity(self, ctx, *, odoo_person=None):
        source = ctx['source']
        original_payload = deepcopy(ctx[source])
        if source == 'email':
            identifier = ctx[source]['from']['email']
        elif source == 'whatsapp':
            identifier = ctx[source]['phone']
        else:
            identifier = ctx[source]['email']

        with patch.object(steps, 'find_person_by_source', return_value=odoo_person) as find_person:
            steps.verify_existing_prospect_step(self.agent, ctx)
            steps.verify_existing_conversation_step(self.agent, ctx)

        find_person.assert_called_once_with(identifier=identifier, source=source)
        self.assertEqual(ctx['source_identifier'], identifier)
        self.assertEqual(ctx[source], original_payload)
        self.assertEqual(ctx['history'][-1]['role'], 'user')
        if source == 'email':
            self.assertIn(original_payload['body'], ctx['history'][-1]['content'])
        else:
            self.assertIn(original_payload['message'], ctx['history'][-1]['content'])
        self.assertNotIn(self.unrelated.email, ctx['history'][-1]['content'])
        ctx['conversation'].refresh_from_db()
        return ctx['conversation']

    def test_unknown_email_contacts_do_not_link_the_first_null_person_id(self):
        for source in ('email', 'form', 'web'):
            with self.subTest(source=source):
                ctx = self.context(source, email=f'diana-{source}@gig.example')

                conversation = self.process_identity(ctx)

                self.assertTrue(ctx['_conversacion_nueva'])
                self.assertIsNone(conversation.prospect_id)
                self.assertFalse(ctx['person']['db_exists'])
                self.assertEqual(conversation.messages.count(), 1)
                self.assertEqual(ProspectModel.objects.count(), 1)

        self.unrelated.refresh_from_db()
        self.assertEqual(self.unrelated.email, 'tatacos@gmail.com')
        self.assertIsNone(self.unrelated.person_id)

    def test_local_email_links_the_matching_contact_without_odoo(self):
        for source in ('email', 'form', 'web'):
            with self.subTest(source=source):
                email = f'diana-{source}@gig.example'
                prospect = ProspectModel.objects.create(
                    name='Diana Díaz', email=email, enterprise='GIG', person_id=None,
                )
                ctx = self.context(source, email=email.upper())

                conversation = self.process_identity(ctx)

                self.assertEqual(conversation.prospect_id, prospect.pk)
                self.assertEqual(ctx['person']['db_data']['id'], prospect.pk)
                self.assertFalse(ctx['person']['exists'])
                self.assertNotEqual(conversation.prospect_id, self.unrelated.pk)

    def test_odoo_partner_id_is_distinct_from_the_local_primary_key(self):
        prospect = ProspectModel.objects.create(
            id=41, name='Diana Díaz', email='diana@gig.example',
            enterprise='GIG', person_id=8001,
        )
        decoy = ProspectModel.objects.create(
            id=8001, name='Contacto diferente', email='other@example.com', person_id=None,
        )
        ctx = self.context('form')

        conversation = self.process_identity(
            ctx, odoo_person={'id': 8001, 'email': prospect.email, 'name': prospect.name},
        )

        self.assertEqual(conversation.prospect_id, prospect.pk)
        self.assertNotEqual(conversation.prospect_id, decoy.pk)
        self.assertNotEqual(prospect.pk, prospect.person_id)
        self.assertEqual(ProspectModel.objects.count(), 3)

    def test_unknown_odoo_partner_falls_back_to_matching_local_email(self):
        for source in ('email', 'form', 'web'):
            with self.subTest(source=source):
                email = f'diana-{source}@gig.example'
                prospect = ProspectModel.objects.create(
                    name='Diana Díaz', email=email, enterprise='GIG', person_id=None,
                )
                ctx = self.context(source, email=email)

                conversation = self.process_identity(
                    ctx, odoo_person={'id': 9001, 'email': email, 'name': prospect.name},
                )

                self.assertEqual(conversation.prospect_id, prospect.pk)
                prospect.refresh_from_db()
                self.assertIsNone(prospect.person_id)

    def test_whatsapp_links_the_local_contact_by_odoo_partner(self):
        prospect = ProspectModel.objects.create(
            name='Diana Díaz', email='diana@gig.example', phone='525551234567', person_id=8001,
        )
        ctx = self.context('whatsapp')

        conversation = self.process_identity(
            ctx, odoo_person={'id': 8001, 'phone': '525551234567', 'name': prospect.name},
        )

        self.assertEqual(conversation.prospect_id, prospect.pk)
        self.assertEqual(conversation.session_id, '525551234567')
        self.assertFalse(ctx['person']['db_exists'])

    def test_unknown_whatsapp_contact_keeps_prospect_unassigned(self):
        ctx = self.context('whatsapp')

        conversation = self.process_identity(ctx)

        self.assertIsNone(conversation.prospect_id)
        self.assertEqual(conversation.messages.count(), 1)
        self.assertEqual(ProspectModel.objects.count(), 1)

    def test_existing_conversation_keeps_its_contact_and_appends_one_message(self):
        prospect = ProspectModel.objects.create(
            name='Diana Díaz', email='diana@gig.example', enterprise='GIG', person_id=None,
        )
        existing = ConversationModel.objects.create(
            source='form', session_id=prospect.email,
            state=ConversationStateEnum.OPEN, prospect=prospect,
        )
        previous = {'role': 'assistant', 'content': '¿En qué plazo necesitas el proyecto?'}
        ChatHistoryModel.objects.create(conversation=existing, message=previous)
        ctx = self.context('form')
        counts = (ProspectModel.objects.count(), ConversationModel.objects.count())

        conversation = self.process_identity(ctx)

        self.assertFalse(ctx['_conversacion_nueva'])
        self.assertEqual(conversation.pk, existing.pk)
        self.assertEqual(conversation.prospect_id, prospect.pk)
        self.assertEqual(ctx['history'][0], previous)
        self.assertEqual(conversation.messages.count(), 2)
        self.assertEqual((ProspectModel.objects.count(), ConversationModel.objects.count()), counts)

    def test_existing_unassigned_conversation_preserves_its_history(self):
        existing = ConversationModel.objects.create(
            source='form', session_id='diana@gig.example', state=ConversationStateEnum.OPEN,
        )
        previous = {'role': 'assistant', 'content': 'Cuéntame más sobre tu proyecto.'}
        ChatHistoryModel.objects.create(conversation=existing, message=previous)
        ctx = self.context('form')

        conversation = self.process_identity(ctx)

        self.assertFalse(ctx['_conversacion_nueva'])
        self.assertEqual(conversation.pk, existing.pk)
        self.assertIsNone(conversation.prospect_id)
        self.assertEqual(ctx['history'][0], previous)
        self.assertEqual(conversation.messages.count(), 2)
        self.assertEqual(ConversationModel.objects.count(), 1)

    def test_precreated_web_conversation_preserves_payload_and_stays_unassigned(self):
        local = ProspectModel.objects.create(
            name='Diana Díaz', email='diana@gig.example', enterprise='GIG', person_id=None,
        )
        existing = ConversationModel.objects.create(
            source='web', session_id='diana-web-session', state=ConversationStateEnum.OPEN,
        )
        ctx = self.context('web')
        ctx['web']['_conversation_id'] = existing.pk

        conversation = self.process_identity(ctx)

        self.assertFalse(ctx['_conversacion_nueva'])
        self.assertEqual(conversation.pk, existing.pk)
        self.assertIsNone(conversation.prospect_id)
        self.assertEqual(ctx['person']['db_data']['id'], local.pk)
        self.assertEqual(conversation.messages.count(), 1)
        message = conversation.messages.get().message
        self.assertEqual(message['display_content'], ctx['web']['message'])
        self.assertIn(local.email, message['content'])
        self.assertIn('GIG', message['content'])
        self.assertEqual(ProspectModel.objects.count(), 2)

