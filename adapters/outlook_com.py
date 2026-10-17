import uuid
from datetime import datetime

from core.models import Message
from preprocess.clean import clean_email

OL_INBOX, OL_SENT = 6, 5


class OutlookAdapter:
    name = "outlook"

    def __init__(self):
        import pythoncom
        import win32com.client
        self._pythoncom = pythoncom
        pythoncom.CoInitialize()
        try:
            self.app = win32com.client.GetActiveObject("Outlook.Application")
        except Exception:
            pythoncom.CoUninitialize()
            raise RuntimeError("未检测到正在运行的 Outlook，请先打开 Outlook 客户端后再操作")
        self.ns = self.app.GetNamespace("MAPI")

    def close(self):
        try:
            self._pythoncom.CoUninitialize()
        except Exception:
            pass

    def current(self) -> Message:
        explorer = self.app.ActiveExplorer()
        if explorer is None and self.app.Explorers.Count > 0:
            explorer = self.app.Explorers.Item(1)
        if explorer is None:
            raise RuntimeError("未检测到打开的 Outlook 邮件窗口，请打开 Outlook 主窗口并选中一封邮件")
        sel = explorer.Selection
        if sel.Count == 0:
            raise RuntimeError("请在 Outlook 中选中一封邮件")
        return self._convert(sel.Item(1))

    def sent_samples(self, limit=200):
        items = self.ns.GetDefaultFolder(OL_SENT).Items
        items.Sort("[SentOn]", True)
        return [self._convert(items.Item(i + 1)) for i in range(min(limit, items.Count))]

    @staticmethod
    def _smtp_address(entry):
        try:
            if entry.Type == "EX":
                smtp = entry.GetExchangeUser().PrimarySmtpAddress
                if smtp:
                    return str(smtp)
        except Exception:
            pass
        try:
            addr = str(entry.Address)
        except Exception:
            return ""
        return "" if addr.startswith("EX:") else addr

    def _convert(self, item):
        try:
            raw = item.HTMLBody if item.BodyFormat in (2, 3) else item.Body
        except Exception:
            raw = item.Body
        try:
            sender = self._smtp_address(item.Sender)
        except Exception:
            sender = ""
        if not sender:
            try:
                sender = str(item.SenderEmailAddress)
            except Exception:
                sender = ""
        try:
            recipients = [a for a in (self._smtp_address(r.AddressEntry) for r in item.Recipients) if a]
        except Exception:
            recipients = []
        received_at = datetime.now()
        for attr in ("SentOn", "ReceivedTime"):
            try:
                t = getattr(item, attr)
                received_at = datetime(t.year, t.month, t.day)
                break
            except Exception:
                continue
        return Message(
            id=str(item.EntryID),
            source="outlook",
            sender=sender,
            recipients=recipients,
            subject=str(item.Subject),
            body_text=clean_email(raw),
            received_at=received_at,
        )
