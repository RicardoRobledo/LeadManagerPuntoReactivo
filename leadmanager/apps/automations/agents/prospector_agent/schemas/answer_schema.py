"""
Schema JSON para la respuesta del agente CRM.

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
            "odoo",
            "kioscos_digitales",
            "web_marketing",
            "nube",
            "nextwave_ai",
            "no_determinado"
          ]
        },
        "bant": {
          "type": "object",
          "properties": {
            "authority": { "type": "string" },
            "need": { "type": "string" },
            "timing": { "type": "string" }
          },
          "required": ["authority", "need", "timing"],
          "additionalProperties": false
        },
        "contacto": {
          "type": "object",
          "properties": {
            "nombre": {
              "type": ["string", "null"],
              "description": "Nombre del prospecto"
            },
            "telefono": {
              "type": ["string", "null"],
              "description": "Teléfono del prospecto."
            },
            "correo": {
              "type": ["string", "null"],
              "description": "Correo del prospecto."
            },
            "empresa": {
              "type": ["string", "null"],
              "description": "Nombre de la empresa o negocio en la que está el prospecto."
            }
          },
          "required": ["nombre", "telefono", "correo", "empresa"],
          "additionalProperties": false
        },
        "resumen_empresa": {
          "type": "string",
          "description": "Información solamente de la empresa, su giro, tamaño, reputación, historia, etc."
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
              "aversion_arrepentimiento",
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
        "enviar_adjunto": {
          "type": "boolean",
          "description": "true sí pregunta sobre kioskos digitales por primera vez y se debe enviar adjunto, false no lo es"
        },
        "etiqueta_perfil_psicologico": {
          "type": "string",
          "description": "ej. Averso a la pérdida / alta necesidad de certidumbre"
        },
        "guia_negociacion_sugerida": {
          "type": "string",
          "description": "Recomendación breve para el vendedor, basada en la matriz de la sección 10"
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
        "semaforo",
        "motivo_semaforo",
        "perfil_interaccion",
        "enviar_adjunto",
        "etiqueta_perfil_psicologico",
        "guia_negociacion_sugerida",
        "resumen_conversacion",
        "escalar_a_humano",
        "motivo_escalamiento"
      ],
      "additionalProperties": false
    }
  },
  "required": ["reply_to_customer_esp", "reply_to_customer", "conversation_state", "crm_update"],
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
    aversion_arrepentimiento = "aversion_arrepentimiento"
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

    authority: str
    need: str
    timing: str


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
    """Información solamente de la empresa, su giro, tamaño, reputación, historia, etc."""

    orientacion_decision: OrientacionDecision
    necesidad_certidumbre: NecesidadCertidumbre
    sesgos_detectados: List[SesgoDetectado]

    score_comercial: int
    semaforo: Semaforo
    motivo_semaforo: str

    perfil_interaccion: PerfilInteraccion

    enviar_adjunto: bool
    """true sí pregunta sobre kioskos digitales por primera vez y se debe enviar adjunto, false no lo es"""

    etiqueta_perfil_psicologico: str
    """ej. Averso a la pérdida / alta necesidad de certidumbre"""

    guia_negociacion_sugerida: str
    """Recomendación breve para el vendedor, basada en la matriz de la sección 10"""

    resumen_conversacion: str
    """Resumen de 2-3 líneas de lo dicho hasta ahora"""

    escalar_a_humano: bool

    motivo_escalamiento: Optional[str] = None
    """Motivo por el que se escala a un humano, o null si no aplica"""


class AnswerSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply_to_customer_esp: str
    reply_to_customer: str
    """Texto que se envía al prospecto por el canal correspondiente"""

    conversation_state: ConversationState
    crm_update: CrmUpdate
