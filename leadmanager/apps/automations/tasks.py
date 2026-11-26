import logging
import os
from datetime import timedelta
from zoneinfo import ZoneInfo

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from imapclient import IMAPClient, SEEN
from imapclient.exceptions import LoginError

from leadmanager.apps.prospects.models import (
    EmailMessageStatusEnum,
    EmailMessageModel,
    ChatHistoryModel,
    ConversationModel,
    ConversationEscalationModel,
    SellerModel,
    ConversationStateEnum,
    ConversationSourceEnum,
    EmailReminderEnum,
)
from leadmanager.apps.automations.models import (
    AgentEventModel, AgentModel, AgentRunModel,
    PendingRunModel, EmailDeliveryLogModel,
)
from leadmanager.apps.prospects.web_chat import WEB_CHAT_MESSAGE_LIMIT
from leadmanager.apps.prospects.qualification import filter_by_semaforo
from leadmanager.apps.prospects.crm_data import merge_crm_payload, parse_json_object

from .agents.agent_base import Agent, StepExecutionError
from .agents.prospector_agent.shadow_steps import SHADOW_STEPS
from .services.agent_execution import run_production_agent
from .services.conversation_analysis import analyzer_enabled
from .services.weekly_analysis import (
    prepare_weekly_batches, retry_due_batches, run_weekly_batch,
)
from .agents.error_policy import clasificar, REINTENTABLE, SIN_SALDO, PERMANENTE
from .services.imap_service import get_unread_emails, send_reminder_email
from .services.conversation_reminders import (
    latest_schema_emails, reminder_email_content, reminder_recipient, reminder_agent_names,
)
from .reminder_schedule import get_reminder_schedule, interval_delta
from .agents.prospector_agent.prospector_steps import revertir_turno
from .services.daily_summary import build_daily_report, send_daily_report
from .services.odoo_service import call as odoo_call, create_or_update_lead, find_person_by_source


logger = logging.getLogger(__name__)

# Cuanto esperar antes del proximo intento, segun la clasificacion del
# fallo. Lo que no esta aqui -PERMANENTE- no se reintenta: una api key
# mala o un prompt invalido fallarian exactamente igual.
ESPERA_POR_ACCION = {
    REINTENTABLE: 60,      # un minuto: estas caidas duran segundos
    SIN_SALDO: 600,        # diez: hasta que alguien pague, insistir es ruido
}

# Tope de intentos por mensaje, contando el primero. Con REINTENTABLE son
# unos 60 minutos de margen.
MAX_INTENTOS = 60

# Cuantos correos atrasados se reencolan por barrido, para no inundar la
# cola cuando OpenAI vuelve despues de una caida larga.
MAX_POR_BARRIDO = 20

# Una corrida que lleve mas de esto sin terminar es un worker que murio.
MINUTOS_PARA_DAR_POR_COLGADO = 15

REMINDER_SUBJECT, REMINDER_BODY = reminder_email_content(settings.EMAIL_FROM_NAME)

BLOCKED_SENDERS = {
    'mailer-daemon@googlemail.com',
}

# La parte local va aparte: MAILER-DAEMON@ llega de google, de outlook
# y de cualquier MTA, cada uno con su dominio.
BLOCKED_LOCAL_PARTS = {
    'mailer-daemon',
    'postmaster',
}


def is_automated_sender(correo):
    direccion = (correo.get('from', {}).get('email') or '').strip().lower()

    if not direccion:
        return False

    if direccion in BLOCKED_SENDERS:
        return True

    return direccion.split('@', 1)[0] in BLOCKED_LOCAL_PARTS


def _aplicar_politica(fila, exc, estado_en_espera, estado_rendido):
    """
    Anota en la fila el resultado de un fallo y decide cuando se reintenta.

    Sirve para las dos tablas de reintentos -EmailMessageModel y
    PendingRunModel-, que comparten las mismas cuatro columnas. Lo unico
    que cambia es como llama cada una a sus estados.

    ``next_attempt_at`` en NULL significa "ya no se reintenta": los
    barridos filtran por fecha y un NULL nunca cumple la condicion, asi
    que la fila se sale sola de la cola.

    Devuelve la clasificacion del fallo, para el log.
    """
    accion = clasificar(exc)
    espera = ESPERA_POR_ACCION.get(accion)

    fila.attempts += 1
    fila.last_error = '{accion}: {exc}'.format(accion=accion, exc=exc)[:2000]

    if espera is None or fila.attempts >= MAX_INTENTOS:
        fila.status = estado_rendido
        fila.next_attempt_at = None
    else:
        fila.status = estado_en_espera
        fila.next_attempt_at = timezone.now() + timedelta(seconds=espera)

    # updated_at va en la lista o no se escribe: con update_fields, Django
    # solo aplica el auto_now a los campos que le nombras.
    fila.save(update_fields=[
        'attempts', 'status', 'last_error', 'next_attempt_at', 'updated_at'])

    return accion


def _programar_reintento(message_id, exc):
    """
    Deja el correo listo para el proximo barrido, o lo da por perdido.

    El correo usa FAILED para los dos casos; lo que los separa es si
    ``next_attempt_at`` tiene fecha o quedo en NULL.
    """
    correo = EmailMessageModel.objects.filter(message_id=message_id).first()

    if correo is None:
        return clasificar(exc)

    return _aplicar_politica(
        correo, exc,
        EmailMessageStatusEnum.FAILED,
        EmailMessageStatusEnum.FAILED,
    )


def _lead_ya_escrito_en_odoo(ctx):
    """
    ¿La corrida alcanzo a cerrar la conversacion?

    create_or_update_lead_step es el que escribe en Odoo y, al terminar,
    deja la conversacion en CLOSED. Si la encontramos cerrada, ese paso ya
    corrio y lo unico que pudo fallar despues es avisarle al prospecto.

    Queda una ventana minima: si el step muere entre la escritura a Odoo y
    el cierre de la conversacion, esto devuelve False y el reintento si
    volveria a escribir. Cerrarla del todo pediria una transaccion que
    abarcara la llamada XML-RPC, que es justo lo que no conviene hacer.
    """
    conversation = ctx.get('conversation')

    if conversation is None:
        return False

    return ConversationModel.objects.filter(
        id=conversation.id, state=ConversationStateEnum.CLOSED
    ).exists()


def _cerrar_sin_reintento(fila, exc, paso, estado_incompleto):
    """
    Da la corrida por terminada aunque el ultimo paso fallara.

    Con la conversacion ya cerrada, el lead esta a salvo en Odoo y lo unico
    que fallo fue el aviso al prospecto. Reintentar el pipeline entero
    volveria a llamar a la IA, escribiria otra vez en Odoo y dejaria una
    segunda conversacion en la base, todo para reenviar un correo: sale mas
    caro el remedio. Se marca como incompleta y se anota por que, para que
    alguien conteste a mano.
    """
    fila.attempts += 1
    fila.status = estado_incompleto
    fila.next_attempt_at = None
    fila.last_error = (
        'sin reintento: el lead ya quedo en Odoo y solo fallo "{paso}". '
        'Falta avisarle al prospecto. Detalle: {exc}'
    ).format(paso=paso, exc=exc)[:2000]

    fila.save(update_fields=[
        'attempts', 'status', 'last_error', 'next_attempt_at', 'updated_at'])


@shared_task(ignore_result=True)
def process_shadow_run(run_id):
    """Consume una copia del turno; nunca envía ni reintenta la atención real."""
    claimed = AgentRunModel.objects.filter(
        pk=run_id, mode=AgentModel.ModeEnum.SHADOW_TESTING,
        status=AgentRunModel.StatusEnum.QUEUED,
    ).update(status=AgentRunModel.StatusEnum.RUNNING, started_at=timezone.now())
    if not claimed:
        return
    run = AgentRunModel.objects.select_related('conversation').get(pk=run_id)
    try:
        enabled = AgentModel.objects.filter(
            key=run.agent_name, category=AgentModel.CategoryEnum.PROSPECTING,
            mode=AgentModel.ModeEnum.SHADOW_TESTING,
            channel_assignments__channel=run.source,
        ).exists()
        if not enabled:
            raise ValueError('El agente ya no está en pruebas para este canal.')
        ctx = dict(run.input_snapshot)
        ctx['conversation'] = run.conversation
        ctx['_production_run_id'] = run.production_run_id
        agent = Agent(
            name=run.agent_name, mode=AgentModel.ModeEnum.SHADOW_TESTING,
            shadow_steps=SHADOW_STEPS,
        )
        agent.run(ctx, run=run)
    except Exception as exc:
        AgentRunModel.objects.filter(pk=run_id).update(
            status=AgentRunModel.StatusEnum.FAILED, finished_at=timezone.now(),
            result={'error': str(exc)},
        )
        AgentEventModel.objects.create(
            run_id=run_id, level=AgentEventModel.LevelEnum.WARNING,
            message='La prueba falló; la atención real no se modifica.',
            data={'error': str(exc)},
        )
        logger.exception('Falló el run shadow %s; producción continúa.', run_id)


@shared_task(name='analyze_conversations')
def analyze_conversations():
    """Procesa la semana anterior, de lunes a lunes con fin exclusivo.

    Ejecutarla de nuevo reutiliza el mismo lote: los análisis completados no se
    repiten y un lote terminado no genera otra anotación.
    """
    zona = ZoneInfo('America/Mexico_City')
    ahora = timezone.now().astimezone(zona)
    hoy = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
    fin = hoy - timedelta(days=ahora.weekday())
    inicio = fin - timedelta(days=7)
    result = {
        'inicio': inicio.isoformat(),
        'fin_exclusivo': fin.isoformat(),
        'zona_horaria': zona.key,
        'total_conversaciones': 0,
        'analizadas': 0,
        'omitidas': 0,
        'fallidas': 0,
        'anotaciones_creadas': 0,
        'sin_cambios': 0,
        'lotes_total': 0,
        'lotes_completados': 0,
    }
    if not analyzer_enabled():
        result['motivo_omision'] = 'analizador_inactivo'
        logger.info('analyze_conversations: el agente de análisis no está activo.')
        return result

    batches = prepare_weekly_batches(inicio, fin)
    result['lotes_total'] = len(batches)
    result['total_conversaciones'] = sum(len(batch.conversation_ids) for batch in batches)
    for batch in batches:
        outcome = run_weekly_batch(batch.pk)
        for key in ('analizadas', 'omitidas', 'fallidas', 'anotaciones_creadas'):
            result[key] += outcome[key]
        if outcome['status'] == 'success':
            result['lotes_completados'] += 1

    logger.info('analyze_conversations: %s', result)
    return result


@shared_task(name='retry_conversation_analyses')
def retry_conversation_analyses():
    """Retoma lotes pausados, respetando el orden y los pasos completados."""
    result = retry_due_batches()
    logger.info('retry_conversation_analyses: %s', result)
    return result


@shared_task(name='resumir-final-dia')
def resumir_final_dia():
    """Analiza las conversaciones de ayer y envía su resumen por correo."""
    zona = ZoneInfo('America/Mexico_City')
    ahora = timezone.now().astimezone(zona)
    inicio = ahora.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    limite = inicio + timedelta(days=1)
    fin = limite - timedelta(microseconds=1)

    logger.info('resumir-final-dia: rango %s a %s', inicio, fin)

    # El límite exclusivo deja fuera la medianoche del día actual.
    conversaciones = ConversationModel.objects.filter(
        created_at__gte=inicio,
        created_at__lt=limite,
    ).order_by('created_at', 'id')

    report = build_daily_report(conversaciones, inicio, fin, zona)

    total = 0
    for total, conversacion in enumerate(report['conversaciones'], start=1):
        logger.info(
            'resumir-final-dia [%s]: id=%s | canal=%s | '
            'estado=%s | creada=%s | unidad=%s',
            total,
            conversacion['id'],
            conversacion['canal'],
            conversacion['estado'],
            conversacion['creada'],
            conversacion['unidad_negocio'],
        )

    logger.info(
        'resumir-final-dia: total de conversaciones del %s: %s',
        inicio.date(), total,
    )

    recipient = send_daily_report(report)
    logger.info('resumir-final-dia: resumen enviado a %s', recipient)

    # El resultado usa ISO 8601 para poder serializarlo con JSON en Celery.
    return {
        'fecha': inicio.date().isoformat(),
        'zona_horaria': zona.key,
        'inicio': inicio.isoformat(),
        'fin': fin.isoformat(),
        'total_conversaciones': total,
        'estadisticas': report['estadisticas'],
        'correo_enviado': True,
        'destinatario': recipient,
    }


@shared_task(bind=True)
def retrieve_emails(self):
    """
    Polls the inbox for unread emails, records them and queues each one
    for background processing.
    """

    logger.info("Checking for unread emails...")

    try:

        correos = get_unread_emails()

        if not correos:
            logger.info("No new emails.")
            return {"procesados": 0}

        # Reopen the connection to flag them as read once queued.
        server = IMAPClient(
            settings.IMAP_HOST,
            port=settings.IMAP_PORT,
            ssl=True
        )

        encolados = 0

        try:

            server.login(settings.IMAP_USER, settings.IMAP_PASS)
            server.select_folder("INBOX")

            uids_ignorados = []
            correos_validos = []

            for correo in correos:
                if is_automated_sender(correo):
                    logger.info("Ignorando correo automático de %s (UID %s).",
                                correo.get('from', {}).get('email', ''), correo['_uid'])
                    uids_ignorados.append(correo['_uid'])
                else:
                    correos_validos.append(correo)

            correos = correos_validos

            EmailMessageModel.objects.bulk_create(
                [
                    EmailMessageModel(
                        email=correo.get("from", {}).get("email", ""),
                        name=correo.get("from", {}).get("name", ""),
                        to=correo.get("to"),
                        message_id=correo.get("message_id"),
                        thread_id=correo.get("thread_id"),
                        subject=correo.get("subject"),
                        body=correo.get("body"),
                        date=correo.get("date"),
                        in_reply_to=correo.get("in_reply_to"),
                        imap_uid=correo.get("_uid"),
                        status=EmailMessageStatusEnum.PENDING,
                        raw=correo,
                    )
                    for correo in correos
                ],
                ignore_conflicts=True
            )

            logger.info("Stored %s email(s) in the database.", len(correos))

            uids_encolados = []

            for correo in correos:
                uid = correo["_uid"]
                try:
                    logger.info(
                        "Queueing email UID %s for background processing.", uid)
                    process_email.delay(correo['message_id'], correo)
                    encolados += 1
                    uids_encolados.append(uid)
                except Exception:
                    logger.exception("Error queueing UID %s", uid)

            # Flagged as read on queueing, not after processing: a failed
            # pipeline shows up in the EmailMessageModel status (FAILED),
            # not in the IMAP flag. This keeps the next beat run from
            # picking the same emails up again.
            #
            # Los ignorados se marcan también: no se procesan, pero si no
            # quedan como leídos el beat los vuelve a bajar en cada vuelta.
            uids_a_marcar = uids_encolados + uids_ignorados

            if uids_a_marcar:
                try:
                    server.add_flags(uids_a_marcar, [SEEN])
                    logger.info("Flagged %s email(s) as read.",
                                len(uids_a_marcar))
                except Exception:
                    logger.exception(
                        "Could not flag UIDs %s as read", uids_a_marcar)

        finally:
            try:
                server.logout()
            except Exception:
                pass

        logger.info("Queued %s email(s).", encolados)

        return {"procesados": encolados}

    except LoginError:
        logger.exception("IMAP credentials rejected.")
        raise


@shared_task(bind=True)
def process_email(self, message_id, email):
    """
    Runs the agent pipeline over an incoming email.

    Si falla, revertir_turno deshace lo que la corrida escribio en el
    historial y _programar_reintento deja la fila lista para que
    reprocess_failed_emails la retome. El reintento sale de la base y no
    del broker, asi sobrevive a un reinicio del worker.
    """

    EmailMessageModel.objects.filter(
        message_id=message_id
    ).update(
        status=EmailMessageStatusEnum.PROCESSING,
        # .update() no dispara el auto_now, y el rescate de corridas
        # colgadas mide justo con esta fecha.
        updated_at=timezone.now(),
    )

    ctx = {"email": email, "source": "email", "history": []}

    try:
        run_production_agent(ctx)

    except StepExecutionError as e:
        revertidos = revertir_turno(ctx)

        if _lead_ya_escrito_en_odoo(ctx):
            correo = EmailMessageModel.objects.filter(
                message_id=message_id).first()
            if correo is not None:
                _cerrar_sin_reintento(
                    correo, e.original_exception, e.step_name,
                    EmailMessageStatusEnum.INCOMPLETE)
            logger.error(
                "Lead ya en Odoo, solo fallo '%s' message_id=%s: NO se "
                "reintenta, hay que responderle a mano.",
                e.step_name, message_id)
            return

        accion = _programar_reintento(message_id, e.original_exception)
        logger.exception(
            "Pipeline '%s' failed on step '%s' message_id=%s (%s, revertidos=%s)",
            ctx.get('_production_agent_key', 'prospector'), e.step_name, message_id, accion, revertidos,
        )
        raise

    except Exception as e:
        # any other error outside a step: ...
        revertidos = revertir_turno(ctx)
        accion = _programar_reintento(message_id, e)
        logger.exception(
            "Unexpected error processing message_id=%s (%s, revertidos=%s)",
            message_id, accion, revertidos)
        raise

    EmailMessageModel.objects.filter(
        message_id=message_id
    ).update(
        status=EmailMessageStatusEnum.PROCESSED,
        next_attempt_at=None,
        updated_at=timezone.now(),
    )


@shared_task(bind=True)
def reprocess_failed_emails(self):
    """
    Reencola los correos que fallaron y ya cumplieron su espera.

    El reintento sale de la base y no del IMAP porque retrieve_emails marca
    el UID como leido en cuanto lo encola: el buzon ya no lo ofrece. Que el
    estado viva en Postgres tiene una ventaja sobre dejarlo en el broker:
    sobrevive a un reinicio del worker y a un Redis limpio.
    """

    ahora = timezone.now()

    # Rescate: si el worker murio a media corrida, la fila se quedo en
    # PROCESSING y ningun filtro la vuelve a mirar. Se devuelve a FAILED
    # para que el barrido de abajo la retome.
    colgados = EmailMessageModel.objects.filter(
        status=EmailMessageStatusEnum.PROCESSING,
        updated_at__lte=ahora - timedelta(minutes=MINUTOS_PARA_DAR_POR_COLGADO),
    ).update(
        status=EmailMessageStatusEnum.FAILED,
        next_attempt_at=ahora,
        updated_at=ahora,
    )

    if colgados:
        logger.warning(
            "Rescatados %s correo(s) colgados en PROCESSING.", colgados)

    pendientes = EmailMessageModel.objects.filter(
        status=EmailMessageStatusEnum.FAILED,
        attempts__lt=MAX_INTENTOS,
        next_attempt_at__lte=ahora,
    ).order_by('next_attempt_at')[:MAX_POR_BARRIDO]

    reencolados = 0

    for correo in pendientes:

        if not correo.raw:
            # Correo anterior a la columna raw: no hay con que reintentarlo.
            EmailMessageModel.objects.filter(pk=correo.pk).update(
                next_attempt_at=None,
                last_error='sin payload guardado, no se puede reintentar',
                updated_at=ahora,
            )
            continue

        # La fecha se empuja ANTES de encolar: si el beat vuelve a correr
        # mientras el worker todavia no lo toma, no se encola dos veces. Y
        # si el mensaje se perdiera, el barrido lo retoma en un minuto.
        EmailMessageModel.objects.filter(pk=correo.pk).update(
            next_attempt_at=ahora + timedelta(
                seconds=ESPERA_POR_ACCION[REINTENTABLE]),
            updated_at=ahora,
        )

        process_email.delay(correo.message_id, correo.raw)
        reencolados += 1

    if reencolados:
        logger.info("Reencolados %s correo(s) fallidos.", reencolados)

    return {'reencolados': reencolados, 'rescatados': colgados}


@shared_task(bind=True)
def process_pending_run(self, pending_id):
    """
    Corre el pipeline sobre una fila de PendingRunModel: formulario o web.

    No usa ``self.retry``: el reintento lo maneja reprocess_pending_runs
    leyendo la tabla, igual que con el correo. Asi hay un solo mecanismo,
    visible con un SELECT, que sobrevive a reinicios del worker y a un
    Redis vacio.
    """

    fila = PendingRunModel.objects.filter(pk=pending_id).first()

    if fila is None:
        logger.warning("PendingRun %s ya no existe, se ignora.", pending_id)
        return

    if fila.status == PendingRunModel.StatusEnum.PROCESSED:
        # El broker puede entregar el mismo mensaje dos veces; sin esto,
        # el prospecto recibiria dos respuestas y pagariamos la IA dos veces.
        logger.info("PendingRun %s ya estaba procesado, se ignora.", pending_id)
        return

    PendingRunModel.objects.filter(pk=fila.pk).update(
        status=PendingRunModel.StatusEnum.PROCESSING,
        # .update() no dispara el auto_now, y el rescate de corridas
        # colgadas mide justo con esta fecha.
        updated_at=timezone.now(),
    )

    # La llave del ctx es el propio source: el pipeline lee ctx['form'] o
    # ctx['web'] segun de donde venga.
    ctx = {fila.source: fila.payload, "source": fila.source, "history": []}

    try:
        run_production_agent(ctx)

    except StepExecutionError as e:
        revertidos = revertir_turno(ctx)

        if _lead_ya_escrito_en_odoo(ctx):
            _cerrar_sin_reintento(
                fila, e.original_exception, e.step_name,
                PendingRunModel.StatusEnum.INCOMPLETE)
            logger.error(
                "Lead ya en Odoo, solo fallo '%s' (%s id=%s): NO se "
                "reintenta, hay que responderle a mano.",
                e.step_name, fila.source, fila.pk)
            return

        accion = _aplicar_politica(
            fila, e.original_exception,
            PendingRunModel.StatusEnum.PENDING,
            PendingRunModel.StatusEnum.ABANDONED,
        )
        logger.exception(
            "Pipeline '%s' falló en step '%s' (%s id=%s, %s, revertidos=%s)",
            ctx.get('_production_agent_key', 'prospector'), e.step_name, fila.source, fila.pk,
            accion, revertidos,
        )
        raise

    except Exception as e:
        revertidos = revertir_turno(ctx)
        accion = _aplicar_politica(
            fila, e,
            PendingRunModel.StatusEnum.PENDING,
            PendingRunModel.StatusEnum.ABANDONED,
        )
        logger.exception(
            "Error inesperado fuera de los steps (%s id=%s, %s, revertidos=%s)",
            fila.source, fila.pk, accion, revertidos)
        raise

    if fila.source == ConversationSourceEnum.WEB:
        ConversationModel.objects.filter(
            pk=fila.payload.get('_conversation_id'),
            source=ConversationSourceEnum.WEB,
            web_message_count__gte=WEB_CHAT_MESSAGE_LIMIT,
        ).update(state=ConversationStateEnum.CLOSED)

    PendingRunModel.objects.filter(pk=fila.pk).update(
        status=PendingRunModel.StatusEnum.PROCESSED,
        next_attempt_at=None,
        updated_at=timezone.now(),
    )


@shared_task(bind=True)
def reprocess_pending_runs(self):
    """
    Reencola las corridas de formulario y web que toca reintentar.

    Es el gemelo de reprocess_failed_emails, sobre la otra tabla. Tambien
    recoge las que nunca llegaron a ejecutarse: la vista las crea con un
    ``next_attempt_at`` a futuro, asi que si el mensaje de Celery se
    perdio, este barrido las levanta pasado ese margen.
    """

    ahora = timezone.now()

    colgados = PendingRunModel.objects.filter(
        status=PendingRunModel.StatusEnum.PROCESSING,
        updated_at__lte=ahora - timedelta(minutes=MINUTOS_PARA_DAR_POR_COLGADO),
    ).update(
        status=PendingRunModel.StatusEnum.PENDING,
        next_attempt_at=ahora,
        updated_at=ahora,
    )

    if colgados:
        logger.warning(
            "Rescatadas %s corrida(s) colgadas en PROCESSING.", colgados)

    pendientes = PendingRunModel.objects.filter(
        status=PendingRunModel.StatusEnum.PENDING,
        attempts__lt=MAX_INTENTOS,
        next_attempt_at__lte=ahora,
    ).order_by('next_attempt_at')[:MAX_POR_BARRIDO]

    reencoladas = 0

    for fila in pendientes:

        # La fecha se empuja ANTES de encolar: si el beat vuelve a correr
        # mientras el worker todavia no la toma, no se encola dos veces. Y
        # si el mensaje se perdiera, el barrido la retoma en un minuto.
        PendingRunModel.objects.filter(pk=fila.pk).update(
            next_attempt_at=ahora + timedelta(
                seconds=ESPERA_POR_ACCION[REINTENTABLE]),
            updated_at=ahora,
        )

        process_pending_run.delay(fila.pk)
        reencoladas += 1

    if reencoladas:
        logger.info("Reencoladas %s corrida(s) pendientes.", reencoladas)

    return {'reencoladas': reencoladas, 'rescatadas': colgados}


@shared_task(bind=True)
def rescue_stuck_agent_runs(self):
    """
    Marca como FAILED los runs que llevan demasiado tiempo en RUNNING.

    Si el worker muere a media corrida, AgentRunModel se queda en RUNNING
    para siempre. WebChatView rechaza POSTs nuevos mientras haya uno
    RUNNING para esa sesion, asi que un run colgado deja al visitante sin
    poder enviar nada mas.
    """

    limite = timezone.now() - timedelta(minutes=MINUTOS_PARA_DAR_POR_COLGADO)

    colgados = AgentRunModel.objects.filter(
        status=AgentRunModel.StatusEnum.RUNNING,
        started_at__lte=limite,
    ).update(
        status=AgentRunModel.StatusEnum.FAILED,
        finished_at=timezone.now(),
    )

    if colgados:
        logger.warning("Marcados %s run(s) colgados como FAILED.", colgados)

    return {'rescatados': colgados}


def _guardar_escalamientos_recordatorio(exitos):
    """Confirma en un lote el vendedor, el semáforo y los marcadores de Odoo."""
    if not exitos:
        return 0
    seller_names = {
        seller_id: resultado.get('seller_name') or (
            settings.ODOO_SELLER_NAME if seller_id == settings.ODOO_SELLER_ID else None
        )
        for _, _, resultado, seller_id in exitos
    }
    with transaction.atomic():
        sellers = SellerModel.objects.in_bulk(seller_names, field_name='user_id')
        SellerModel.objects.bulk_create([
            SellerModel(user_id=seller_id, name=name)
            for seller_id, name in seller_names.items() if seller_id not in sellers
        ], ignore_conflicts=True, batch_size=50)
        sellers = SellerModel.objects.in_bulk(seller_names, field_name='user_id')
        conversaciones = []
        nuevos_analisis = []
        escalaciones = []
        for recordatorio, escalado, resultado, seller_id in exitos:
            conv = recordatorio.conversation
            message = dict(recordatorio.message)
            message['odoo_reminder_sync'] = {
                'lead_id': resultado['lead_id'],
                'person_id': resultado.get('person_id'),
                'seller_id': seller_id,
            }
            recordatorio.message = message
            conv.state = ConversationStateEnum.CLOSED
            conversaciones.append(conv)
            content = {'crm_update': escalado, 'conversation_state': 'qualified'}
            nuevos_analisis.append(ChatHistoryModel(
                conversation=conv, message={'role': 'assistant', 'content': content},
            ))
            escalaciones.append(ConversationEscalationModel(
                session=conv, seller=sellers[seller_id],
                conversation_content=content,
            ))
        ChatHistoryModel.objects.bulk_update(
            [row for row, _, _, _ in exitos], ['message'], batch_size=50,
        )
        ConversationModel.objects.bulk_update(
            conversaciones, ['state'], batch_size=50,
        )
        ChatHistoryModel.objects.bulk_create(nuevos_analisis, batch_size=50)
        ConversationEscalationModel.objects.bulk_create(escalaciones, batch_size=50)
    for recordatorio, _, resultado, seller_id in exitos:
        logger.info('Recordatorio escalado en Odoo: conversacion_id=%s | lead_id=%s | vendedor_id=%s',
                    recordatorio.conversation_id, resultado['lead_id'], seller_id)
    return len(exitos)


@shared_task(bind=True)
def send_reminders_to_stale_conversations(self):
    """
    Envía un recordatorio por correo a prospectos en proceso de cualquier canal.

    Requiere semáforo amarillo y que venza el intervalo configurado para Nada → FIRST.
    Usa el correo del análisis CRM; prospect.email y el identificador del canal
    correo o formulario sirven como respaldo para datos antiguos o incompletos.
    Registra los envíos aceptados por lotes. Después asigna el vendedor en
    Odoo y escala a verde solo los leads sincronizados. Si Odoo falla, el
    siguiente ciclo reintenta sin reenviar el correo.
    """

    schedule = get_reminder_schedule()
    first_interval = next((item for item in schedule if item.get('from') == 'none' and item.get('to') == 'first' and item.get('enabled', True)), None)
    if first_interval is None:
        logger.info('Recordatorios: transición Nada → FIRST desactivada en la configuración.')
        return {'procesados': 0}
    cutoff = timezone.now() - interval_delta(first_interval)

    scope = ConversationModel.objects.all()
    antiguas = scope.filter(created_at__lte=cutoff)
    pendientes = antiguas.filter(email_reminder_phase__isnull=True)
    # Comparte la clasificación comercial con el filtro de Conversations.
    qs = filter_by_semaforo(pendientes, 'amarillo').select_related('prospect')
    candidatos = qs.count()
    schema_emails = latest_schema_emails(qs)
    names_by_channel, default_agent_name = reminder_agent_names()
    estados_anteriores = EmailDeliveryLogModel.objects.filter(
        conversation__in=qs,
        kind=EmailDeliveryLogModel.KindEnum.REMINDER,
    ).values_list('conversation_id', 'status')
    aceptados_anteriores = set()
    no_reintentables = set()
    for conversation_id, status in estados_anteriores:
        if status == EmailDeliveryLogModel.StatusEnum.SMTP_ACCEPTED:
            aceptados_anteriores.add(conversation_id)
        elif status in (
            EmailDeliveryLogModel.StatusEnum.SKIPPED,
            EmailDeliveryLogModel.StatusEnum.SENDING,
        ):
            no_reintentables.add(conversation_id)
    logger.info(
        'Recordatorios: canales=todos | limite_configurado=%s | '
        'total=%s | con_48h=%s | sin_recordatorio=%s | en_proceso=%s',
        cutoff.isoformat(),
        scope.count(), antiguas.count(), pendientes.count(), candidatos,
    )

    procesados = 0
    sin_correo = 0
    actualizados = []
    historial = []
    omitidos = []
    reminder_message = {
        'role': 'assistant',
        'content': 'Envío de recordatorio: correo aceptado previamente; contenido no disponible.'
    }

    def guardar_envios():
        if not actualizados and not omitidos:
            return
        # Cada lote conserva juntos la fase y el historial del correo aceptado.
        with transaction.atomic():
            if actualizados:
                ConversationModel.objects.bulk_update(
                    actualizados, ['email_reminder_phase'], batch_size=50,
                )
                ChatHistoryModel.objects.bulk_create(historial, batch_size=50)
            if omitidos:
                EmailDeliveryLogModel.objects.bulk_create(omitidos, batch_size=50)
        actualizados.clear()
        historial.clear()
        omitidos.clear()

    for conv in qs:

        if conv.pk in aceptados_anteriores:
            # Una corrida previa pudo detenerse después de SMTP y antes del
            # bulk_update. La bitácora aceptada impide repetir el correo.
            conv.email_reminder_phase = EmailReminderEnum.FIRST
            actualizados.append(conv)
            historial.append(ChatHistoryModel(
                conversation=conv, message=dict(reminder_message),
            ))
            if len(actualizados) >= 50:
                guardar_envios()
            continue

        if conv.pk in no_reintentables:
            # Un omitido ya tiene una decisión final. Un intento en proceso
            # tampoco se puede reenviar sin saber si SMTP lo aceptó.
            continue

        _, reminder_body = reminder_email_content(names_by_channel.get(conv.source, default_agent_name))
        destinatario, origen = reminder_recipient(conv, schema_emails.get(conv.pk))

        if not destinatario:
            sin_correo += 1
            omitidos.append(EmailDeliveryLogModel(
                recipient='', subject=REMINDER_SUBJECT,
                kind=EmailDeliveryLogModel.KindEnum.REMINDER,
                status=EmailDeliveryLogModel.StatusEnum.SKIPPED,
                conversation=conv, source=conv.source or '',
                task_id=str(getattr(self.request, 'id', '') or '')[:255],
                error_detail='No hay correo válido en el análisis CRM, el prospecto ni el identificador del canal correo o formulario.',
                finished_at=timezone.now(),
            ))
            logger.warning(
                'Recordatorio omitido: conversacion_id=%s | canal=%s | '
                'motivo=sin correo válido en análisis CRM, prospecto ni respaldo del canal',
                conv.pk, conv.source,
            )
            if len(omitidos) >= 50:
                guardar_envios()
            continue

        if not send_reminder_email(
            destinatario, REMINDER_SUBJECT, reminder_body,
            conversation_id=conv.pk, source=conv.source,
        ):
            logger.warning(
                'Recordatorio fallido: conversacion_id=%s | canal=%s | destinatario=%s. '
                'Se conserva pendiente para el próximo ciclo.',
                conv.pk, conv.source, destinatario,
            )
            continue

        logger.info(
            'Recordatorio aceptado por SMTP: conversacion_id=%s | canal=%s | '
            'destinatario=%s | origen=%s',
            conv.pk, conv.source, destinatario, origen,
        )

        conv.email_reminder_phase = EmailReminderEnum.FIRST
        actualizados.append(conv)
        historial.append(ChatHistoryModel(
            conversation=conv, message={'role': 'assistant', 'content': 'Envío de recordatorio:' + reminder_body},
        ))
        procesados += 1
        if len(actualizados) >= 50:
            guardar_envios()

    guardar_envios()

    sincronizados = 0
    fallidos_odoo = 0
    enviados = filter_by_semaforo(
        scope.filter(email_reminder_phase=EmailReminderEnum.FIRST), 'amarillo',
    ).select_related('prospect')
    # El marcador se guarda en el recordatorio y la evaluación verde en un
    # análisis separado; así ambos cambios se confirman juntos en la base.
    recordatorios = (
        ChatHistoryModel.objects
        .filter(conversation__in=enviados,
                message__content__startswith='Envío de recordatorio:')
        .select_related('conversation', 'conversation__prospect')
        .order_by('conversation_id', '-created_at', '-id')
    )
    revisadas = set()
    por_sincronizar = []
    for recordatorio in recordatorios:
        conv = recordatorio.conversation
        if conv.pk in revisadas:
            continue
        revisadas.add(conv.pk)
        marker = recordatorio.message.get('odoo_reminder_sync')
        if not isinstance(marker, dict) or not marker.get('seller_id'):
            por_sincronizar.append(recordatorio)

    ids = [recordatorio.conversation_id for recordatorio in por_sincronizar]
    crm_updates = {}
    for conversation_id, message in (
        ChatHistoryModel.objects.filter(
            conversation_id__in=ids, message__role='assistant',
        ).order_by('conversation_id', '-created_at', '-id')
        .values_list('conversation_id', 'message').iterator(chunk_size=500)
    ):
        if conversation_id in crm_updates or not isinstance(message, dict):
            continue
        content = parse_json_object(message.get('content'))
        if isinstance(content.get('crm_update'), dict) or 'semaforo' in content:
            crm_updates[conversation_id] = merge_crm_payload(content)
    faltantes = set(ids) - crm_updates.keys()
    for conversation_id, content in (
        ConversationEscalationModel.objects.filter(session_id__in=faltantes)
        .order_by('session_id', '-created_at', '-id')
        .values_list('session_id', 'conversation_content').iterator(chunk_size=500)
    ):
        crm_updates.setdefault(conversation_id, merge_crm_payload(content))

    destinatarios = {}
    for conversation_id, recipient in (
        EmailDeliveryLogModel.objects.filter(
            conversation_id__in=ids,
            kind=EmailDeliveryLogModel.KindEnum.REMINDER,
            status=EmailDeliveryLogModel.StatusEnum.SMTP_ACCEPTED,
        ).order_by('conversation_id', '-created_at', '-id')
        .values_list('conversation_id', 'recipient').iterator(chunk_size=500)
    ):
        destinatarios.setdefault(conversation_id, recipient)

    exitos = []
    for recordatorio in por_sincronizar:
        conv = recordatorio.conversation
        crm_update = dict(crm_updates.get(conv.pk) or {})
        contact = crm_update.get('contacto')
        contact = dict(contact) if isinstance(contact, dict) else {}
        destinatario = destinatarios.get(conv.pk)
        if not destinatario:
            destinatario, _ = reminder_recipient(conv, contact.get('correo'))
        if not destinatario:
            logger.warning('Odoo pendiente: conversacion_id=%s sin correo válido.', conv.pk)
            fallidos_odoo += 1
            continue

        contact['correo'] = destinatario
        if conv.prospect:
            contact.setdefault('nombre', conv.prospect.name)
            contact.setdefault('telefono', conv.prospect.phone)
            contact.setdefault('empresa', conv.prospect.enterprise)
        crm_update['contacto'] = contact
        crm_update['semaforo'] = 'amarillo'

        source = conv.source or ConversationSourceEnum.EMAIL
        identifier = conv.session_id if source == ConversationSourceEnum.WHATSAPP else destinatario
        lookup_source = source if source == ConversationSourceEnum.WHATSAPP else ConversationSourceEnum.EMAIL
        try:
            marker = recordatorio.message.get('odoo_reminder_sync')
            if isinstance(marker, dict) and marker.get('lead_id'):
                # Los recordatorios sincronizados antes de asignar vendedor
                # conservan su lead: no se abre una oportunidad duplicada.
                rows = odoo_call('crm.lead', 'read', [[marker['lead_id']]],
                                 {'fields': ['user_id']})
                if not rows:
                    # Si eliminaron ese lead, la búsqueda por correo del
                    # servicio reutiliza otro vigente o crea uno nuevo.
                    person = find_person_by_source(identifier, lookup_source) or {}
                    resultado = create_or_update_lead(
                        crm_update=crm_update, source=source,
                        identifier=identifier, person=person,
                        seller_id=settings.ODOO_SELLER_ID,
                    )
                else:
                    assigned = rows[0].get('user_id')
                    if not assigned:
                        if not odoo_call('crm.lead', 'write',
                                         [[marker['lead_id']], {'user_id': settings.ODOO_SELLER_ID}]):
                            raise RuntimeError('Odoo no confirmó la asignación del vendedor.')
                    resultado = {
                        'lead_id': marker['lead_id'],
                        'person_id': marker.get('person_id'),
                        'seller_id': assigned[0] if isinstance(assigned, (list, tuple)) else assigned or settings.ODOO_SELLER_ID,
                        'seller_name': assigned[1] if isinstance(assigned, (list, tuple)) and len(assigned) > 1 else None,
                    }
            else:
                person = (
                    {'id': conv.prospect.person_id}
                    if conv.prospect and conv.prospect.person_id else
                    find_person_by_source(identifier, lookup_source) or {}
                )
                resultado = create_or_update_lead(
                    crm_update=crm_update, source=source, identifier=identifier,
                    person=person, seller_id=settings.ODOO_SELLER_ID,
                )
            if not resultado.get('lead_id'):
                raise RuntimeError('Odoo no devolvió el identificador del lead.')
            seller_id = int(resultado.get('seller_id') or settings.ODOO_SELLER_ID)
            escalado = dict(crm_update)
            escalado['semaforo'] = 'verde'
            escalado['escalar_a_humano'] = True
            escalado['motivo_escalamiento'] = 'Recordatorio enviado y vendedor asignado.'
            exitos.append((recordatorio, escalado, resultado, seller_id))
        except Exception:
            fallidos_odoo += 1
            logger.exception('Odoo pendiente tras recordatorio: conversacion_id=%s', conv.pk)
        if len(exitos) >= 50:
            sincronizados += _guardar_escalamientos_recordatorio(exitos)
            exitos.clear()

    sincronizados += _guardar_escalamientos_recordatorio(exitos)

    logger.info('Odoo: sincronizados=%s | pendientes=%s', sincronizados, fallidos_odoo)
    logger.info("Reminders sent to %s stale conversation(s). Sin correo válido: %s.",
                procesados, sin_correo)

    return {'procesados': procesados}
