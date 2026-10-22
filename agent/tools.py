"""工具层，供编排器调用。"""


class Toolbox:
    """工具箱。"""

    def __init__(self, retriever, llm, rules, top_k=5):
        self.retriever = retriever
        self.llm = llm
        self.rules = rules
        self.top_k = top_k

    def get_rules(self):
        """取回复规则副本。"""
        return list(self.rules)

    def search_handbook(self, query, top_k=None):
        """检索相关手册条款。"""
        return self.retriever.search(query, "handbook", top_k or self.top_k)

    def search_style_samples(self, situation, top_k=None):
        """检索历史邮件学语气。"""
        return self.retriever.search(situation, "style", top_k or 3)

    def classify_email(self, email_text):
        """分类并生成摘要。"""
        return self.llm.classify(email_text)

    def draft_reply(self, email_text, clauses=None, styles=None):
        """结合条款与语气起草。"""
        clauses = clauses if clauses is not None else self.search_handbook(email_text)
        styles = styles if styles is not None else self.search_style_samples(email_text)
        return self.llm.draft(email_text, clauses, styles, self.get_rules())
