"""A FAKE chromadb.config: Settings keeps its keywords."""


class Settings:
    def __init__(self, **kwargs) -> None:
        self.__dict__.update(kwargs)
