import importlib
import json
import os
import pkgutil


class ResourceHandler:
    """
    Carga automáticamente los prompts y las tools de OpenAI de todos los
    agentes.

    Cada agente vive en su propia carpeta dentro de ``agents/`` (p. ej.
    ``prospector_agent``) y declara dos directorios: ``prompts_dir`` (archivos
    ``.txt``) y ``tools_dir`` (archivos ``.json`` con la definición de cada
    tool de OpenAI). La clase del agente hereda de ``ResourceHandler``, define
    esas rutas y se exporta desde el ``__init__.py`` de la carpeta del agente.

    Para agregar un agente nuevo solo hay que:

    1. Crear ``agents/<nombre>/prompts/`` con los ``.txt`` y, si hace falta,
       ``agents/<nombre>/tools/`` con los ``.json``.
    2. Crear una clase que herede de ``ResourceHandler`` y defina
       ``prompts_dir`` (y opcionalmente ``tools_dir``).
    3. Exportarla desde ``agents/<nombre>/__init__.py``.

    ``load_all`` descubre las carpetas de agente y carga prompts y tools de
    todas; se llama una sola vez (por ejemplo en el ``ready`` de la app).

    Cada archivo queda disponible en su handler como atributo de clase:
    ``nombre.txt`` -> ``nombre`` (string) y ``nombre.json`` -> ``nombre``
    (dict parseado con ``json.load``). Las tools además se pueden obtener
    como lista con ``get_tools()``.
    """

    prompts_dir = None
    tools_dir = None
    _registry = []

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        if cls.prompts_dir or cls.tools_dir:
            ResourceHandler._registry.append(cls)

    @classmethod
    def load(cls):
        """Lee prompts y tools y los deja como atributos de clase."""
        loaded = {}
        loaded.update(cls._load_prompts())
        loaded.update(cls._load_tools())
        return loaded

    @classmethod
    def _load_prompts(cls):
        """Lee los ``.txt`` de ``prompts_dir`` como strings."""
        if not cls.prompts_dir:
            return {}

        loaded = {}

        for filename in sorted(os.listdir(cls.prompts_dir)):
            if not filename.endswith('.txt'):
                continue

            name = filename[:-4]
            path = os.path.join(cls.prompts_dir, filename)

            with open(path, 'r', encoding='utf-8') as prompt_file:
                content = prompt_file.read()

            setattr(cls, name, content)
            loaded[name] = content

        return loaded

    @classmethod
    def _load_tools(cls):
        """Lee los ``.json`` de ``tools_dir`` como dicts (tools de OpenAI)."""
        if not cls.tools_dir or not os.path.isdir(cls.tools_dir):
            return {}

        loaded = {}

        for filename in sorted(os.listdir(cls.tools_dir)):
            if not filename.endswith('.json'):
                continue

            name = filename[:-5]
            path = os.path.join(cls.tools_dir, filename)

            with open(path, 'r', encoding='utf-8') as tool_file:
                content = json.load(tool_file)

            setattr(cls, name, content)
            loaded[name] = content

        return loaded

    @classmethod
    def get_tools(cls):
        """Devuelve las tools de OpenAI cargadas como lista, en orden alfabético."""
        if not cls.tools_dir or not os.path.isdir(cls.tools_dir):
            return []

        return [
            cls.get(os.path.splitext(filename)[0])
            for filename in sorted(os.listdir(cls.tools_dir))
            if filename.endswith('.json')
        ]

    @classmethod
    def get(cls, name):
        """Devuelve el recurso por su nombre, sin extensión."""
        return getattr(cls, name)

    @classmethod
    def load_all(cls):
        """Descubre los agentes y carga prompts y tools de todos ellos."""
        cls._discover_agents()

        for handler in ResourceHandler._registry:
            handler.load()

        return ResourceHandler._registry

    @classmethod
    def _discover_agents(cls):
        """Importa cada carpeta de agente para registrar sus handlers."""
        package = importlib.import_module(__package__)

        for module_info in pkgutil.iter_modules(package.__path__):
            if module_info.ispkg:
                importlib.import_module(f"{__package__}.{module_info.name}")
