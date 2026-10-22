"""固定流水线：分类→检索→起草。"""


class Orchestrator:
    """串联各工具动作。"""

    def __init__(self, tools):
        self.tools = tools

    def run(self, msg):
        """跑一次完整流程。"""
        # 只反映本轮错误
        self.tools.llm.last_error = ""

        info = self.tools.classify_email(msg.body_text)
        clauses = self.tools.search_handbook(msg.body_text)
        styles = self.tools.search_style_samples(msg.body_text)
        out = self.tools.draft_reply(msg.body_text, clauses, styles)

        try:
            confidence = float(out.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        return {
            "blocked": False,
            "offline": not self.tools.llm.online,
            "labels": info.get("labels", []),
            "need_reply": bool(info.get("need_reply", True)),
            "summary": info.get("summary", ""),
            "draft": out.get("draft", ""),
            "cited_clauses": out.get("cited_clauses", []),
            "confidence": confidence,
            "engine_error": self.tools.llm.last_error,
            # 正文截断 80 字
            "clauses": [
                {
                    "clause_no": c.metadata.get("clause_no", ""),
                    "section_path": c.metadata.get("section_path", ""),
                    "text": c.text[:80],
                }
                for c in clauses
            ],
        }
