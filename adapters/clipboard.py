import uuid
from datetime import datetime

import pyperclip

from core.models import Message
from preprocess.clean import clean_email


class ClipboardAdapter:
    name = "clipboard"

    def current(self) -> Message:
        raw = (pyperclip.paste() or "").strip()
        if not raw:
            raise RuntimeError("剪贴板为空，请先复制邮件正文")
        body = clean_email(raw)
        subject = body.splitlines()[0][:30] if body else ""
        return Message(
            id=uuid.uuid4().hex,
            source="clipboard",
            sender="剪贴板",
            recipients=[],
            subject=subject,
            body_text=body,
            received_at=datetime.now(),
        )
