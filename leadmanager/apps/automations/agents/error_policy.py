"""
Clasifica un fallo del pipeline en una de tres acciones, para que los
tasks decidan si reintentar, esperar mas, o rendirse.

El SDK de OpenAI NO tiene una clase propia para "se acabo el saldo": eso
llega como RateLimitError (429), igual que un "vas muy rapido" que si se
resuelve solo. Lo que las separa es ``exc.code``.
"""

import openai

from ..services.agent_selection import ProductionAgentUnavailable


REINTENTABLE = 'reintentable'
SIN_SALDO = 'sin_saldo'
PERMANENTE = 'permanente'


class CorreoOmitidoError(RuntimeError):
    """El destinatario no es válido y el correo no debe reintentarse."""


def _causa_openai(exc):
    """
    La excepcion de OpenAI que origino el fallo, o None si no fue la IA.

    Se miran dos niveles porque llega de dos formas: cruda desde
    generate_history_summary_step, que no tiene try/except, o dentro de
    __cause__ cuando los steps 4 y 5 la encadenan a su RuntimeError.
    """
    for candidato in (exc, getattr(exc, '__cause__', None)):
        if isinstance(candidato, openai.OpenAIError):
            return candidato

    return None


def clasificar(exc):

    if isinstance(exc, (CorreoOmitidoError, ProductionAgentUnavailable)):
        return PERMANENTE

    api = _causa_openai(exc)

    if api is None:
        # No fue la IA: red, base, Odoo, IMAP. Casi siempre pasajero.
        return REINTENTABLE

    if isinstance(api, openai.APIConnectionError):
        # APITimeoutError hereda de esta, asi que ya queda cubierto.
        return REINTENTABLE

    if isinstance(api, openai.RateLimitError):
        return SIN_SALDO if api.code == 'insufficient_quota' else REINTENTABLE

    if isinstance(api, (openai.AuthenticationError,
                        openai.PermissionDeniedError,
                        openai.BadRequestError)):
        # API key mala, 403, prompt invalido, contexto excedido:
        # reintentar mil veces da exactamente el mismo error.
        return PERMANENTE

    return REINTENTABLE
