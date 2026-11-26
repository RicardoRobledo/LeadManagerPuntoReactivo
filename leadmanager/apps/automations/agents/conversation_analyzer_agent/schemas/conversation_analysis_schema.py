from pydantic import BaseModel, Field, field_validator


class LearningAnnotationSchema(BaseModel):
    observaciones: str = Field(min_length=1, max_length=4000)
    sugerencias: str = Field(min_length=1, max_length=4000)

    @field_validator('observaciones', 'sugerencias')
    @classmethod
    def strip_content(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('Las observaciones y las sugerencias no pueden estar vacías.')
        return value


class ConversationAnalysisSchema(BaseModel):
    annotations: list[LearningAnnotationSchema] = Field(max_length=10)
