from datetime import timedelta
import uuid

from django.core.serializers.json import DjangoJSONEncoder
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils import timezone
from django.urls import reverse

from ..base.models import BaseModel
from ..prospects.models import ConversationSourceEnum
from .agent_configuration import (
    DEFAULT_BUSINESS_UNIT_DESCRIPTION, DEFAULT_BUSINESS_UNIT_NAME,
    default_prompt_config, validate_prompt_config,
)


class AgentModel(BaseModel):
    """Catálogo y prompts de los agentes implementados en el proyecto."""

    class CategoryEnum(models.TextChoices):
        PROSPECTING = 'Prospección', 'Prospección'
        ANALYSIS = 'Análisis', 'Análisis'

    class ModeEnum(models.TextChoices):
        PRODUCTION = 'production', 'Producción'
        SHADOW_TESTING = 'shadow_testing', 'Pruebas (shadow testing)'

    key = models.SlugField('Identificador', max_length=100, unique=True)
    name = models.CharField('Nombre', max_length=100)
    is_active = models.BooleanField('Activo', default=True)
    role = models.CharField('Función', max_length=150)
    description = models.TextField('Descripción')
    category = models.CharField('Categoría', max_length=80, choices=CategoryEnum.choices, default=CategoryEnum.PROSPECTING)
    mode = models.CharField('Modo', max_length=20, choices=ModeEnum.choices, default=ModeEnum.PRODUCTION)
    avatar = models.CharField('Imagen', max_length=255, blank=True)
    channels = models.JSONField('Canales', default=list)
    learning_enabled = models.BooleanField('Puede aprender', default=False, blank=True)
    prompt = models.JSONField(
        'Configuración del prompt', default=default_prompt_config,
        validators=[validate_prompt_config],
    )

    class Meta:
        verbose_name = 'Agente'
        verbose_name_plural = 'Agentes'
        ordering = ('pk',)

    def __str__(self):
        return self.name

    def get_shadow_mode_block_reason(self, *, category=None, key=None):
        if (self.category if category is None else category) != self.CategoryEnum.PROSPECTING:
            return 'El modo de pruebas está disponible solo para agentes de prospección.'
        if (self.key if key is None else key) == 'prospector':
            return 'Este agente atiende en producción y no puede ponerse en modo de pruebas.'
        if self.is_active and self.mode == self.ModeEnum.PRODUCTION:
            return 'Desactiva este agente de producción antes de seleccionarlo para pruebas.'
        return ''

    @property
    def avatar_url(self):
        if not self.avatar:
            return ''
        if self.avatar.startswith('agent-avatars/'):
            return reverse('app_prospects:agent_avatar_file', args=[self.key])
        return ''

    def save(self, *args, **kwargs):
        if isinstance(self.prompt, dict) and self.prompt.get('name'):
            self.name = self.prompt['name'].strip()
            if kwargs.get('update_fields') and 'prompt' in kwargs['update_fields']:
                kwargs['update_fields'] = set(kwargs['update_fields']) | {'name'}
        super().save(*args, **kwargs)


class AgentChannelAssignment(models.Model):
    agent = models.ForeignKey(AgentModel, on_delete=models.CASCADE, related_name='channel_assignments')
    channel = models.CharField(max_length=20, choices=ConversationSourceEnum.choices)
    active = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=('agent', 'channel'), name='unique_agent_channel'),
            models.UniqueConstraint(fields=('channel',), condition=models.Q(active=True), name='unique_active_agent_channel'),
        ]
        ordering = ('channel',)


class LearningAnnotationModel(BaseModel):
    """Anotaciones de aprendizaje guardadas para cada agente."""

    MAX_ACTIVE_ANNOTATIONS = 10

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    is_active = models.BooleanField('Activo', default=True)
    observation = models.TextField('Observaciones', blank=True, default='')
    suggestion = models.TextField('Sugerencias')
    agent = models.ForeignKey(
        AgentModel, on_delete=models.CASCADE,
        related_name='learning_annotations', verbose_name='Agente',
    )

    class Meta:
        verbose_name = 'Anotación de aprendizaje'
        verbose_name_plural = 'Anotaciones de aprendizaje'
        ordering = ('-created_at',)

    def _validate_active_limit(self):
        if not self.is_active or not self.agent_id:
            return
        annotations = type(self).objects.filter(agent_id=self.agent_id, is_active=True)
        if annotations.filter(pk=self.pk).exists():
            return
        if annotations.count() >= self.MAX_ACTIVE_ANNOTATIONS:
            raise ValidationError({
                'is_active': ValidationError(
                    'Este agente ya tiene %(limit)s anotaciones activas. '
                    'Resume las activas en una sola para activar más.',
                    code='active_annotation_limit',
                    params={'limit': self.MAX_ACTIVE_ANNOTATIONS},
                ),
            })

    def clean(self):
        super().clean()
        if self.agent_id:
            with transaction.atomic():
                AgentModel.objects.select_for_update().filter(pk=self.agent_id).exists()
                self._validate_active_limit()

    def save(self, *args, **kwargs):
        with transaction.atomic():
            AgentModel.objects.select_for_update().filter(pk=self.agent_id).exists()
            self._validate_active_limit()
            return super().save(*args, **kwargs)


class WeeklyAnalysisBatchModel(BaseModel):
    """Lista fija y avance del análisis semanal de un agente."""

    class StatusEnum(models.TextChoices):
        PENDING = 'pending', 'Pendiente'
        RUNNING = 'running', 'En proceso'
        WAITING_RETRY = 'waiting_retry', 'Esperando reintento'
        SUCCESS = 'success', 'Completado'
        FAILED = 'failed', 'Fallido'

    agent = models.ForeignKey(AgentModel, on_delete=models.CASCADE, related_name='weekly_analyses')
    week_start = models.DateField()
    week_end = models.DateField()
    conversation_ids = models.JSONField(default=list)
    status = models.CharField(max_length=20, choices=StatusEnum.choices, default=StatusEnum.PENDING)
    phase = models.CharField(max_length=20, default='individual')
    final_attempts = models.PositiveSmallIntegerField(default=0)
    claim_token = models.UUIDField(null=True, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    final_input = models.JSONField(null=True, blank=True)
    final_result = models.JSONField(default=dict, blank=True)
    annotation = models.ForeignKey(
        LearningAnnotationModel, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='weekly_batches',
    )
    last_run = models.ForeignKey('AgentRunModel', null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        verbose_name = 'Lote de análisis semanal'
        verbose_name_plural = 'Lotes de análisis semanal'
        ordering = ('-week_start', 'agent_id')
        constraints = [models.UniqueConstraint(
            fields=('agent', 'week_start'), name='unique_weekly_analysis_agent',
        )]


class ConversationAnalysisModel(BaseModel):
    """Seguimiento del análisis y reintentos de una conversación por agente."""

    class StatusEnum(models.TextChoices):
        PENDING = 'pending', 'Pendiente'
        PROCESSING = 'processing', 'En proceso'
        SUCCESS = 'success', 'Analizada'
        FAILED = 'failed', 'Fallida'
        SKIPPED = 'skipped', 'Omitida'

    conversation = models.ForeignKey(
        'prospects.ConversationModel', on_delete=models.CASCADE,
        related_name='learning_analyses',
    )
    agent = models.ForeignKey(
        AgentModel, on_delete=models.CASCADE, related_name='conversation_analyses',
    )
    status = models.CharField(max_length=20, choices=StatusEnum.choices, default=StatusEnum.PENDING)
    fingerprint = models.CharField(max_length=64, blank=True)
    completed_fingerprint = models.CharField(max_length=64, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    claim_token = models.UUIDField(null=True, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    last_run = models.ForeignKey(
        'AgentRunModel', null=True, blank=True, on_delete=models.SET_NULL,
    )
    annotations_created = models.PositiveIntegerField(default=0)
    batch = models.ForeignKey(
        WeeklyAnalysisBatchModel, null=True, blank=True, on_delete=models.CASCADE,
        related_name='items',
    )
    position = models.PositiveIntegerField(default=0)
    result = models.JSONField(null=True, blank=True)

    class Meta:
        verbose_name = 'Análisis de conversación'
        verbose_name_plural = 'Seguimiento de análisis'
        ordering = ('-updated_at',)
        constraints = [
            models.UniqueConstraint(
                fields=('conversation', 'agent'), condition=models.Q(batch__isnull=True),
                name='unique_conversation_analysis_agent',
            ),
            models.UniqueConstraint(fields=('batch', 'conversation'), name='unique_batch_analysis_conversation'),
        ]
        indexes = [models.Index(fields=('status', 'next_attempt_at'))]


class AgentBusinessUnitModel(BaseModel):
    agent = models.ForeignKey(AgentModel, on_delete=models.CASCADE, related_name='business_units', verbose_name='Agente')
    name = models.CharField('Nombre', max_length=100)
    description = models.TextField('Descripción', max_length=4000)

    class Meta:
        verbose_name = 'Unidad de negocio'
        verbose_name_plural = 'Unidades de negocio'
        ordering = ('name', 'pk')
        constraints = [models.UniqueConstraint(fields=('agent', 'name'), name='unique_agent_business_unit_name')]

    def clean(self):
        super().clean()
        self.name = self.name.strip().upper()
        self.description = self.description.strip()
        if len(self.name) > 100:
            raise ValidationError({'name': 'El nombre admite hasta 100 caracteres.'})
        if self.name.replace('_', ' ') == 'NO DETERMINADO':
            self.name = DEFAULT_BUSINESS_UNIT_NAME
        if self.pk:
            previous = type(self).objects.using(self._state.db).filter(pk=self.pk).values('name', 'description', 'agent_id').first()
            if previous and previous['name'] == DEFAULT_BUSINESS_UNIT_NAME:
                errors = {
                    field: 'La unidad predeterminada NO_DETERMINADO no se puede modificar.'
                    for field, value in (('name', self.name), ('description', self.description), ('agent', self.agent_id))
                    if value != previous['agent_id' if field == 'agent' else field]
                }
                if errors:
                    raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


@receiver(post_save, sender=AgentModel)
def ensure_agent_default_unit(sender, instance, using, raw=False, **kwargs):
    if raw:
        return
    AgentBusinessUnitModel.objects.using(using).get_or_create(
        agent_id=instance.pk, name=DEFAULT_BUSINESS_UNIT_NAME,
        defaults={'description': DEFAULT_BUSINESS_UNIT_DESCRIPTION},
    )
    # La creación de la unidad sincroniza sus referencias; conserva también la instancia.
    instance.prompt = AgentModel.objects.using(using).values_list('prompt', flat=True).get(pk=instance.pk)


@receiver(post_save, sender=AgentBusinessUnitModel)
@receiver(post_delete, sender=AgentBusinessUnitModel)
def sync_business_unit_references(sender, instance, using, **kwargs):
    # El JSON referencia las unidades; sus nombres y descripciones viven en la tabla.
    with transaction.atomic(using=using):
        agent = AgentModel.objects.using(using).select_for_update().filter(pk=instance.agent_id).first()
        if agent is None:
            return
        identifiers = list(agent.business_units.using(using).values_list('pk', flat=True))
        config = dict(agent.prompt)
        if config.get('business_units') != identifiers:
            config['business_units'] = identifiers
            AgentModel.objects.using(using).filter(pk=agent.pk).update(prompt=config, updated_at=timezone.now())


class PendingRunModel(BaseModel):
    """
    Cola durable de corridas del pipeline para los canales que no tienen
    tabla propia: formulario y chat web.

    El correo no pasa por aqui. Su copia ya vive en ``email_messages``,
    que existia desde antes y guarda ademas el cuerpo y las cabeceras.

    Formulario y web si la necesitan: su contenido llega por POST y se va
    directo a la cola de Celery. Sin esta fila, un fallo del pipeline
    borraria el lead del mapa sin dejar una sola linea que consultar.
    Escribiendo aqui ANTES de encolar, cualquier cosa que entre al sistema
    queda registrada antes de tocar la IA.
    """

    class StatusEnum(models.TextChoices):
        PENDING = 'pending', 'Pending'
        """Recien llegado, o esperando su proximo intento."""

        PROCESSING = 'processing', 'Processing'
        PROCESSED = 'processed', 'Processed'

        INCOMPLETE = 'incomplete', 'Incomplete'
        """
        El lead SI quedo en Odoo, pero fallo el ultimo paso: avisarle al
        prospecto. No se reintenta -rehacerlo duplicaria la conversacion y
        volveria a escribir en Odoo- pero alguien deberia contestarle a mano.
        """

        ABANDONED = 'abandoned', 'Abandoned'
        """Se agotaron los intentos, o el error era de los que no se arreglan solos."""

    id = models.AutoField(primary_key=True)

    source = models.CharField(max_length=20)
    """``form`` o ``web``: decide bajo que llave entra el payload al ctx."""

    payload = models.JSONField(encoder=DjangoJSONEncoder)
    """Lo que llego por POST, tal cual, para reintentar con lo mismo."""

    identifier = models.CharField(max_length=255, blank=True)
    """Correo o session_id. Solo para poder buscar la fila a ojo."""

    status = models.CharField(
        max_length=20, choices=StatusEnum.choices, default=StatusEnum.PENDING)

    attempts = models.PositiveSmallIntegerField(default=0)
    """Cuantas veces se intento, contando la primera."""

    next_attempt_at = models.DateTimeField(null=True, blank=True)
    """Cuando toca el proximo intento. NULL significa que ya no se reintenta."""

    last_error = models.TextField(blank=True)
    """Clasificacion y mensaje del ultimo fallo."""

    class Meta:
        verbose_name = 'Pending run'
        verbose_name_plural = 'Pending runs'
        ordering = ('-created_at',)
        indexes = [models.Index(fields=['status', 'next_attempt_at'])]

    def __str__(self):
        return f'{self.id} - {self.source} - {self.identifier} ({self.status})'

    @classmethod
    def encolar(cls, source, payload, identifier='', gracia=60):
        """
        Registra la corrida y devuelve la fila, lista para encolar.

        ``next_attempt_at`` nace en el futuro a proposito: es la red de
        seguridad. Si el worker la procesa antes -lo normal, en segundos-
        nunca llega a usarse. Si el mensaje de Celery se pierde, el
        barrido la retoma pasado ese margen y el lead no se cae.
        """
        return cls.objects.create(
            source=source,
            payload=payload,
            identifier=(identifier or '')[:255],
            next_attempt_at=timezone.now() + timedelta(seconds=gracia),
        )


class AgentRunModel(models.Model):
    class StatusEnum(models.TextChoices):
        QUEUED = 'queued', 'Queued'
        RUNNING = 'running', 'Running'
        SUCCESS = 'success', 'Success'
        FAILED = 'failed', 'Failed'

    agent_name = models.CharField(max_length=100)
    mode = models.CharField(
        max_length=20, choices=AgentModel.ModeEnum.choices,
        default=AgentModel.ModeEnum.PRODUCTION,
    )
    conversation = models.ForeignKey(
        'prospects.ConversationModel', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='agent_runs'
    )
    production_run = models.ForeignKey(
        'self', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='shadow_runs',
    )
    input_snapshot = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    result = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    source = models.CharField(max_length=50, blank=True)
    status = models.CharField(
        max_length=20, choices=StatusEnum.choices, default=StatusEnum.RUNNING)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=['agent_name', 'status'])]
        constraints = [
            models.UniqueConstraint(
                fields=('production_run', 'agent_name'),
                condition=models.Q(mode='shadow_testing'),
                name='unique_shadow_agent_run',
            ),
        ]


class TestConversationModel(BaseModel):
    """
    Conversación de un agente de pruebas (shadow) junto a una conversación real.

    Vive en su propia tabla a propósito: tableros, estadísticas,
    recordatorios, Odoo y el chat del prospecto solo leen ``conversations``
    y ``chat_history``, así que nada de lo que se guarde aquí puede llegar
    a un cliente ni alterar las cifras reales.
    """

    agent = models.ForeignKey(
        AgentModel, on_delete=models.CASCADE,
        related_name='test_conversations', verbose_name='Agente de pruebas',
    )
    conversation = models.ForeignKey(
        'prospects.ConversationModel', on_delete=models.CASCADE,
        related_name='test_conversations', verbose_name='Conversación real',
    )

    class Meta:
        verbose_name = 'Conversación de pruebas'
        verbose_name_plural = 'Conversaciones de pruebas'
        ordering = ('-created_at', '-pk')
        constraints = [
            models.UniqueConstraint(
                fields=('agent', 'conversation'),
                name='unique_test_conversation_per_agent',
            ),
        ]

    def __str__(self):
        return f'{self.agent} · conversación {self.conversation_id}'


class TestMessageModel(BaseModel):
    """
    Un mensaje de la conversación de pruebas.

    Cada turno guarda el mensaje del prospecto y la respuesta que habría
    dado el agente de pruebas, ligados al mensaje real que los originó para
    compararlos con la respuesta de producción.
    """

    test_conversation = models.ForeignKey(
        TestConversationModel, on_delete=models.CASCADE, related_name='messages',
    )
    run = models.ForeignKey(
        AgentRunModel, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='test_messages',
    )
    source_message = models.ForeignKey(
        'prospects.ChatHistoryModel', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='test_messages',
    )
    """Mensaje real del prospecto que originó este turno."""

    message = models.JSONField(encoder=DjangoJSONEncoder)

    class Meta:
        verbose_name = 'Mensaje de pruebas'
        verbose_name_plural = 'Mensajes de pruebas'
        ordering = ('created_at', 'pk')


class AgentStepModel(models.Model):
    class StatusEnum(models.TextChoices):
        RUNNING = 'running', 'Running'
        SUCCESS = 'success', 'Success'
        FAILED = 'failed', 'Failed'

    run = models.ForeignKey(
        AgentRunModel, on_delete=models.CASCADE, related_name='steps')
    step_name = models.CharField(max_length=150)
    order = models.PositiveIntegerField()
    status = models.CharField(
        max_length=20, choices=StatusEnum.choices, default=StatusEnum.RUNNING)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    exception_type = models.CharField(max_length=200, blank=True)
    error_message = models.TextField(blank=True)
    traceback = models.TextField(blank=True)

    class Meta:
        ordering = ['order']
        unique_together = ('run', 'order')


class AgentEventModel(models.Model):
    """Bitácora de negocio: reintentos, decisiones, validaciones fallidas, etc.
    NO es para excepciones — eso va en AgentErrorModel."""

    class LevelEnum(models.TextChoices):
        DEBUG = 'debug', 'Debug'
        INFO = 'info', 'Info'
        WARNING = 'warning', 'Warning'

    run = models.ForeignKey(
        AgentRunModel, on_delete=models.CASCADE, related_name='events')
    step = models.ForeignKey(AgentStepModel, null=True, blank=True,
                             on_delete=models.CASCADE, related_name='events')
    level = models.CharField(
        max_length=10, choices=LevelEnum.choices, default=LevelEnum.INFO)
    message = models.CharField(max_length=500)
    data = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class EmailDeliveryLogModel(models.Model):
    """Un registro por intento de correo; SMTP aceptado no implica entrega final."""

    class StatusEnum(models.TextChoices):
        SENDING = 'sending', 'En proceso'
        SMTP_ACCEPTED = 'smtp_accepted', 'Aceptado por SMTP'
        FAILED = 'failed', 'Fallido'
        SKIPPED = 'skipped', 'Omitido'

    class KindEnum(models.TextChoices):
        RESPONSE = 'response', 'Respuesta'
        REMINDER = 'reminder', 'Recordatorio'
        DAILY_SUMMARY = 'daily_summary', 'Resumen diario'
        OTHER = 'other', 'Otro'

    conversation = models.ForeignKey(
        'prospects.ConversationModel', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='email_delivery_logs',
    )
    source = models.CharField('Canal', max_length=20, blank=True)
    recipient = models.CharField('Destinatario', max_length=320, blank=True)
    subject = models.CharField('Asunto', max_length=500, blank=True)
    kind = models.CharField('Tipo', max_length=20, choices=KindEnum.choices, default=KindEnum.OTHER)
    status = models.CharField('Estado', max_length=20, choices=StatusEnum.choices, default=StatusEnum.SENDING)
    message_id = models.CharField('Identificador del mensaje', max_length=255, blank=True, db_index=True)
    task_id = models.CharField('Identificador de tarea', max_length=255, blank=True)
    error_type = models.CharField('Tipo de error', max_length=200, blank=True)
    error_detail = models.TextField('Detalle', blank=True)
    created_at = models.DateTimeField('Inicio', auto_now_add=True)
    finished_at = models.DateTimeField('Fin', null=True, blank=True)

    class Meta:
        verbose_name = 'Intento de correo'
        verbose_name_plural = 'Intentos de correo'
        ordering = ('-created_at', '-pk')
        indexes = [
            models.Index(fields=['status', '-created_at'], name='email_log_status_date_idx'),
            models.Index(fields=['kind', '-created_at'], name='email_log_kind_date_idx'),
        ]

    @property
    def channel_label(self):
        from leadmanager.apps.prospects.models import ConversationSourceEnum
        return dict(ConversationSourceEnum.choices).get(self.source, self.source or 'Sin canal')

    def __str__(self):
        return f'{self.recipient or "Sin destinatario"} — {self.get_status_display()}'
