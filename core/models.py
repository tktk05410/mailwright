from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Message:
    id: str
    source: str
    sender: str
    recipients: list
    subject: str
    body_text: str
    received_at: datetime = None
    thread_id: str = ""


@dataclass
class Chunk:
    chunk_id: str
    text: str
    kind: str
    metadata: dict = field(default_factory=dict)
