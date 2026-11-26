"""Contrato de la única conclusión de un lote semanal de análisis."""

from pydantic import BaseModel, Field, field_validator


class FinalAnalysisSchema(BaseModel):
    observaciones: str = Field(min_length=1, max_length=4000)
    sugerencias: str = Field(min_length=1, max_length=4000)

    @field_validator('observaciones', 'sugerencias')
    @classmethod
    def strip_content(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('Las observaciones y las sugerencias finales no pueden estar vacías.')
        return value
