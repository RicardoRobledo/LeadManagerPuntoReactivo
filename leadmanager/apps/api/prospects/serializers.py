from rest_framework import status
from rest_framework import serializers

from leadmanager.apps.prospects.models import MessengerReminderModel


class MessengerReminderSerializer(serializers.ModelSerializer):

    class Meta:
        model = MessengerReminderModel
        fields = (
            'id',
            'sender_id',
            'phase',
            'sent',
            'scheduled_at',
            'created_at',
            'updated_at'
        )
