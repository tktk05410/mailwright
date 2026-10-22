"""项目内部数据结构。"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Message:
    """一封邮件的统一表示。"""

    id: str
    source: str  # clipboard / outlook / manual
    sender: str
    recipients: list
    subject: str
    body_text: str
    received_at: datetime = None
    thread_id: str = ""


@dataclass
class Chunk:
    """知识库文本块。"""

    chunk_id: str
    text: str
    kind: str  # handbook 或 style
    metadata: dict = field(default_factory=dict)
