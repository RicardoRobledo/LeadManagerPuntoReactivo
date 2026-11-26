"""
Schema JSON de la respuesta del agente para el canal web.

``reply_to_customer`` se publica en el widget mediante polling y
``conversation_state`` permite continuar el hilo hasta el enrutamiento.

Tampoco lleva ``enviar_adjunto`` (el adjunto solo se manda por correo) y el
``bant`` se reduce a ``need``: el widget ya no pregunta puesto ni plazo.

{
  "type": "object",
  "properties": {
    "reply_to_customer_esp": {
      "type": "string",
      "description": "Texto que se envía al prospecto en español"
    },
    "reply_to_customer": {
        "type": "string",
        "description": "Texto que se envía al prospecto por el canal correspondiente"
      },
      "conversation_state": {
        "type": "string",
        "enum": [
          "bienvenida",
          "descubrimiento",
          "calificacion",
          "perfilamiento",
          "resumen",
          "enrutamiento"
        ]
    },
    "crm_update": {
      "type": "object",
      "properties": {
        "unidad_negocio_detectada": {
          "type": "string",
          "enum": [
            "INTELIGENCIA ARTIFICIAL",
            "CIBERSEGURIDAD",
            "BPM",
            "APPS",
            "DESARROLLO WEB",
            "DIGITAL SIGNAGE",
            "ECOMMERCE",
            "KIOSKOS_DIGITALES",
            "MARKETING_DIGITAL",
            "ODOO",
            "NO_DETERMINADO"
          ]
        },
        "bant": {
          "type": "object",
          "properties": {
            "need": { "type": ["string", "null"] }
          },
          "required": ["need"],
          "additionalProperties": false
        },
        "contacto": {
          "type": "object",
          "properties": {
            "nombre": { "type": ["string", "null"] },
            "telefono": { "type": ["string", "null"] },
            "correo": { "type": ["string", "null"] },
            "empresa": { "type": ["string", "null"] }
          },
          "required": ["nombre", "telefono", "correo", "empresa"],
          "additionalProperties": false
        },
        "resumen_empresa": {
          "type": "string",
          "description": "Resumen de la empresa, con lo que se encuentre."
        },
        "orientacion_decision": {
          "type": "string",
          "enum": ["perdida", "ganancia", "no_determinado"]
        },
        "necesidad_certidumbre": {
          "type": "string",
          "enum": ["alta", "media", "baja"]
        },
        "sesgos_detectados": {
          "type": "array",
          "items": {
            "type": "string",
            "enum": [
              "anclaje",
              "efecto_dotacion",
              "contabilidad_mental",
              "aversion_perdida"
            ]
          }
        },
        "score_comercial": { "type": "integer" },
        "semaforo": {
          "type": "string",
          "enum": ["verde", "amarillo", "rojo", "gris"]
        },
        "motivo_semaforo": {
          "type": "string",
          "description": "Motivo por el que se asigna el semáforo"
        },
        "perfil_interaccion": {
          "type": "string",
          "enum": [
            "pregunton",
            "explorador",
            "comparador",
            "interesado",
            "comprador_decidido",
            "indeciso",
            "regateador",
            "poco_interactivo",
            "no_identificado"
          ]
        },
        "etiqueta_perfil_psicologico": {
          "type": "string",
          "description": "ej. Averso a la pérdida / alta necesidad de certidumbre"
        },
        "guia_negociacion_sugerida": {
          "type": "string",
          "description": "Recomendación breve para el vendedor"
        },
        "resumen_conversacion": {
          "type": "string",
          "description": "Resumen de 2-3 líneas de lo dicho hasta ahora"
        },
        "escalar_a_humano": { "type": "boolean" },
        "motivo_escalamiento": {
          "type": ["string", "null"],
          "description": "Motivo por el que se escala a un humano, o null si no aplica"
        }
      },
      "required": [
        "unidad_negocio_detectada",
        "bant",
        "contacto",
        "resumen_empresa",
        "orientacion_decision",
        "necesidad_certidumbre",
        "sesgos_detectados",
        "score_comercial",
        "motivo_semaforo",
        "perfil_interaccion",
        "semaforo",
        "etiqueta_perfil_psicologico",
        "guia_negociacion_sugerida",
        "resumen_conversacion",
        "escalar_a_humano",
        "motivo_escalamiento"
      ],
      "additionalProperties": false
    }
  },
  "required": ["crm_update", "reply_to_customer_esp", "reply_to_customer", "conversation_state"],
  "additionalProperties": false
}
"""

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class ConversationState(str, Enum):
    bienvenida = "bienvenida"
    descubrimiento = "descubrimiento"
    calificacion = "calificacion"
    perfilamiento = "perfilamiento"
    resumen = "resumen"
    enrutamiento = "enrutamiento"


class OrientacionDecision(str, Enum):
    perdida = "perdida"
    ganancia = "ganancia"
    no_determinado = "no_determinado"


class NecesidadCertidumbre(str, Enum):
    alta = "alta"
    media = "media"
    baja = "baja"


class SesgoDetectado(str, Enum):
    anclaje = "anclaje"
    efecto_dotacion = "efecto_dotacion"
    contabilidad_mental = "contabilidad_mental"
    aversion_perdida = "aversion_perdida"


class Semaforo(str, Enum):
    verde = "verde"
    amarillo = "amarillo"
    rojo = "rojo"
    gris = "gris"


class PerfilInteraccion(str, Enum):
    pregunton = "pregunton"
    explorador = "explorador"
    comparador = "comparador"
    interesado = "interesado"
    comprador_decidido = "comprador_decidido"
    indeciso = "indeciso"
    regateador = "regateador"
    poco_interactivo = "poco_interactivo"
    no_identificado = "no_identificado"


class Bant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    need: Optional[str] = None
    """Qué necesita el prospecto. El widget ya no pregunta puesto ni plazo."""


class Contacto(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre: Optional[str] = None
    """Nombre del prospecto"""

    telefono: Optional[str] = None
    """Teléfono del prospecto."""

    correo: Optional[str] = None
    """Correo del prospecto."""

    empresa: Optional[str] = None
    """Nombre de la empresa o negocio en la que está el prospecto."""


class CrmUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unidad_negocio_detectada: str
    bant: Bant
    contacto: Contacto

    resumen_empresa: str
    """Resumen de la empresa, con lo que se encuentre."""

    orientacion_decision: OrientacionDecision
    necesidad_certidumbre: NecesidadCertidumbre
    sesgos_detectados: List[SesgoDetectado]

    score_comercial: int
    semaforo: Semaforo
    motivo_semaforo: str
    perfil_interaccion: PerfilInteraccion

    etiqueta_perfil_psicologico: str
    """ej. Averso a la pérdida / alta necesidad de certidumbre"""

    guia_negociacion_sugerida: str
    """Recomendación breve para el vendedor"""

    resumen_conversacion: str
    """Resumen de 2-3 líneas de lo dicho hasta ahora"""

    escalar_a_humano: bool

    motivo_escalamiento: Optional[str] = None
    """Motivo por el que se escala a un humano, o null si no aplica"""


class AnswerWebSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_state: ConversationState

    reply_to_customer_esp: str
    reply_to_customer: str
    crm_update: CrmUpdate
