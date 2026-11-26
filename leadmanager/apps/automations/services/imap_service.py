import email
import logging
import re

from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from email.header import decode_header
from email.mime.application import MIMEApplication
from imapclient import IMAPClient

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid, parsedate_to_datetime

from ..models import EmailDeliveryLogModel
from .email_delivery import begin_email_delivery, finish_email_delivery


logger = logging.getLogger(__name__)


CASOS_DE_EXITO_PDF_PATH = (
    Path(__file__).resolve().parents[4]
    / 'documentos'
    / 'Casos_de_exito.pdf'
)


def decode_data(valor):
    if not valor:
        return ""

    partes = []

    for texto, encoding in decode_header(valor):

        if isinstance(texto, bytes):
            texto = texto.decode(
                encoding or "utf-8",
                errors="ignore"
            )

        partes.append(texto)

    return "".join(partes)


def extract_body(msg):
    """
    Prioriza text/plain.
    Si solo existe HTML, elimina las etiquetas.
    """

    texto_plano = None
    texto_html = None

    if msg.is_multipart():

        for parte in msg.walk():

            if "attachment" in str(
                parte.get("Content-Disposition", "")
            ):
                continue

            charset = parte.get_content_charset() or "utf-8"
            tipo = parte.get_content_type()

            try:

                payload = parte.get_payload(decode=True)

                if not payload:
                    continue

                if tipo == "text/plain" and texto_plano is None:

                    texto_plano = payload.decode(
                        charset,
                        errors="ignore"
                    )

                elif tipo == "text/html" and texto_html is None:

                    texto_html = payload.decode(
                        charset,
                        errors="ignore"
                    )

            except Exception:
                continue

    else:

        charset = msg.get_content_charset() or "utf-8"

        try:

            payload = msg.get_payload(decode=True)

            if payload:

                if msg.get_content_type() == "text/html":

                    texto_html = payload.decode(
                        charset,
                        errors="ignore"
                    )

                else:

                    texto_plano = payload.decode(
                        charset,
                        errors="ignore"
                    )

        except Exception:
            pass

    if texto_plano:
        return texto_plano.strip()

    if texto_html:

        texto = re.sub(
            r"<[^>]+>",
            " ",
            texto_html
        )

        return re.sub(
            r"\s+",
            " ",
            texto
        ).strip()

    return "(sin contenido de texto)"


def extract_thread_id(msg):
    """
    Agrupa los correos de un mismo hilo.

    Usa el primer id de ``References``; si no hay, el de
    ``In-Reply-To``; si no hay ninguno, el propio ``Message-ID``.
    """

    references = msg.get("References") or ""
    in_reply_to = msg.get("In-Reply-To") or ""

    ref_ids = re.findall(r"<([^>]+)>", references)

    if ref_ids:
        return ref_ids[0]

    reply_ids = re.findall(r"<([^>]+)>", in_reply_to)

    if reply_ids:
        return reply_ids[0]

    return msg.get("Message-ID") or ""


def parse_data(datos_raw):
    """
    Becomes raw email data into a dictionary with relevant fields.

    example output:
    {
        "from": {"name": "John Doe", "email": "john.doe@example.com"},
        "to": "jane.smith@example.com",
        "subject": "Asunto del correo",
        "date": "2023-01-01 12:00:00",
        "message_id": "<1234567890@example.com>",
        "in_reply_to": "<9876543210@example.com>",
        "references": "<9876543210@example.com>",
        "thread_id": "<1234567890@example.com>",
        "body": "Cuerpo del correo"
    }
    """

    msg = email.message_from_bytes(datos_raw)

    name_and_email_from = extract_name_and_email(msg.get("From"))

    return {
        "from": {
            "name": name_and_email_from["name"],
            "email": name_and_email_from["email"],
        },
        "to": decode_data(msg.get("To")),
        "subject": decode_data(msg.get("Subject")),
        "date": parsedate_to_datetime(msg.get("Date")) if msg.get("Date") else None,
        "message_id": msg.get("Message-ID"),
        "in_reply_to": msg.get("In-Reply-To"),
        "references": msg.get("References"),
        "thread_id": extract_thread_id(msg),
        "body": extract_body(msg),
    }


def extract_name_and_email(raw):

    if not raw:
        return {"name": "", "email": ""}

    str_valor = raw if isinstance(raw, str) else ""

    if not str_valor:
        return {"name": "", "email": ""}

    email_match = re.search(r"<(.+?)>", str_valor)

    email = (
        email_match.group(1).strip().lower()
        if email_match
        else str_valor.strip().lower()
    )

    name = re.sub(r"<.+?>", "", str_valor).strip()

    return {
        "name": name,
        "email": email,
    }


def email_to_lead_data(correo):

    datos_from = extract_name_and_email(correo.get("de"))
    datos_to = extract_name_and_email(correo.get("para"))

    asunto = (correo.get("asunto") or "").strip()

    chat_input = (
        f"Asunto:{asunto}\n\n"
        f"---\n\n"
        f"Datos prospecto\n\n"
        f"---\n\n"
        f"Remitente:{datos_from['nombre']}\n\n"
        f"---\n\n"
        f"Cuerpo: {correo.get('cuerpo', '')}"
    )

    return {
        "sessionId": datos_from["email"],
        "chatInput": chat_input,
        "source": "email",
        "ourEmail": datos_to["email"],
    }


def get_unread_emails():
    """
    Returns the unread emails in the inbox.

    Uses BODY.PEEK[] instead of RFC822 on purpose: plain RFC822 makes the
    server set \\Seen as a side effect of reading, so an email would count
    as handled just for having been fetched — even if queueing it failed
    right after. With PEEK, the only thing that marks it is the explicit
    add_flags in tasks.py, once it is actually queued.
    """

    server = IMAPClient(
        settings.IMAP_HOST,
        port=settings.IMAP_PORT,
        ssl=True
    )

    try:

        server.login(
            settings.IMAP_USER,
            settings.IMAP_PASS
        )

        server.select_folder("INBOX")

        uids = server.search(["UNSEEN"])

        correos = []

        if not uids:
            return []

        for uid, datos in server.fetch(
            uids,
            ["BODY.PEEK[]"]
        ).items():

            # the server answers BODY.PEEK[] with the BODY[] key (no PEEK);
            # RFC822 kept as a fallback in case the server replies that way
            raw = datos.get(b"BODY[]") or datos.get(b"RFC822")

            if not raw:
                logger.warning("No body returned for UID %s, skipping.", uid)
                continue

            correo = parse_data(raw)

            correo["_uid"] = uid

            correos.append(correo)

        return correos

    finally:

        try:
            server.logout()
        except Exception:
            pass


def _html_email_part(body_html, inline_images):
    """Agrupa el HTML y sus imágenes incrustadas para mostrarlas por Content-ID."""
    html = MIMEText(body_html, 'html', 'utf-8')
    if not inline_images:
        return html
    related = MIMEMultipart('related')
    related.attach(html)
    for content_id, image_path in inline_images.items():
        path = Path(image_path)
        image = MIMEImage(path.read_bytes())
        image.add_header('Content-ID', '<{}>'.format(content_id))
        image.add_header('Content-Disposition', 'inline', filename=path.name)
        related.attach(image)
    return related


def casos_de_exito_cabe_como_adjunto():
    """
    ¿El PDF de casos de éxito se puede adjuntar sin que el correo rebote?

    El archivo pesaba 33.5 MB: en base64 el correo llegaba a ~43 MB, y Gmail
    (25 MB) y Outlook (36 MB) lo rechazaban después de que nuestro SMTP ya
    lo había aceptado. El prospecto no recibía la respuesta y aquí nadie se
    enteraba, porque el rebote llega como un correo de Mailer-Daemon.
    """
    try:
        tamano = CASOS_DE_EXITO_PDF_PATH.stat().st_size
    except OSError:
        return False

    return tamano <= settings.EMAIL_MAX_ADJUNTO_MB * 1024 * 1024


def _dominio_remitente():
    """
    El dominio de la cuenta que envía (info@estrasol.mx -> estrasol.mx).

    Se usa para el Message-ID y para el HELO. Sin esto, smtplib y
    make_msgid toman el hostname del VPS ("ubuntu-24.localhost"), que no
    resuelve en DNS ni coincide con el remitente: Gmail lo cuenta en contra
    y, tras varios correos al mismo destinatario, los manda a spam.
    """
    return (settings.EMAIL_HOST_USER or '').rsplit('@', 1)[-1] or None


def _build_email_message(to, subject, body, body_html, send_attachment,
                         in_reply_to, references, inline_images, message_id):
    """Construye el MIME conservando adjuntos, imágenes y cabeceras del hilo."""
    if send_attachment:
        msg = MIMEMultipart("mixed")
    else:
        msg = MIMEMultipart("alternative")

    msg["Subject"] = subject
    msg["From"] = formataddr(
        (settings.EMAIL_FROM_NAME, settings.EMAIL_HOST_USER)
    )
    msg["To"] = to
    msg["Message-ID"] = message_id

    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to

    if references:
        msg["References"] = references

    if send_attachment:

        body_container = MIMEMultipart("alternative")
        body_container.attach(MIMEText(body, "plain", "utf-8"))

        if body_html:
            body_container.attach(_html_email_part(body_html, inline_images))

        msg.attach(body_container)

        try:
            with open(CASOS_DE_EXITO_PDF_PATH, "rb") as archivo:
                pdf_data = archivo.read()
        except OSError:
            logger.warning(
                "No se pudo leer el PDF de casos de éxito en %s; "
                "se envía el correo sin adjunto.",
                CASOS_DE_EXITO_PDF_PATH,
            )
        else:
            attachment = MIMEApplication(
                pdf_data,
                _subtype="pdf",
                Name="Casos_de_exito.pdf",
            )
            attachment.add_header(
                "Content-Disposition",
                "attachment",
                filename="Casos_de_exito.pdf",
            )
            msg.attach(attachment)

    else:

        msg.attach(MIMEText(body, "plain", "utf-8"))

        if body_html:
            msg.attach(_html_email_part(body_html, inline_images))

    return msg


def send_email(to, body, body_html=None, send_attachment=False,
               subject=None, in_reply_to=None, references=None, inline_images=None,
               *, conversation_id=None, source='', kind=EmailDeliveryLogModel.KindEnum.OTHER):
    """Registra un intento y devuelve si SMTP aceptó el correo, sin confirmar entrega final."""
    recipient = to.strip() if isinstance(to, str) else ''
    subject = subject or 'Respuesta a tu consulta de Estrasol'
    message_id = make_msgid(domain=_dominio_remitente())
    record = begin_email_delivery(
        recipient, subject, kind, conversation_id=conversation_id,
        source=source, message_id=message_id,
    )
    if record is None:
        return False
    try:
        validate_email(recipient)
    except ValidationError as exc:
        finish_email_delivery(record, EmailDeliveryLogModel.StatusEnum.SKIPPED, error=exc)
        logger.warning('Correo omitido: intento_id=%s | motivo=destinatario no válido', record.pk)
        return False

    accepted = False
    try:
        msg = _build_email_message(
            recipient, subject, body, body_html, send_attachment,
            in_reply_to, references or in_reply_to, inline_images, message_id,
        )
        with smtplib.SMTP(settings.EMAIL_HOST, settings.EMAIL_PORT,
                          local_hostname=_dominio_remitente(),
                          timeout=getattr(settings, 'EMAIL_TIMEOUT', None) or 30) as server:

            if getattr(settings, "EMAIL_USE_TLS", True):
                server.starttls()

            server.login(
                settings.EMAIL_HOST_USER,
                settings.EMAIL_HOST_PASSWORD,
            )

            refused = server.sendmail(
                settings.EMAIL_HOST_USER,
                [recipient],
                msg.as_string(),
            )
            if isinstance(refused, dict) and refused:
                raise smtplib.SMTPRecipientsRefused(refused)
            accepted = True
    except Exception as exc:
        if accepted:
            # La aceptación de DATA ya ocurrió. Un fallo de QUIT no debe
            # provocar un reenvío ni registrar el mensaje como rechazado.
            finish_email_delivery(
                record, EmailDeliveryLogModel.StatusEnum.SMTP_ACCEPTED,
                detail=f'SMTP aceptó el mensaje; hubo un error al cerrar la sesión: {exc}',
            )
            logger.warning('SMTP aceptó intento_id=%s; falló el cierre de la sesión.', record.pk)
            return True
        finish_email_delivery(record, EmailDeliveryLogModel.StatusEnum.FAILED, error=exc)
        logger.exception('Error sending email to %s | intento_id=%s | message_id=%s',
                         recipient, record.pk, message_id)
        return False

    finish_email_delivery(record, EmailDeliveryLogModel.StatusEnum.SMTP_ACCEPTED)
    logger.info('Email sent to %s | intento_id=%s | message_id=%s', recipient, record.pk, message_id)
    return True


def send_reminder_email(to_email, subject, body, *, conversation_id=None, source=''):
    """Envía y registra el recordatorio sin cabeceras de respuesta a un hilo."""
    sent = send_email(
        to_email, body, subject=subject, conversation_id=conversation_id,
        source=source, kind=EmailDeliveryLogModel.KindEnum.REMINDER,
    )
    if sent:
        logger.info('Reminder email sent to %s', to_email)
    return sent
