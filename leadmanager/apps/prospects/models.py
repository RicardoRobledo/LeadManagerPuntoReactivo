import uuid

from django.db import models
from django.db.models import Q, UniqueConstraint
from django.core.serializers.json import DjangoJSONEncoder

from ..base.models import BaseModel


__author__ = 'Ricardo'
__version__ = '0.1'


class ConversationSourceEnum(models.TextChoices):
    WEB = 'web', 'chatweb'
    FORM = 'form', 'Formulario'
    EMAIL = 'email', 'Correo'
    LINKEDIN = 'linkedin', 'LinkedIn'
    WHATSAPP = 'whatsapp', 'WhatsApp'
    MESSENGER = 'messenger', 'Messenger'
    INSTAGRAM = 'instagram', 'Instagram'


class EmailMessageStatusEnum(models.TextChoices):
    PENDING = 'pending', 'Pending'
    PROCESSING = 'processing', 'Processing'
    PROCESSED = 'processed', 'Processed'
    FAILED = 'failed', 'Failed'

    INCOMPLETE = 'incomplete', 'Incomplete'
    """
    El lead SI quedo en Odoo, pero fallo el ultimo paso: responderle al
    prospecto. No se reintenta -rehacerlo duplicaria la conversacion y
    volveria a escribir en Odoo- pero alguien deberia contestarle a mano.
    """


class EmailReminderEnum(models.TextChoices):
    FIRST = 'first', 'First'
    SECOND = 'second', 'Second'
    THIRD = 'third', 'Third'
    FOURTH = 'fourth', 'Fourth'


class ConversationStateEnum(models.TextChoices):
    OPEN = 'open', 'Open'
    CLOSED = 'closed', 'Closed'


class ProspectModel(BaseModel):

    id = models.AutoField(primary_key=True)
    email = models.EmailField(unique=True, null=True, blank=True)
    phone = models.CharField(max_length=50, null=True, blank=True)
    name = models.CharField(max_length=255, null=True, blank=True)
    function = models.CharField(max_length=50, null=True, blank=True)
    enterprise = models.CharField(max_length=255, null=True, blank=True)
    enterprise_summary = models.TextField(null=True, blank=True)
    person_id = models.IntegerField(unique=True, null=True, blank=True)

    class Meta:
        db_table = 'prospects'
        verbose_name = 'Prospect'
        verbose_name_plural = 'Prospects'

    def __str__(self):
        return f'{self.id} - {self.name}'

    def __repr__(self):
        return (
            f'ProspectModel(id={self.id}, name={self.name}, email={self.email}, function={self.function}, '
            f'enterprise={self.enterprise}, enterprise_summary={self.enterprise_summary}, phone={self.phone}, person_id={self.person_id}, '
            f'created_at={self.created_at}, updated_at={self.updated_at})'
        )


class EmailMessageModel(BaseModel):

    id = models.AutoField(primary_key=True)
    message_id = models.CharField(max_length=255, unique=True)
    thread_id = models.CharField(max_length=255, null=True, blank=True)
    email = models.EmailField()
    name = models.CharField(max_length=255, null=True, blank=True)
    to = models.CharField(max_length=255, null=True, blank=True)
    subject = models.CharField(max_length=500, null=True, blank=True)
    body = models.TextField()
    date = models.DateTimeField(null=True, blank=True)
    in_reply_to = models.CharField(max_length=255, null=True, blank=True)
    imap_uid = models.CharField(max_length=100, null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=EmailMessageStatusEnum.choices, default=EmailMessageStatusEnum.PENDING)

    attempts = models.PositiveSmallIntegerField(default=0)
    """Cuantas veces se intento procesar, contando la primera."""

    next_attempt_at = models.DateTimeField(null=True, blank=True)
    """Cuando toca el proximo intento. NULL significa que ya no se reintenta."""

    last_error = models.TextField(blank=True)
    """Clasificacion y mensaje del ultimo fallo."""

    raw = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder)
    """
    El payload del IMAP tal cual, para poder reintentar con exactamente lo
    mismo que vio el primer intento. Hace falta porque la tabla no guarda
    'references', y sin esa cabecera la respuesta pierde el hilo del correo.
    Lleva encoder porque 'date' viene como datetime.
    """

    class Meta:
        db_table = 'email_messages'
        verbose_name = 'Email Message'
        verbose_name_plural = 'Email Messages'

    def __str__(self):
        return (
            f'EmailMessageModel(id={self.id}, message_id={self.message_id}, '
            f'thread_id={self.thread_id}, email={self.email}, '
            f'name={self.name}, to={self.to}, subject={self.subject}, '
            f'date={self.date}, in_reply_to={self.in_reply_to}, '
            f'imap_uid={self.imap_uid}, status={self.status}, '
            f'created_at={self.created_at}, updated_at={self.updated_at})'
        )


class ConversationModel(BaseModel):

    id = models.AutoField(primary_key=True)
    session_id = models.CharField(max_length=255, null=True, blank=True)
    source = models.CharField(
        max_length=20,
        choices=ConversationSourceEnum.choices,
        null=True,
        blank=True,
    )
    prospect = models.ForeignKey(
        ProspectModel,
        on_delete=models.SET_NULL,
        related_name='conversations',
        null=True,
        blank=True,
    )
    state = models.CharField(
        max_length=10,
        choices=ConversationStateEnum.choices,
        default=ConversationStateEnum.OPEN,
    )
    web_message_count = models.PositiveIntegerField(default=0, editable=False)
    """Mensajes del visitante aceptados en esta conversación web, incluido el inicial."""

    email_reminder_phase = models.CharField(
        max_length=10,
        choices=EmailReminderEnum.choices,
        null=True,
        blank=True,
    )

    # ----------------------------------------------------------------
    # Atribución: de dónde llegó el visitante
    # ----------------------------------------------------------------
    # Los cinco UTM estándar, tal como los manda el sitio. Van en la
    # conversación y no en el prospecto porque una misma persona puede
    # llegar dos veces por campañas distintas, y colgarlos del prospecto
    # perdería la primera.
    #
    # Texto y no una tabla relacionada a propósito: son la foto de cómo
    # llegó esa visita, no un catálogo que se administre. Con llave
    # foránea, renombrar una campaña cambiaría retroactivamente el origen
    # de todas las conversaciones viejas, que para atribución está mal.
    #
    # Las conversaciones por correo los dejan vacíos, y está bien: un
    # correo entrante no tiene navegador ni URL de aterrizaje de donde
    # sacarlos. Ahí vacío significa "no aplica", no "se perdió el dato".
    #
    # 255 en todos porque utm_content y utm_term los generan las
    # plataformas de anuncios y pueden salir largos.

    utm_source = models.CharField(max_length=255, blank=True, default='')
    """De dónde viene: google, facebook, newsletter."""

    utm_medium = models.CharField(max_length=255, blank=True, default='')
    """Tipo de tráfico: cpc, email, social. Es el que distingue pagado de no."""

    utm_campaign = models.CharField(max_length=255, blank=True, default='')
    """Nombre de la campaña."""

    utm_content = models.CharField(max_length=255, blank=True, default='')
    """Qué anuncio o variante, cuando se prueban varios."""

    utm_term = models.CharField(max_length=255, blank=True, default='')
    """Palabra clave que pagó el clic."""

    traffic_origin = models.CharField(max_length=255, blank=True, default='')
    """Origen de tráfico que no es UTM, como "organic" o "referral". Puede ser útil para reportes."""

    class Meta:
        db_table = 'conversations'
        verbose_name = 'Conversation'
        verbose_name_plural = 'Conversations'
        constraints = [
            UniqueConstraint(
                fields=['session_id'],
                condition=Q(state='open'),
                name='unique_open_conversation_per_session',
            )
        ]
        indexes = [
            # Las dos agrupaciones del reporte "qué trae más leads", cada
            # una acotada por fecha. Los otros tres UTM se consultan
            # filtrando por estos dos, así que no llevan índice propio.
            models.Index(
                fields=['utm_source', 'created_at'],
                name='conv_utm_source_fecha_idx',
            ),
            models.Index(
                fields=['utm_campaign', 'created_at'],
                name='conv_utm_campana_fecha_idx',
            ),
        ]

    def __str__(self):
        return (
            f'ConversationModel(id={self.id}, '
            f'source={self.source}, '
            f'state={self.state}, '
            f'session_id={self.session_id}, '
            f'email_reminder_phase={self.email_reminder_phase}, '
            f'prospect_id={self.prospect_id}, '
            f'created_at={self.created_at}, '
            f'updated_at={self.updated_at})'
        )


class SellerModel(BaseModel):

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    user_id = models.IntegerField(unique=True)
    name = models.TextField(null=True, blank=True)
    email = models.TextField(null=True, blank=True)
    phone = models.TextField(null=True, blank=True)
    assigned_count = models.IntegerField(default=0)
    rotative = models.BooleanField(default=True)
    last_assigned_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'sellers'
        verbose_name = 'Seller'
        verbose_name_plural = 'Sellers'
        ordering = ('assigned_count', 'last_assigned_at')

    def __str__(self):
        return (
            f'SellerModel(id={self.id}, user_id={self.user_id}, '
            f'name={self.name}, email={self.email}, '
            f'assigned_count={self.assigned_count}, '
            f'rotative={self.rotative}, '
            f'last_assigned_at={self.last_assigned_at}, '
            f'created_at={self.created_at}, '
            f'updated_at={self.updated_at})'
        )


class ConversationEscalationModel(BaseModel):

    id = models.AutoField(primary_key=True)

    # El atributo será "session" y el valor de la columna
    # session_id estará disponible como self.session_id.
    session = models.ForeignKey(
        ConversationModel,
        on_delete=models.CASCADE,
        related_name='escalations',
        null=True,
        blank=True,
    )
    seller = models.ForeignKey(
        SellerModel,
        on_delete=models.SET_NULL,
        related_name='conversation_escalations',
        null=True,
        blank=True,
    )
    conversation_content = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = 'conversation_escalations'
        verbose_name = 'Conversation escalation'
        verbose_name_plural = 'Conversation escalations'
        ordering = ('-created_at',)

    def __str__(self):
        return (
            f'ConversationEscalationModel(id={self.id}, '
            f'seller_id={self.seller_id}, '
            f'conversation_content={self.conversation_content}, '
            f'created_at={self.created_at}, '
            f'updated_at={self.updated_at})'
        )


class ChatHistoryModel(BaseModel):

    id = models.AutoField(primary_key=True)
    conversation = models.ForeignKey(
        ConversationModel,
        on_delete=models.CASCADE,
        related_name='messages',
        null=True,
        blank=True,
    )
    message = models.JSONField()
    email_message = models.OneToOneField(
        EmailMessageModel,
        on_delete=models.SET_NULL,
        related_name='single_email_message',
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = 'Chat History'
        verbose_name_plural = 'Chat Histories'
        ordering = ('created_at',)

    def __str__(self):
        return (
            f'ChatHistoryModel(id={self.id}, '
            f'conversation_id={self.conversation_id}, '
            f'message={self.message}, '
            f'email_message={self.email_message}, '
            f'created_at={self.created_at}, '
            f'updated_at={self.updated_at})'
        )


class ReminderPhase(models.TextChoices):
    FIRST = 'FIRST', 'First'
    SECOND = 'SECOND', 'Second'
    THIRD = 'THIRD', 'Third'
    FOURTH = 'FOURTH', 'Fourth'


class MessengerReminderModel(BaseModel):

    class Meta:
        verbose_name = 'Messenger Reminder'
        verbose_name_plural = 'Messenger Reminders'

    sender_id = models.CharField(max_length=255)
    phase = models.CharField(
        max_length=10,
        choices=ReminderPhase.choices,
        default=ReminderPhase.FIRST,
    )
    sent = models.BooleanField(default=False)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    stop_reminder = models.BooleanField(default=False)

    def __str__(self):
        return f'MessengerReminderModel(id={self.id}, scheduled_at={self.scheduled_at}, stop_reminder={self.stop_reminder}, phase={self.phase}, created_at={self.created_at}, updated_at={self.updated_at})'


class NotQualifiedCategoryModel(BaseModel):

    class SourceChoices(models.TextChoices):
        CHAT = 'chat', 'Chat'
        EMAIL = 'email', 'Correo'

    class CategoryChoices(models.TextChoices):
        DATA_REQUEST = 'data_request', 'Data request'
        PRICE = 'price', 'Price'
        URGENCY = 'urgency', 'Urgency'
        TECHNICAL_REQUIREMENTS = 'technical_requirements', 'Technical requirements'
        CALL_TO_ACTION = 'call_to_action', 'Call to action'
        LONG_OR_COMPLEX_MESSAGE = 'long_or_complex_message', 'Long or complex message'
        SYSTEM_ERROR = 'system_error', 'System error'
        ABANDONMENT_WITH_NO_CLEAR_CAUSE = 'abandonment_with_no_clear_cause', 'Abandonment with no clear cause'

    class ReasonChoices(models.TextChoices):
        HIGH_PRICE = 'high_price', 'High price'
        LACK_OF_CLEAR_INFORMATION = 'lack_of_clear_information', 'Lack of clear information'
        LACK_OF_URGENCY = 'lack_of_urgency', 'Lack of urgency'
        INCOMPLETE_LEAD = 'incomplete_lead', 'Incomplete lead'
        EXPLORATION_ONLY = 'exploration_only', 'Exploration only'
        NEEDS_EXTERNAL_VALIDATION = 'needs_external_validation', 'Needs external validation'
        TECHNICAL_ISSUE = 'technical_issue', 'Technical issue'
        LEAD_ABANDONMENT = 'lead_abandonment', 'Lead abandonment'
        PRODUCT_MISMATCH = 'product_mismatch', 'Product mismatch'

    reason = models.CharField(
        max_length=64,
        choices=ReasonChoices.choices,
        default=ReasonChoices.HIGH_PRICE,
    )
    category = models.CharField(
        max_length=64,
        choices=CategoryChoices.choices,
        default=CategoryChoices.DATA_REQUEST,
    )
    source = models.CharField(
        choices=SourceChoices.choices,
        max_length=10,
        default=SourceChoices.CHAT,
    )
    product = models.CharField(max_length=255, null=True, blank=True)

    def __str__(self):
        return f'NotQualifiedCategoryModel(id={self.id}, reason={self.reason}, category={self.category}, product={self.product}, source={self.source}, created_at={self.created_at}, updated_at={self.updated_at})'
