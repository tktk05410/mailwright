class Orchestrator:
    def __init__(self, tools):
        self.tools = tools

    def run(self, msg):
        if self.tools.check_blocklist(msg.sender):
            return {"blocked": True, "sender": msg.sender}
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
            "labels": info.get("labels", []),
            "need_reply": bool(info.get("need_reply", True)),
            "summary": info.get("summary", ""),
            "draft": out.get("draft", ""),
            "cited_clauses": out.get("cited_clauses", []),
            "confidence": confidence,
            "engine_error": self.tools.llm.last_error,
            "clauses": [
                {
                    "clause_no": c.metadata.get("clause_no", ""),
                    "section_path": c.metadata.get("section_path", ""),
                    "text": c.text[:80],
                }
                for c in clauses
            ],
        }
