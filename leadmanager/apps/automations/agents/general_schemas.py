from pydantic import BaseModel, Field


class ValidationSchema(BaseModel):
    score: int = Field(..., description="Puntaje de la validación")
    reason: str = Field(..., description="Razón de la validación")
