from datetime import timedelta
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from leadmanager.apps.prospects.models import (
    MessengerReminderModel,
    EmailReminderModel,
    ReminderPhase
)

User = get_user_model()


class ReminderFlowTest(APITestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            password="123456"
        )
        self.client.force_authenticate(user=self.user)
        self.url = "/api/prospects/reminders/get-reminders/"

    def test_only_expired_reminders_are_returned(self):
        now = timezone.now()

        # 🔥 EXPIRADO
        expired = MessengerReminderModel.objects.create(
            sender_id="user_1",
            phase=ReminderPhase.FIRST,
            sent=False,
            scheduled_at=now - timedelta(days=2)
        )

        # ⏳ NO expirado
        MessengerReminderModel.objects.create(
            sender_id="user_2",
            phase=ReminderPhase.FIRST,
            sent=False,
            scheduled_at=now + timedelta(hours=2)
        )

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

        data = response.json()["messenger_reminders"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["id"], expired.id)

    def test_next_phase_is_created(self):
        now = timezone.now()

        MessengerReminderModel.objects.create(
            sender_id="user_4",
            phase=ReminderPhase.FIRST,
            sent=False,
            scheduled_at=now - timedelta(days=2)  # 🔥 vencido
        )

        self.client.get(self.url)

        self.assertTrue(
            MessengerReminderModel.objects.filter(
                sender_id="user_4",
                phase=ReminderPhase.SECOND
            ).exists()
        )

    def test_next_phase_transitions(self):
        now = timezone.now()

        cases = [
            # phase actual, phase esperada después
            (ReminderPhase.FIRST, ReminderPhase.SECOND),
            (ReminderPhase.SECOND, ReminderPhase.THIRD),
            (ReminderPhase.THIRD, ReminderPhase.FOURTH),
            (ReminderPhase.FOURTH, None),  # fase terminal
        ]

        for idx, (current_phase, expected_next) in enumerate(cases):
            sender_id = f"user_phase_{idx}"

            MessengerReminderModel.objects.create(
                sender_id=sender_id,
                phase=current_phase,
                sent=False,
                scheduled_at=now -
                timedelta(
                    days=2) if current_phase != ReminderPhase.FOURTH else None
            )

            self.client.get(self.url)

            if expected_next is None:
                # FOURTH no debe crear nada
                self.assertEqual(
                    MessengerReminderModel.objects.filter(
                        sender_id=sender_id
                    ).count(),
                    1
                )
            else:
                self.assertTrue(
                    MessengerReminderModel.objects.filter(
                        sender_id=sender_id,
                        phase=expected_next
                    ).exists()
                )

    def test_idempotency_no_duplicate_phases(self):
        now = timezone.now()

        MessengerReminderModel.objects.create(
            sender_id="user_5",
            phase=ReminderPhase.FIRST,
            sent=False,
            scheduled_at=now - timedelta(days=2)  # 🔥 vencido
        )

        self.client.get(self.url)
        self.client.get(self.url)

        self.assertEqual(
            MessengerReminderModel.objects.filter(
                sender_id="user_5",
                phase=ReminderPhase.SECOND
            ).count(),
            1
        )

    def test_create_messenger_reminder_post(self):
        response = self.client.post(
            "/api/prospects/reminders/create-messenger-reminder/",
            data={
                "sender_id": "user_post_1"
            }
        )

        self.assertEqual(response.status_code, 201)

        data = response.json()
        self.assertIn("scheduled_at", data)

        reminder = MessengerReminderModel.objects.get(
            sender_id="user_post_1"
        )

        self.assertEqual(reminder.phase, ReminderPhase.FIRST)
        self.assertFalse(reminder.sent)
        self.assertIsNotNone(reminder.scheduled_at)

    def test_messenger_reminder_scheduled_at_is_in_future(self):
        before = timezone.now()

        self.client.post(
            "/api/prospects/reminders/create-messenger-reminder/",
            data={
                "sender_id": "user_post_2"
            }
        )

        reminder = MessengerReminderModel.objects.get(
            sender_id="user_post_2"
        )

        after = timezone.now()

        self.assertGreater(reminder.scheduled_at, before)
        self.assertLess(
            reminder.scheduled_at,
            after + timedelta(days=1, seconds=5)
        )

    def test_create_email_reminder_post(self):
        response = self.client.post(
            "/api/prospects/reminders/create-email-reminder/",
            data={
                "email": "test@example.com"
            }
        )

        self.assertEqual(response.status_code, 201)

        reminder = EmailReminderModel.objects.get(
            email="test@example.com"
        )

        self.assertEqual(reminder.phase, ReminderPhase.FIRST)
        self.assertFalse(reminder.sent)
        self.assertIsNotNone(reminder.scheduled_at)

    def test_create_messenger_reminder_missing_sender_id(self):
        response = self.client.post(
            "/api/prospects/reminders/create-messenger-reminder/",
            data={}
        )

        self.assertEqual(response.status_code, 403)

    def test_create_email_reminder_missing_email(self):
        response = self.client.post(
            "/api/prospects/reminders/create-email-reminder/",
            data={}
        )

        self.assertEqual(response.status_code, 403)

    def test_sent_reminder_is_not_processed_again(self):
        now = timezone.now()

        MessengerReminderModel.objects.create(
            sender_id="user_sent",
            phase=ReminderPhase.FIRST,
            sent=True,  # 🔒 ya procesado
            scheduled_at=now - timedelta(days=2)
        )

        self.client.get(self.url)

        self.assertFalse(
            MessengerReminderModel.objects.filter(
                sender_id="user_sent",
                phase=ReminderPhase.SECOND
            ).exists()
        )

    def test_does_not_create_duplicate_next_phase(self):
        now = timezone.now()

        MessengerReminderModel.objects.create(
            sender_id="user_dup",
            phase=ReminderPhase.FIRST,
            sent=False,
            scheduled_at=now - timedelta(days=2)
        )

        MessengerReminderModel.objects.create(
            sender_id="user_dup",
            phase=ReminderPhase.SECOND,
            sent=False,
            scheduled_at=now + timedelta(days=3)
        )

        self.client.get(self.url)

        self.assertEqual(
            MessengerReminderModel.objects.filter(
                sender_id="user_dup",
                phase=ReminderPhase.SECOND
            ).count(),
            1
        )

    def test_messenger_and_email_are_independent(self):
        now = timezone.now()

        MessengerReminderModel.objects.create(
            sender_id="user_m",
            phase=ReminderPhase.FIRST,
            sent=False,
            scheduled_at=now - timedelta(days=2)
        )

        EmailReminderModel.objects.create(
            email="user@test.com",
            phase=ReminderPhase.FIRST,
            sent=False,
            scheduled_at=now - timedelta(days=2)
        )

        self.client.get(self.url)

        self.assertTrue(
            MessengerReminderModel.objects.filter(
                sender_id="user_m",
                phase=ReminderPhase.SECOND
            ).exists()
        )

        self.assertTrue(
            EmailReminderModel.objects.filter(
                email="user@test.com",
                phase=ReminderPhase.SECOND
            ).exists()
        )

    def test_fourth_is_processed_but_no_next_is_created(self):
        now = timezone.now()

        MessengerReminderModel.objects.create(
            sender_id="user_final",
            phase=ReminderPhase.FOURTH,
            sent=False,
            scheduled_at=now - timedelta(days=3)
        )

        self.client.get(self.url)

        # Se marca como enviado
        reminder = MessengerReminderModel.objects.get(sender_id="user_final")
        self.assertTrue(reminder.sent)

        # No se crea ningún otro
        self.assertEqual(
            MessengerReminderModel.objects.filter(
                sender_id="user_final").count(),
            1
        )

    def test_messenger_reminders_stop_when_lead_is_converted(self):
        now = timezone.now()

        MessengerReminderModel.objects.create(
            sender_id="user_stop",
            phase=ReminderPhase.FIRST,
            sent=False,
            stop_reminder=True,
            scheduled_at=now - timedelta(days=2)  # 🔥 vencido
        )

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

        # ❌ NO se crea la siguiente fase
        self.assertFalse(
            MessengerReminderModel.objects.filter(
                sender_id="user_stop",
                phase=ReminderPhase.SECOND
            ).exists()
        )

        # ❌ NO se devuelve en la API
        data = response.json()
        self.assertEqual(data["messenger_reminders"], [])

    def test_email_reminders_stop_when_lead_is_converted(self):
        now = timezone.now()

        EmailReminderModel.objects.create(
            email="stop@test.com",
            phase=ReminderPhase.FIRST,
            sent=False,
            stop_reminder=True,
            scheduled_at=now - timedelta(days=2)  # 🔥 vencido
        )

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

        # ❌ NO se crea la siguiente fase
        self.assertFalse(
            EmailReminderModel.objects.filter(
                email="stop@test.com",
                phase=ReminderPhase.SECOND
            ).exists()
        )

        # ❌ NO se devuelve en la API
        data = response.json()
        self.assertEqual(data["email_reminders"], [])

    def test_stopped_reminders_are_not_returned(self):
        now = timezone.now()

        MessengerReminderModel.objects.create(
            sender_id="user_stopped",
            phase=ReminderPhase.FIRST,
            sent=False,
            stop_reminder=True,
            scheduled_at=now - timedelta(days=2)
        )

        response = self.client.get(self.url)
        data = response.json()

        self.assertEqual(data["messenger_reminders"], [])
        self.assertEqual(data["email_reminders"], [])
