import os

from ..resource_handler import ResourceHandler


class ProspectResourceHandler(ResourceHandler):
    """
    Handler de prompts y tools del agente prospectador.

    Solo declara dónde están sus recursos: el ``ResourceHandler`` base lee
    los ``.txt`` de ``prompts/`` como strings (p. ej.
    ``search_information_prompt``) y los ``.json`` de ``tools/`` como
    dicts (tools de OpenAI).
    """

    prompts_dir = os.path.join(os.path.dirname(__file__), 'prompts')
    tools_dir = os.path.join(os.path.dirname(__file__), 'tools')
