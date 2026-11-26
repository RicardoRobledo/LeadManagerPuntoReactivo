from django.db.models import Q
from cmath import phase
from zoneinfo import ZoneInfo
from datetime import timedelta
from django.utils import timezone

from rest_framework import status
from django.db.models import F
from django.db import transaction
from leadmanager.apps.base.models import BaseModel
from leadmanager.apps.prospects.models import ChatHistoryModel, ProspectModel, ConversationModel, ReminderPhase, MessengerReminderModel, NotQualifiedCategoryModel, ConversationSourceEnum, ConversationStateEnum
from leadmanager.apps.automations.reminder_schedule import get_reminder_schedule, interval_delta
from .serializers import MessengerReminderSerializer

from rest_framework.decorators import api_view
from django.http import JsonResponse


PHASE_SEQUENCE = {
    ReminderPhase.FIRST: ReminderPhase.SECOND,
    ReminderPhase.SECOND: ReminderPhase.THIRD,
    ReminderPhase.THIRD: ReminderPhase.FOURTH,
}

PHASE_DELAYS = {
    ReminderPhase.FIRST: timedelta(hours=48),
    ReminderPhase.SECOND: timedelta(days=1),
    ReminderPhase.THIRD: timedelta(days=3),
    ReminderPhase.FOURTH: timedelta(days=3),
}


def configured_phase_delays():
    delays = {}
    transitions = {('none', 'first'): ReminderPhase.FIRST,
                   ('first', 'second'): ReminderPhase.SECOND,
                   ('second', 'third'): ReminderPhase.THIRD}
    for item in get_reminder_schedule():
        phase = transitions.get((item.get('from'), item.get('to')))
        if phase and item.get('enabled', True):
            delays[phase] = interval_delta(item)
    return delays

CHANNEL_CONFIG = {
    # SmsReminderModel: 'phone_number',
    MessengerReminderModel: 'sender_id',
}


mx_zi = ZoneInfo('America/Mexico_City')


def compute_scheduled_at(phase):
    # ❌ Fase inválida (no existe delay)
    delays = configured_phase_delays()
    if phase not in delays:
        raise ValueError(f'Invalid phase for scheduling: {phase}')

    # ✅ Fases con delay
    return timezone.now() + delays[phase]


@api_view(['POST'])
def create_message(request):

    email = request.POST.get('email', '')
    message = request.POST.get('message', '')
    role = request.POST.get('role', '')

    if not all([email, message, role]):
        return JsonResponse({'error': 'Missing parameters'}, status=403)

    if role not in ('user', 'assistant'):
        return JsonResponse({'error': 'Invalid role parameter'}, status=403)

    prospect, created = ProspectModel.objects.get_or_create(
        email=email)
    conversation = None

    if created:

        conversation = ConversationModel.objects.create(
            prospect=prospect,
            type='email',
            state='in_progress'
        )

        #EmailMessageModel.objects.create(conversation=conversation, role=role, message=message)
    else:

        conversation = prospect.conversations.order_by('-created_at').first()

        if conversation.state == 'in_progress':

            #EmailMessageModel.objects.create(conversation=conversation, role=role, message=message)
            pass

        else:

            conversation = ConversationModel.objects.create(
                prospect=prospect,
                type='email',
                state='in_progress'
            )

            #EmailMessageModel.objects.create(conversation=conversation, role=role, message=message)

    return JsonResponse({'msg': 'Message created successfully', 'conversation_id': conversation.id}, status=201)


@api_view(['POST'])
def create_form_conversation(request):

    message = request.POST.get('message', '')
    role = request.POST.get('role', '')
    source_site = request.POST.get('source_site', None)

    if not all([message, role, source_site]):
        return JsonResponse({'error': 'Missing parameters'}, status=403)

    if role not in ('user', 'assistant'):
        return JsonResponse({'error': 'Invalid role parameter'}, status=403)

    if not source_site in ('google', 'facebook',):
        return JsonResponse({'error': 'Invalid source_site parameter'}, status=403)

    conversation = ConversationModel.objects.create(
        type='form',
        state='in_progress',
        source_site=source_site)

    #FormMessageModel.objects.create(    conversation=conversation,    role=role,    message=message,)

    return JsonResponse({'msg': 'Form message created successfully', 'conversation_id': conversation.id}, status=201)


@api_view(['POST'])
def close_form_conversation(request):
    conversation_id = request.POST.get('conversation_id')
    message = request.POST.get('message', '')
    role = request.POST.get('role', '')

    if not all([conversation_id, message, role]):
        return JsonResponse(
            {'error': 'Missing parameters'},
            status=403
        )

    if role not in ('user', 'assistant'):
        return JsonResponse(
            {'error': 'Invalid role parameter'},
            status=403
        )

    if not conversation_id.isdigit():
        return JsonResponse(
            {'error': 'Invalid conversation_id parameter'},
            status=403
        )

    try:
        with transaction.atomic():
            conversation = (
                ConversationModel.objects
                .select_for_update()
                .filter(id=conversation_id, state='in_progress')
                .first()
            )

            if not conversation:
                return JsonResponse(
                    {'error': 'No in-progress conversation found to close'},
                    status=404
                )

            #FormMessageModel.objects.create(conversation=conversation,role=role,message=message)

            conversation.state = 'closed'
            conversation.save(update_fields=['state'])

            return JsonResponse(
                {'message': 'Conversation closed successfully'},
                status=200
            )

    except Exception as e:
        return JsonResponse(
            {'error': f'Unexpected error closing conversation: {e}'},
            status=500
        )


@api_view(['GET'])
def get_conversation_history(request):

    id = request.GET.get('id', '')

    if not id:
        return JsonResponse({'error': 'ID parameter is required'}, status=403)

    #conversation = EmailMessageModel.objects.filter(conversation__id=id)

    #if not conversation.exists():
    #    return JsonResponse({'error': 'Conversation not found'}, status=404)

    #data = [
    #    {'message': c.message, 'role': c.role,
    #        'created_at': c.created_at, 'id': c.id}
    #    for c in conversation.order_by('created_at')
    #]

    #return JsonResponse({'conversation': data}, status=200)
    pass


@api_view(['GET'])
def email_exists(request):

    email = request.GET.get('email')

    if not email:
        return JsonResponse({'error': 'Email parameter is required'}, status=403)

    exists = ProspectModel.objects.filter(email=email).exists()

    return JsonResponse({'exists': exists}, status=200)


@api_view(['GET'])
def get_conversation_state(request):

    email = request.GET.get('email')
    if not email:
        return JsonResponse({'error': 'Email parameter is required'}, status=403)

    prospect = ProspectModel.objects.prefetch_related(
        'conversations').filter(email=email)
    if not prospect.exists():
        return JsonResponse({'error': 'Prospect not found'}, status=404)

    conversation = prospect.first().conversations.order_by('-created_at').first()
    if conversation:
        return JsonResponse({'state': conversation.state}, status=200)
    else:
        return JsonResponse({'error': 'Conversation not found'}, status=404)


@api_view(['POST'])
def close_conversation(request):

    email = request.POST.get('email')

    if not email:
        return JsonResponse({'error': 'Email parameter is required'}, status=403)

    prospect = ProspectModel.objects.prefetch_related(
        'conversations').filter(email=email)
    if not prospect.exists():
        return JsonResponse({'error': 'Prospect not found'}, status=404)

    conversation = prospect.first().conversations.filter(
        state='in_progress').order_by('-created_at')

    if conversation.exists():
        conversation = conversation.first()
        conversation.state = 'closed'
        conversation.save()
        return JsonResponse({'message': 'Conversation closed successfully'}, status=200)
    else:
        return JsonResponse({'error': 'No in-progress conversation found to close'}, status=404)


@api_view(['GET'])
def get_next_owner(request):

    try:
        with transaction.atomic():

            owner = (
                OwnerModel.objects
                .select_for_update(skip_locked=True)
                .order_by(
                    F('last_assigned_at').asc(nulls_first=True)
                )
                .first()
            )

            if not owner:
                return JsonResponse(
                    {'error': 'No owners available'},
                    status=status.HTTP_404_NOT_FOUND
                )

            # 🔐 Guardar SIEMPRE en UTC
            owner.last_assigned_at = timezone.now()
            owner.allocated_amount = F('allocated_amount') + 1
            owner.save(update_fields=['last_assigned_at', 'allocated_amount'])

        return JsonResponse(
            {
                'hubspot_owner_id': owner.hubspot_owner_id,
                'email': owner.email
            },
            status=status.HTTP_200_OK
        )

    except Exception as e:
        return JsonResponse(
            {'error': 'Failed to allocate owner'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
def create_sms_reminder(request):

    phone_number = request.POST.get('phone_number', None)
    phase = request.POST.get('phase', None)

    if not phone_number or not phase:
        return JsonResponse({'error': 'missing parameters'}, status=403)

    if not phase or phase not in ReminderPhase.values:
        return JsonResponse(
            {
                'error': 'invalid phase',
                'valid_phases': ReminderPhase.values
            },
            status=403
        )

    #reminder = SmsReminderModel.objects.filter(    phone_number=phone_number,    phase=phase)

    #if reminder.exists():
    #    return JsonResponse({'error': 'reminder already exists'}, status=409)

    with transaction.atomic():
        pass
        #SmsReminderModel.objects.create(    phone_number=phone_number,    phase=phase)

    return JsonResponse({'message': 'reminder created successfully'}, status=201)


@api_view(['POST'])
def create_messenger_reminder(request):

    sender_id = request.POST.get('sender_id', None)
    phase = request.POST.get('phase', None)

    if not sender_id or not phase:
        return JsonResponse({'error': 'missing parameters'}, status=403)

    if not phase or phase not in ReminderPhase.values:
        return JsonResponse(
            {
                'error': 'invalid phase',
                'valid_phases': ReminderPhase.values
            },
            status=403
        )

    reminder = MessengerReminderModel.objects.filter(
        sender_id=sender_id,
        phase=phase
    )

    if reminder.exists():
        return JsonResponse({'error': 'reminder already exists'}, status=409)

    with transaction.atomic():
        MessengerReminderModel.objects.create(
            sender_id=sender_id,
            phase=phase
        )

    return JsonResponse({'message': 'reminder created successfully'}, status=201)


@api_view(['POST'])
def create_email_reminder(request):

    email = request.POST.get('email', None)
    phase = request.POST.get('phase', None)

    if not email or not phase:
        return JsonResponse({'error': 'missing parameters'}, status=403)

    if not phase or phase not in ReminderPhase.values:
        return JsonResponse(
            {
                'error': 'invalid phase',
                'valid_phases': ReminderPhase.values
            },
            status=403
        )

    #reminder = EmailReminderModel.objects.filter(email=email, phase=phase)

    #if reminder.exists():
    #    return JsonResponse({'error': 'reminder already exists'}, status=409)

    with transaction.atomic():
        pass
        #EmailReminderModel.objects.create(    email=email,    phase=phase)

    return JsonResponse({'message': 'reminder created successfully'}, status=201)


@api_view(['POST'])
def create_sms_reminder(request):

    phone_number = request.POST.get('phone_number', None)
    phase = request.POST.get('phase', None)

    if not all([phase, phone_number]):
        return JsonResponse({'error': 'missing phase or phone_number parameter'}, status=403)

    if not phase or phase not in ReminderPhase.values:
        return JsonResponse({'error': 'invalid phase', 'valid_phases': ReminderPhase.values}, status=403)

    with transaction.atomic():
        pass
        #SmsReminderModel.objects.create(    phone_number=phone_number,    phase=phase,)

    return JsonResponse({'message': 'sms reminder created successfully'}, status=201)


@api_view(['POST'])
def create_messenger_reminder(request):

    sender_id = request.POST.get('sender_id')
    phase = ReminderPhase.FIRST

    if not sender_id:
        return JsonResponse({'error': 'missing sender_id parameters'}, status=403)

    try:
        scheduled_at = compute_scheduled_at(phase)
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=403)

    with transaction.atomic():
        MessengerReminderModel.objects.create(
            sender_id=sender_id,
            phase=phase,
            scheduled_at=scheduled_at,
        )

    return JsonResponse(
        {
            'message': 'messenger reminder created successfully',
            'scheduled_at': scheduled_at,
        },
        status=201
    )


@api_view(['POST'])
def create_email_reminder(request):

    email = request.POST.get('email', None)
    phase = ReminderPhase.FIRST

    if not email:
        return JsonResponse({'error': 'missing email parameter'}, status=403)

    try:
        scheduled_at = compute_scheduled_at(phase)
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=403)

    with transaction.atomic():
        #EmailReminderModel.objects.create(    email=email,    phase=phase,    scheduled_at=scheduled_at,)
        pass

    return JsonResponse(
        {
            'message': 'messenger reminder created successfully',
            'scheduled_at': scheduled_at,
        },
        status=201
    )


def create_next_phase(messenger_qs, email_qs):
    now = timezone.now()

    def process(queryset, model_cls):
        contact_field = CHANNEL_CONFIG[model_cls]
        to_create = []
        to_mark_sent = []

        # 🔁 Transiciones normales (FIRST → SECOND → THIRD → FOURTH)
        for phase, next_phase in PHASE_SEQUENCE.items():

            candidates = queryset.filter(
                phase=phase,
                scheduled_at__lte=now,
                sent=False
            )

            if not candidates.exists():
                continue

            if next_phase not in configured_phase_delays():
                to_mark_sent.extend(candidates.values_list('id', flat=True))
                continue

            contacts = list(
                candidates.values_list(contact_field, flat=True)
            )

            existing = set(
                model_cls.objects.filter(
                    **{
                        f'{contact_field}__in': contacts,
                        'phase': next_phase
                    }
                ).values_list(contact_field, flat=True)
            )

            for reminder in candidates:

                # 🔴 CASO: lead ya no debe recibir mensajes
                if reminder.stop_reminder:
                    to_mark_sent.append(reminder.id)
                    continue  # ⏭️ NO crea next phase

                contact = getattr(reminder, contact_field)

                if contact in existing:
                    continue

                to_create.append(
                    model_cls(
                        **{
                            contact_field: contact,
                            'phase': next_phase,
                            'scheduled_at': compute_scheduled_at(next_phase),
                        }
                    )
                )

                to_mark_sent.append(reminder.id)

        # ✅ FASE FINAL (FOURTH): se procesa, pero NO crea nada
        finals = queryset.filter(
            phase=ReminderPhase.FOURTH,
            scheduled_at__lte=now,
            sent=False
        )

        if finals.exists():
            to_mark_sent.extend(finals.values_list('id', flat=True))

        # 🔒 Persistencia
        if to_create:
            model_cls.objects.bulk_create(to_create)

        if to_mark_sent:
            model_cls.objects.filter(id__in=to_mark_sent).update(sent=True)

    process(messenger_qs, MessengerReminderModel)
    #process(email_qs, EmailReminderModel)


def expired_queryset(model_cls):
    return model_cls.objects.filter(
        scheduled_at__lte=timezone.now(),
        sent=False,
        stop_reminder=False
    )


@api_view(['GET'])
def get_reminders(request):

    with transaction.atomic():

        messenger_qs = (
            expired_queryset(MessengerReminderModel)
            .select_for_update()
        )

        #email_qs = (
        #    expired_queryset(EmailReminderModel)
        #    .select_for_update()
        #)

        # ⚠️ EVALUAR dentro del atomic
        messenger_list = list(messenger_qs)
        #email_list = list(email_qs)

        # Avanza fases y marca sent SOLO de estos
        #create_next_phase(messenger_qs, email_qs)

        messenger_data = MessengerReminderSerializer(
            messenger_list, many=True
        ).data

        #email_data = EmailReminderSerializer(
        #    email_list, many=True
        #).data

    return JsonResponse(
        {
            'messenger_reminders': messenger_data,
        #    'email_reminders': email_data
        },
        status=200
    )


@api_view(['GET'])
def verify_sms_existing_reminder(request):

    phone_number = request.GET.get('phone_number', None)

    if not phone_number:
        return JsonResponse({'error': 'phone_number parameter is required'}, status=403)

    #reminder = SmsReminderModel.objects.filter(
    #    phone_number=phone_number,
    #    sent=False,
    #    stop_reminder=False
    #)

    #if not reminder.exists():
    #    return JsonResponse({'exists': False}, status=200)

    return JsonResponse({'exists': True}, status=200)


@api_view(['GET'])
def verify_messenger_existing_reminder(request):

    sender_id = request.GET.get('sender_id', None)

    if not sender_id:
        return JsonResponse({'error': 'sender_id parameter is required'}, status=403)

    reminder = MessengerReminderModel.objects.filter(
        sender_id=sender_id,
        sent=False,
        stop_reminder=False
    )

    if not reminder.exists():
        return JsonResponse({'exists': False}, status=200)

    return JsonResponse({'exists': True}, status=200)


@api_view(['GET'])
def verify_email_existing_reminder(request):

    email = request.GET.get('email', None)

    if not email:
        return JsonResponse({'error': 'email parameter is required'}, status=403)

    #reminder = EmailReminderModel.objects.filter(
    #    email=email,
    #    sent=False,
    #    stop_reminder=False
    #)

    #if not reminder.exists():
    #    return JsonResponse({'exists': False}, status=200)

    return JsonResponse({'exists': True}, status=200)


@api_view(['POST'])
def mark_messenger_as_quoted(request):
    sender_id = request.POST.get('sender_id')

    if not sender_id:
        return JsonResponse({'error': 'missing sender_id'}, status=400)

    reminder = (
        MessengerReminderModel.objects
        .filter(
            sender_id=sender_id,
            sent=False
        )
        .order_by('-created_at')
        .first()
    )

    if not reminder:
        return JsonResponse({'status': 'no active reminder'}, status=200)

    if reminder.stop_reminder:
        return JsonResponse({'status': 'already stopped'}, status=200)

    with transaction.atomic():
        reminder.stop_reminder = True
        reminder.save(update_fields=['stop_reminder'])

    return JsonResponse({'status': 'messenger reminder stopped'}, status=200)


@api_view(['POST'])
def mark_email_as_quoted(request):
    email = request.POST.get('email')

    if not email:
        return JsonResponse({'error': 'missing email'}, status=400)

    #reminder = (
    #    EmailReminderModel.objects
    #    .filter(
    #        email=email,
    #        sent=False
    #    )
    #    .order_by('-created_at')
    #    .first()
    #)

    #if not reminder:
    #    return JsonResponse({'status': 'no active reminder'}, status=200)

    #if reminder.stop_reminder:
    #    return JsonResponse({'status': 'already stopped'}, status=200)

    #with transaction.atomic():
    #    reminder.stop_reminder = True
    #    reminder.save(update_fields=['stop_reminder'])

    return JsonResponse({'status': 'email reminder stopped'}, status=200)


@api_view(['GET'])
def get_not_qualified_conversations(request):

    two_weeks_ago = timezone.now() - timedelta(weeks=2)

    # =========================
    # EMAIL CONVERSATIONS (NOT QUALIFIED)
    # =========================
    email_not_qualified_conversations = (
        ConversationModel.objects
        .filter(
            source=ConversationSourceEnum.EMAIL,
            state=ConversationStateEnum.OPEN,
            created_at__gte=two_weeks_ago,
            prospect__isnull=False
        )
        .select_related('prospect')
        .prefetch_related('messages')
    )

    email_conversations = {}

    for conv in email_not_qualified_conversations:
        prospect_id = conv.prospect.id

        if prospect_id not in email_conversations:
            email_conversations[prospect_id] = {
                'prospect_id': prospect_id,
                'email': conv.prospect.email,
                'conversations': []
            }

        email_conversations[prospect_id]['conversations'].append({
            'conversation_id': conv.id,
            'created_at': conv.created_at,
            'messages': [
                {
                    'role': msg.role,
                    'message': msg.message,
                    'created_at': msg.created_at
                }
                for msg in conv.messages.all().order_by('created_at')
            ]
        })

    # =========================
    # CHAT QUALIFIED (WEB)
    # =========================
    chat_qualified_sessions = (
        ConversationModel.objects
        .filter(
            created_at__gte=two_weeks_ago,
            state=ConversationStateEnum.CLOSED,
        )
        .values_list('session_id', flat=True)
        .distinct()
    )

    # =========================
    # CHAT N8N NOT QUALIFIED
    # =========================
    n8n_not_qualified_qs = (
        ChatHistoryModel.objects
        .filter(
            created_at__gte=two_weeks_ago,
            session_id__isnull=False
        )
        .exclude(
            session_id__in=chat_qualified_sessions
        )
    )

    session_ids = (
        n8n_not_qualified_qs
        .values_list('session_id', flat=True)
        .distinct()
    )

    chat_conversations = []

    for session_id in session_ids:
        last_messages_qs = (
            ChatHistoryModel.objects
            .filter(session_id=session_id)
            .order_by('-created_at')[:20]
        )

        chat_conversations.append({
            'session_id': session_id,
            'messages': [
                {
                    'id': msg.id,
                    'role': msg.message.get('type'),
                    'message': msg.message.get('content'),
                    'created_at': msg.created_at
                }
                for msg in reversed(last_messages_qs)
            ]
        })

    # =========================
    # RESPONSE
    # =========================
    return JsonResponse({'email_conversations': list(email_conversations.values()), 'chat_conversations': chat_conversations}, status=200,)


@api_view(['POST'])
def save_not_qualified_lead_classification(request):

    primary_reason = request.data.get('primary_reason')
    drop_off_point = request.data.get('drop_off_point')
    source = request.data.get('source')
    product = request.data.get('product')

    if not all([primary_reason, drop_off_point, source, product]):
        return JsonResponse(
            {'error': 'primary_reason, drop_off_point, source, and product are required'},
            status=400
        )

    if primary_reason not in dict(NotQualifiedCategoryModel.ReasonChoices.choices):
        return JsonResponse(
            {'error': f'Invalid primary_reason: {primary_reason}'},
            status=400
        )

    if drop_off_point not in dict(NotQualifiedCategoryModel.CategoryChoices.choices):
        return JsonResponse(
            {'error': f'Invalid drop_off_point: {drop_off_point}'},
            status=400
        )

    if source not in dict(NotQualifiedCategoryModel.SourceChoices.choices):
        return JsonResponse(
            {'error': f'Invalid source: {source}'},
            status=400
        )

    with transaction.atomic():
        NotQualifiedCategoryModel.objects.create(
            reason=primary_reason,
            category=drop_off_point,
            source=source,
            product=product
        )

    return JsonResponse(
        {'message': 'Chat classification saved successfully'},
        status=201
    )
