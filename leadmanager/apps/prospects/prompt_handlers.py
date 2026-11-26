class PromptHandler():

    system_prompt = ''

    @classmethod
    def set_system_prompt(cls, path: str):

        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
            cls.system_prompt = content

        return cls.system_prompt
