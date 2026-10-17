import re

from bs4 import BeautifulSoup

HEADER_MARK = re.compile(r"^(发件人|发送时间|收件人|抄送|主题|From|Sent|To|Cc|Subject|Date|日期)[:：]", re.IGNORECASE)
QUOTE_MARK = re.compile(
    r"(^-{2,}\s*(原始邮件|转发邮件)|^在\s*.{0,30}\s*写道[:：]\s*$|^On\s.+\s*wrote:\s*$|"
    r"^-----+\s*原始邮件|^-----+\s*转发|^原始邮件|^From:\s)",
    re.IGNORECASE,
)
SIG_MARK = re.compile(r"^(--\s*$|发自我的|Sent from my|Best regards,?$)")
LEADING_QUOTE = re.compile(r"^\s*>+\s?")
SIG_TAIL_LINES = 10


def html_to_text(raw):
    if "<" not in raw:
        return raw
    return BeautifulSoup(raw, "html.parser").get_text("\n")


def strip_quotes(text):
    kept, in_quote = [], False
    for line in text.splitlines():
        s = line.strip()
        if HEADER_MARK.match(s):
            continue
        if not in_quote and QUOTE_MARK.search(s):
            in_quote = True
        if in_quote:
            continue
        kept.append(LEADING_QUOTE.sub("", line))
    return "\n".join(kept)


def strip_signature(text):
    lines = text.splitlines()
    cut = len(lines)
    for i, line in enumerate(lines[-SIG_TAIL_LINES:]):
        if SIG_MARK.match(line.strip()):
            cut = len(lines) - SIG_TAIL_LINES + i
            break
    return "\n".join(lines[:cut])


def truncate(text, limit=2000):
    return text[:limit]


def clean_email(raw):
    return truncate(strip_signature(strip_quotes(html_to_text(raw or "")))).strip()
