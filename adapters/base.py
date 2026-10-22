"""数据源适配器统一接口。"""

from typing import Protocol

from core.models import Message


class SourceAdapter(Protocol):
    """邮件来源的最小协议。"""

    name: str

    def current(self) -> Message:
        """读取当前一封邮件。"""
        ...
