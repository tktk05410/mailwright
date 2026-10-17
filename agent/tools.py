class Toolbox:
    def __init__(self, retriever, llm, rules, blocklist, top_k=5):
        self.retriever = retriever
        self.llm = llm
        self.rules = rules
        self.blocklist = blocklist
        self.top_k = top_k

    def get_current_email(self, adapter):
        return adapter.current()

    def check_blocklist(self, sender):
        return sender in self.blocklist

    def get_rules(self):
        return list(self.rules)

    def search_handbook(self, query, top_k=None):
        return self.retriever.search(query, "handbook", top_k or self.top_k)

    def search_style_samples(self, situation, top_k=None):
        return self.retriever.search(situation, "style", top_k or 3)

    def classify_email(self, email_text):
        return self.llm.classify(email_text)

    def draft_reply(self, email_text, clauses=None, styles=None):
        clauses = clauses if clauses is not None else self.search_handbook(email_text)
        styles = styles if styles is not None else self.search_style_samples(email_text)
        return self.llm.draft(email_text, clauses, styles, self.get_rules())
