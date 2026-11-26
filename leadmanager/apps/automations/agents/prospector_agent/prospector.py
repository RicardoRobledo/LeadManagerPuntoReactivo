from ..agent_base import Agent
from . import prospector_steps
from ...services.agent_execution import capture_shadow_input_step

import logging


logger = logging.getLogger(__name__)


PRODUCTION_STEPS = [
    prospector_steps.verify_existing_prospect_step,
    prospector_steps.verify_existing_conversation_step,
    capture_shadow_input_step,
    prospector_steps.load_knowledge_catalog_step,
    prospector_steps.search_information_step,
    prospector_steps.generate_prospect_response_step,
    prospector_steps.generate_history_summary_step,
    prospector_steps.get_odoo_seller_step,
    prospector_steps.find_or_create_company_step,
    prospector_steps.create_or_update_lead_step,
    prospector_steps.send_response_to_prospect_step,
]


def build_prospector_agent(configuration):
    return Agent(
        name=configuration.key,
        mode=configuration.mode,
        production_steps=list(PRODUCTION_STEPS),
    )


prospector_agent = Agent(name="prospector", steps=list(PRODUCTION_STEPS))
