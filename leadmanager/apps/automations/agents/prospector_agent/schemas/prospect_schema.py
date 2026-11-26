from typing import Optional
from pydantic import BaseModel, Field


class SearchSchema(BaseModel):
    document_ids: list[int] = Field(
        default_factory=list,
        max_length=3,
        description="Lista de máximo 3 ids de documentos a buscar"
    )
    enterprise: Optional[str] = Field(
        default=None,
        description="Nombre de la empresa a buscar, o null si no se identifica o ya está registrada"
    )
