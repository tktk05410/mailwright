from typing import Protocol

from core.models import Message


class SourceAdapter(Protocol):
    name: str

    def current(self) -> Message: ...
