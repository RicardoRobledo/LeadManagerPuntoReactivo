"""Contratos de respuesta con las unidades de negocio configuradas por el usuario."""

from functools import lru_cache
from typing import Literal

from pydantic import create_model

from .answer_schema import AnswerSchema
from .answer_schema_web import AnswerWebSchema


@lru_cache(maxsize=64)
def configured_answer_schema(source, unit_names):
    base = AnswerWebSchema if source == 'web' else AnswerSchema
    names = tuple(dict.fromkeys((*unit_names, 'NO_DETERMINADO')))
    unit_type = Literal[names]
    crm = create_model(
        'ConfiguredCrmUpdate', __base__=base.model_fields['crm_update'].annotation,
        unidad_negocio_detectada=(unit_type, ...),
    )
    return create_model('ConfiguredAnswerSchema', __base__=base, crm_update=(crm, ...))
