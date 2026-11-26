"""
Diagnostico de punta a punta del flujo de correo.

Correr con:
    python manage.py shell < diagnostico_correo.py

Revisa las 4 etapas por las que pasa un correo, para ubicar en cual se
rompio:
    1. Llego al buzon IMAP?
    2. Lo levanto retrieve_emails y lo guardo en la BD?
    3. Corrio el pipeline y genero conversacion/mensajes?
    4. Genero una respuesta el agente?
"""

from django.conf import settings
from imapclient import IMAPClient

from leadmanager.apps.prospects.models import (
    EmailMessageModel,
    ConversationModel,
    ChatHistoryModel,
)


def titulo(texto):
    print("\n" + "=" * 70)
    print(texto)
    print("=" * 70)


# ---------------------------------------------------------------- 1. IMAP
titulo("1) BUZON IMAP (INBOX)")

server = IMAPClient(settings.IMAP_HOST, port=settings.IMAP_PORT, ssl=True)

try:
    server.login(settings.IMAP_USER, settings.IMAP_PASS)
    info = server.select_folder("INBOX")

    print("cuenta          :", settings.IMAP_USER)
    print("total mensajes  :", info.get(b"EXISTS"))
    print("UIDNEXT         :", info.get(b"UIDNEXT"))
    print("UNSEEN          :", server.search(["UNSEEN"]))
    print("sin 'procesado' :", server.search(["UNKEYWORD", "procesado"]))

    ultimos = server.search(["ALL"])[-6:]
    print("\nultimos 6 correos en el buzon:")

    if ultimos:
        for uid, data in server.fetch(ultimos, ["ENVELOPE", "FLAGS"]).items():
            env = data[b"ENVELOPE"]
            remitente = ""
            if env.from_:
                a = env.from_[0]
                remitente = b"@".join(
                    [a.mailbox or b"", a.host or b""]).decode("utf-8", "ignore")
            asunto = (env.subject or b"").decode("utf-8", "ignore")
            print(
                f"  UID {uid} | {remitente} | {asunto[:45]} | flags={data[b'FLAGS']}")
    else:
        print("  (vacio)")

finally:
    try:
        server.logout()
    except Exception:
        pass


# ------------------------------------------------------------------ 2. BD
titulo("2) BASE DE DATOS - EmailMessageModel (ultimos 10)")

correos = EmailMessageModel.objects.order_by("-id")[:10]

if not correos:
    print("  (no hay registros: retrieve_emails nunca guardo nada)")

for m in correos:
    print(f"  #{m.id} | {m.status} | de={m.email} | uid={m.imap_uid} | "
          f"asunto={(m.subject or '')[:35]}")
    print(f"        message_id={m.message_id}")


# -------------------------------------------------------- 3. Conversaciones
titulo("3) CONVERSACIONES Y MENSAJES (ultimas 3)")

conversaciones = ConversationModel.objects.order_by("-id")[:3]

if not conversaciones:
    print("  (no hay conversaciones: el pipeline nunca llego al paso 2)")

for c in conversaciones:
    mensajes = c.messages.order_by("created_at")
    print(f"\n  conversacion #{c.id} | session={c.session_id} | "
          f"estado={c.state} | source={c.source} | mensajes={mensajes.count()}")

    for msg in mensajes:
        data = msg.message or {}
        rol = data.get("role")
        contenido = str(data.get("content"))
        print(f"     [{rol}] {contenido[:300]}")


# ---------------------------------------------------------------- 4. Resumen
titulo("4) RESUMEN POR ESTADO")

for estado in ["PENDING", "PROCESSING", "PROCESSED", "FAILED"]:
    try:
        total = EmailMessageModel.objects.filter(status=estado).count()
        print(f"  {estado:12} : {total}")
    except Exception as e:
        print(f"  {estado:12} : (no se pudo contar: {e})")

print("\nListo.\n")
