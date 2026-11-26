"""Análisis del cierre diario mediante response_model, sin ejecutar un run."""

import json
from pathlib import Path

from django.conf import settings
from pydantic import BaseModel, ConfigDict

from .agent_base import Agent
from ..services.report_redaction import redact_contact_data


class DailySummaryAnalysis(BaseModel):
    model_config = ConfigDict(extra='forbid')

    resumen_ejecutivo: str


daily_summary_agent = Agent(name='resumir-final-dia', steps=[])
PROMPT_PATH = Path(__file__).parent / 'prompts' / 'daily_summary.txt'


def analyze_daily_report(report):
    """Una llamada estructurada para el conjunto, sin Agent.run ni log_event."""
    if not report['conversaciones']:
        return DailySummaryAnalysis(
            resumen_ejecutivo='No se registraron conversaciones en este periodo.',
        )

    # Las tarjetas tienen contactos y textos originales exclusivos del correo.
    # Solo el análisis anonimizado y los totales se comparten con el LLM.
    analysis_report = {key: report[key] for key in (
        'fecha', 'zona_horaria', 'inicio', 'fin', 'estadisticas', 'conversaciones',
    )}
    response = daily_summary_agent.response_model(
        system_prompt=PROMPT_PATH.read_text(encoding='utf-8'),
        input=[{'role': 'user', 'content': json.dumps(analysis_report, ensure_ascii=False)}],
        type='parsed',
        model=settings.DAILY_SUMMARY_MODEL,
        text_format=DailySummaryAnalysis,
    )
    analysis = response.output_parsed
    if analysis is None:
        raise RuntimeError('El LLM no devolvió un análisis estructurado del cierre diario.')

    if not analysis.resumen_ejecutivo.strip():
        raise RuntimeError('El resumen ejecutivo está vacío.')
    analysis.resumen_ejecutivo = redact_contact_data(analysis.resumen_ejecutivo)
    return analysis
