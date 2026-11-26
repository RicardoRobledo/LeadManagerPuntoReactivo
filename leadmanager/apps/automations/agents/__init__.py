from .agent_base import Agent
from .prospector_agent.prospector import prospector_agent

__all__ = [
    "Agent",
    "AGENTS",
]

# The order defines the flow. To add or remove an agent, or
# change the order, just edit this list.
AGENTS = {
    "prospector": prospector_agent,
}
