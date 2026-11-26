WEB_CHAT_MESSAGE_LIMIT = 9
WEB_CHAT_LIMIT_MESSAGE = 'Has alcanzado el límite de mensajes permitidos.'


def message_limit_state(conversation):
    count = conversation.web_message_count if conversation else 0
    reached = count >= WEB_CHAT_MESSAGE_LIMIT
    return {
        'message_count': count,
        'message_limit': WEB_CHAT_MESSAGE_LIMIT,
        'message_limit_reached': reached,
        'limit_message': WEB_CHAT_LIMIT_MESSAGE if reached else '',
    }
