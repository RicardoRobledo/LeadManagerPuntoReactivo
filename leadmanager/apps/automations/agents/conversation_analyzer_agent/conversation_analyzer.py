"""Ejecución explícita con conversation_id y agent_id en el contexto."""

from ..agent_base import Agent
from . import conversation_analyzer_steps as steps


conversation_analyzer_agent = Agent(
    name='conversation_analyzer',
    steps=[
        steps.load_conversation_step,
        steps.check_learning_enabled_step,
        steps.analyze_conversation_step,
        steps.save_learning_annotations_step,
    ],
)
