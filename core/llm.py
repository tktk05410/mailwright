"""LLM 客户端，在线优先、失败降级。"""

import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from agent.prompts import CLASSIFY_SYSTEM, LABELS, build_draft_messages

# 默认服务与重试参数
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
TIMEOUT = 30.0
RETRY_TIMES = 3
CONFIG_PATH = Path(__file__).parent.parent / "config.json"

# 域名映射平台名
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
    """读一次生成参数。"""
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    return {
        "temperature": float(cfg.get("temperature", 0.3)),
        "max_tokens": int(cfg.get("max_tokens", 1024)),
    }


def _platform_name(base_url):
    """域名转平台名。"""
    host = urlparse(base_url).netloc or base_url
    for key, name in _PLATFORMS:
        if key in host:
            return name
    return host


def _extract_json(text):
    """容错提取 JSON。"""
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
    """LLM 统一入口。"""

    def __init__(self):
        key = os.getenv("LLM_API_KEY", "").strip()
        base_url = os.getenv("LLM_BASE_URL", "").strip() or DEFAULT_BASE_URL
        self.model = os.getenv("LLM_MODEL", "").strip() or DEFAULT_MODEL
        self.platform = _platform_name(base_url)
        self._client = None
        self.last_error = ""
        if key:
            # 无 Key 则走离线 mock
            from openai import OpenAI
            self._client = OpenAI(api_key=key, base_url=base_url, timeout=TIMEOUT)

    @property
    def online(self):
        """是否在线可用。"""
        return self._client is not None

    @property
    def mode(self):
        """状态栏文案。"""
        return f"{self.platform} 在线" if self._client else "离线演示"

    def ping(self):
        """测试连通性。"""
        if self._client is None:
            raise RuntimeError("未配置 API Key")
        self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=8,
            timeout=15,
        )

    def _chat(self, system, user):
        """调用对话接口。"""
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
                    time.sleep(1.0 + attempt)  # 简单退避
        raise last

    def classify(self, email_text):
        """分类并生成摘要。"""
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
        # 只保留白名单标签
        out["labels"] = [x for x in out["labels"] if x in LABELS] or ["其他"]
        return out

    def draft(self, email_text, clauses, styles, rules):
        """生成回复草稿。"""
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
        # 条款号转字符串
        cited = out.get("cited_clauses")
        out["cited_clauses"] = [str(n) for n in cited] if isinstance(cited, list) else []
        return out


# 离线关键词规则
_CLASSIFY_RULES = [
    ("客户询价", ("报价", "价格", "采购", "quote")),
    ("催办", ("催", "进度", "跟进", "何时")),
    ("会议邀约", ("会议", "开会", "议程", "日程")),
    ("通知", ("通知", "公告", "须知")),
]


def mock_classify(email_text):
    """按关键词离线分类。"""
    labels = [name for name, words in _CLASSIFY_RULES if any(w in email_text for w in words)]
    if not labels:
        labels = ["其他"]
    need_reply = any(x in labels for x in ("客户询价", "催办", "会议邀约"))
    summary = re.split(r"[。\n]", email_text.strip())[0][:40] if email_text.strip() else ""
    return {"labels": labels, "need_reply": need_reply, "summary": summary}


def mock_draft(email_text, clauses):
    """按关键词离线起草。"""
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
