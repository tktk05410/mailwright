from datetime import datetime

RRF_K = 60
HALF_LIFE_DAYS = 30.0


def rrf_merge(rankings):
    scores = {}
    for ranking in rankings:
        for r, (chunk, _) in enumerate(ranking):
            scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + 1.0 / (RRF_K + r + 1)
    return scores


def _recency_decay(chunk, now):
    d = chunk.metadata.get("date", "")
    if not d:
        return 1.0
    try:
        days = max((now - datetime.fromisoformat(d)).days, 0)
    except ValueError:
        return 1.0
    return 1.0 / (1.0 + days / HALF_LIFE_DAYS)


class Retriever:
    def __init__(self, index, min_cos=0.0, min_rrf=0.0, disabled_docs=()):
        self.index = index
        self.min_cos = min_cos
        self.min_rrf = min_rrf
        self.disabled_docs = set(disabled_docs)

    def search(self, query, kind, top_k=5, now=None):
        bm25_hits = self.index.bm25_search(query, kind, top_k * 2)
        vec_hits = self.index.vector_search(query, kind, top_k * 2, min_cos=self.min_cos)
        if not bm25_hits and not vec_hits:
            return []
        fused = rrf_merge([bm25_hits, vec_hits])
        merged = {}
        for ranking in (bm25_hits, vec_hits):
            for chunk, _ in ranking:
                if chunk.metadata.get("doc", "") in self.disabled_docs:
                    continue
                merged.setdefault(chunk.chunk_id, chunk)
        ranked = sorted(
            (c for c in merged.values() if fused[c.chunk_id] >= self.min_rrf),
            key=lambda c: fused[c.chunk_id],
            reverse=True,
        )
        if kind == "style":
            now = now or datetime.now()
            ranked = sorted(ranked, key=lambda c: fused[c.chunk_id] * _recency_decay(c, now), reverse=True)
        return ranked[:top_k]
