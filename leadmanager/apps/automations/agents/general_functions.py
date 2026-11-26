import json

from leadmanager.apps.prospects.models import ConversationModel, ConversationStateEnum, ChatHistoryModel


def get_previous_conversation_context(session_id):
    """
    Return a text with the last thing that happened with this prospect in a
    conversation that is already closed, or None if it is the first time they write.

    Takes the most recent assistant message that carries a crm_update: that is
    where Sofia left the accumulated summary, the BANT and the company. If the
    history was compacted by generate_history_summary_step, that message is the
    summary itself, which is exactly what we want.
    """
    previa = (
        ConversationModel.objects
        .filter(session_id=session_id)
        .exclude(state=ConversationStateEnum.OPEN)
        .order_by("-id")
        .first()
    )

    if previa is None:
        return None

    for msg in previa.messages.order_by("-created_at"):
        contenido = (msg.message or {}).get("content") or ""

        try:
            data = json.loads(contenido)
        except (ValueError, TypeError):
            continue    # los mensajes del usuario y los de búsqueda no son JSON

        if not isinstance(data, dict):
            continue

        crm = data.get("crm_update") or {}

        if not crm:
            continue

        return (
            f"[Contexto de la conversación anterior]\nEsto es lo último que quedó registrado de este prospecto."
            "Úsalo para no volver a preguntar su información personal, la información de lo que pidió la última"
            "vez es solo de contexto, no asumas que quiere lo mismo a no ser que el lo diga, pero no retomes esa."
            f"conversación, esta es una nueva conversación:{json.dumps(crm, ensure_ascii=False, indent=2)}"
        )

    return None
