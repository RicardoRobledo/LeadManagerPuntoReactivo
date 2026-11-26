from pathlib import Path

from ..resource_handler import ResourceHandler


class ConversationAnalyzerResourceHandler(ResourceHandler):
    prompts_dir = Path(__file__).resolve().parent / 'prompts'
