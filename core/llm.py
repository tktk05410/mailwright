import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from agent.prompts import CLASSIFY_SYSTEM, LABELS, build_draft_messages

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
TIMEOUT = 30.0
RETRY_TIMES = 3
CONFIG_PATH = Path(__file__).parent.parent / "config.json"

_PLATFORMS = [
    ("deepseek", "DeepSeek"),
    ("openai", "OpenAI"),
    ("moonshot", "Moonshot"),
    ("bigmodel", "智谱 GLM"),
    ("dashscope", "通义千问"),
    ("siliconflow", "SiliconFlow"),
    ("minimax", "MiniMax"),
    ("stepfun", "阶跃星辰"),
]


def _gen_params():
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    return {
        "temperature": float(cfg.get("temperature", 0.3)),
        "max_tokens": int(cfg.get("max_tokens", 1024)),
    }


def _platform_name(base_url):
    host = urlparse(base_url).netloc or base_url
    for key, name in _PLATFORMS:
        if key in host:
            return name
    return host


def _extract_json(text):
    text = (text or "").strip()
    for cand in (
        text,
        re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.M).strip(),
    ):
        try:
            out = json.loads(cand)
            return out if isinstance(out, dict) else {}
        except Exception:
            pass
    i, j = text.find("{"), text.rfind("}")
    if i >= 0 and j > i:
        try:
            out = json.loads(text[i:j + 1])
            return out if isinstance(out, dict) else {}
        except Exception:
            return {}
    return {}


class ChatLLM:
    def __init__(self):
        key = os.getenv("LLM_API_KEY", "").strip()
        base_url = os.getenv("LLM_BASE_URL", "").strip() or DEFAULT_BASE_URL
        self.model = os.getenv("LLM_MODEL", "").strip() or DEFAULT_MODEL
        self.platform = _platform_name(base_url)
        self._client = None
        self.last_error = ""
        if key:
            from openai import OpenAI
            self._client = OpenAI(api_key=key, base_url=base_url, timeout=TIMEOUT)

    @property
    def online(self):
        return self._client is not None

    @property
    def mode(self):
        return f"{self.platform} 在线" if self._client else "离线演示"

    def ping(self):
        if self._client is None:
            raise RuntimeError("未配置 API Key")
        self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=8,
            timeout=15,
        )

    def _chat(self, system, user):
        params = _gen_params()
        last = None
        for attempt in range(RETRY_TIMES):
            try:
                resp = self._client.chat.completions.create(
                    model=self.model,
                    temperature=params["temperature"],
                    max_tokens=params["max_tokens"],
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                )
                return _extract_json(resp.choices[0].message.content)
            except Exception as ex:
                last = ex
                if attempt < RETRY_TIMES - 1:
                    time.sleep(1.0 + attempt)
        raise last

    def classify(self, email_text):
        if self._client is None:
            return mock_classify(email_text)
        try:
            out = self._chat(CLASSIFY_SYSTEM, email_text)
        except Exception as ex:
            self.last_error = f"在线分类失败，已降级：{ex}"
            return mock_classify(email_text)
        if not isinstance(out.get("labels"), list):
            self.last_error = "在线分类返回格式异常，已降级"
            return mock_classify(email_text)
        out["labels"] = [x for x in out["labels"] if x in LABELS] or ["其他"]
        return out

    def draft(self, email_text, clauses, styles, rules):
        if self._client is None:
            return mock_draft(email_text, clauses)
        try:
            out = self._chat(*build_draft_messages(email_text, clauses, styles, rules))
        except Exception as ex:
            self.last_error = f"在线生成失败，已降级：{ex}"
            return mock_draft(email_text, clauses)
        if not str(out.get("draft", "")).strip():
            self.last_error = "在线草稿为空，已降级"
            return mock_draft(email_text, clauses)
        cited = out.get("cited_clauses")
        out["cited_clauses"] = [str(n) for n in cited] if isinstance(cited, list) else []
        return out


_CLASSIFY_RULES = [
    ("客户询价", ("报价", "价格", "采购", "quote")),
    ("催办", ("催", "进度", "跟进", "何时")),
    ("会议邀约", ("会议", "开会", "议程", "日程")),
    ("通知", ("通知", "公告", "须知")),
]


def mock_classify(email_text):
    labels = [name for name, words in _CLASSIFY_RULES if any(w in email_text for w in words)]
    if not labels:
        labels = ["其他"]
    need_reply = any(x in labels for x in ("客户询价", "催办", "会议邀约"))
    summary = re.split(r"[。\n]", email_text.strip())[0][:40] if email_text.strip() else ""
    return {"labels": labels, "need_reply": need_reply, "summary": summary}


def mock_draft(email_text, clauses):
    if any(w in email_text for w in ("报价", "价格", "采购")):
        body = "关于您咨询的报价事宜，我们需按内部审批流程确认后另行回复，暂无法在邮件中给出具体金额。"
    elif any(w in email_text for w in ("交付", "时间", "进度", "上线")):
        body = "相关工作正在按计划推进，具体完成时间需与团队确认后另行同步，届时第一时间告知您。"
    else:
        body = "您反馈的事项已收到，我会尽快核对相关情况并回复您。"
    cites = [c.metadata.get("clause_no", "") for c in clauses if c.metadata.get("clause_no")][:3]
    return {
        "draft": f"您好，感谢来信。{body}如有其他问题，欢迎随时联系。",
        "cited_clauses": cites,
        "confidence": 0.5,
    }
