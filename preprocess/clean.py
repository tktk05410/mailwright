"""邮件清洗：去头、去引用、去签名。"""

import re

from bs4 import BeautifulSoup

# 邮件头只删本行
HEADER_MARK = re.compile(
    r"^(发件人|发送时间|收件人|抄送|主题|From|Sent|To|Cc|Subject|Date|日期)[:：]",
    re.IGNORECASE,
)
# 引用起点
QUOTE_MARK = re.compile(
    r"(^-{2,}\s*(原始邮件|转发邮件|Original Message|Forwarded message)"
    r"|^在\s*.{0,40}\s*写道[:：]\s*$"
    r"|^On\s.+\s*wrote[:：]\s*$"
    r"|^-{2,}\s*原始邮件"
    r"|^_{5,}\s*$)",
    re.IGNORECASE,
)
# 签名标记
SIG_MARK = re.compile(
    r"^(--\s*$|发自我的|Sent from my|Best regards,?$)",
    re.IGNORECASE,
)
LEADING_QUOTE = re.compile(r"^\s*>+\s?")
# 必须出现真实标签才算 HTML
HTML_TAG = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*(?:\s[^>]*)?/?>")
ENVELOPE = {
    "sender": re.compile(r"^(发件人|From)[:：]\s*(.+)$", re.IGNORECASE),
    "subject": re.compile(r"^(主题|Subject)[:：]\s*(.+)$", re.IGNORECASE),
}

# 只在末尾/开头若干行内匹配
SIG_TAIL_LINES = 10
HEADER_TAIL_LINES = 20
TRUNCATE_LIMIT = 2000


def html_to_text(raw):
    """确认是 HTML 才解析。"""
    if not HTML_TAG.search(raw or ""):
        return raw or ""
    return BeautifulSoup(raw, "html.parser").get_text("\n")


def strip_quotes(text):
    """去邮件头与引用块。"""
    kept, in_quote = [], False
    for i, line in enumerate(text.splitlines()):
        s = line.strip()
        if i < HEADER_TAIL_LINES and HEADER_MARK.match(s):
            continue  # 跳过本行
        if not in_quote and QUOTE_MARK.search(s):
            in_quote = True  # 进入引用区
        if in_quote:
            continue
        kept.append(LEADING_QUOTE.sub("", line))  # 去引用符
    return "\n".join(kept)


def strip_signature(text):
    """去末尾签名。"""
    lines = text.splitlines()
    start = max(len(lines) - SIG_TAIL_LINES, 0)
    for i in range(start, len(lines)):
        if SIG_MARK.match(lines[i].strip()):
            return "\n".join(lines[:i])
    return text


def truncate(text, limit=TRUNCATE_LIMIT):
    """按字数截断。"""
    return text[:limit]


def parse_envelope(raw):
    """取发件人与主题。"""
    sender, subject = "", ""
    for line in (raw or "").splitlines()[:HEADER_TAIL_LINES]:
        s = line.strip()
        if not sender:
            m = ENVELOPE["sender"].match(s)
            if m:
                sender = m.group(2).strip()
                continue
        if not subject:
            m = ENVELOPE["subject"].match(s)
            if m:
                subject = m.group(2).strip()
        if sender and subject:
            break
    return sender, subject


def clean_email(raw):
    """清洗入口。"""
    return truncate(strip_signature(strip_quotes(html_to_text(raw or "")))).strip()
