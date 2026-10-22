"""混合检索：BM25 与向量。"""

from datetime import datetime

RRF_K = 60  # 平滑常数
HALF_LIFE_DAYS = 30.0  # 30 天权重减半


def rrf_merge(rankings):
    """按倒数名次融合。"""
    scores = {}
    for ranking in rankings:
        for r, (chunk, _) in enumerate(ranking):
            scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + 1.0 / (RRF_K + r + 1)
    return scores


def _recency_decay(chunk, now):
    """越新权重越高。"""
    d = chunk.metadata.get("date", "")
    if not d:
        return 1.0
    try:
        days = max((now - datetime.fromisoformat(d)).days, 0)
    except ValueError:
        return 1.0
    return 1.0 / (1.0 + days / HALF_LIFE_DAYS)


class Retriever:
    """合并两路检索结果。"""

    def __init__(self, index, min_cos=0.0, min_rrf=0.0, disabled_docs=(), vector_fallback=True):
        self.index = index
        self.min_cos = min_cos
        # min_rrf=0.02 等价两路都命中
        self.min_rrf = min_rrf
        self.disabled_docs = set(disabled_docs)
        # 只对向量路放开单路命中
        self.vector_fallback = vector_fallback

    def search(self, query, kind, top_k=5, now=None):
        """双路召回后融合排序。"""
        bm25_hits = self.index.bm25_search(query, kind, top_k * 2)
        vec_hits = self.index.vector_search(query, kind, top_k * 2, min_cos=self.min_cos)
        if not bm25_hits and not vec_hits:
            return []
        fused = rrf_merge([bm25_hits, vec_hits])

        # 去重并过滤禁用手册
        merged = {}
        for ranking in (bm25_hits, vec_hits):
            for chunk, _ in ranking:
                if chunk.metadata.get("doc", "") in self.disabled_docs:
                    continue
                merged.setdefault(chunk.chunk_id, chunk)

        # 手册严格，语气宽松
        floor = self.min_rrf if kind == "handbook" else 0.0
        vec_ids = {chunk.chunk_id for chunk, _ in vec_hits}
        allow_fallback = self.vector_fallback and kind == "handbook"

        def keep(chunk):
            # 两路都命中
            if fused[chunk.chunk_id] >= floor:
                return True
            # 向量单路兜底
            return allow_fallback and chunk.chunk_id in vec_ids

        ranked = sorted(
            (c for c in merged.values() if keep(c)),
            key=lambda c: fused[c.chunk_id],
            reverse=True,
        )
        if kind == "style":
            now = now or datetime.now()
            ranked = sorted(
                ranked, key=lambda c: fused[c.chunk_id] * _recency_decay(c, now), reverse=True
            )
        return ranked[:top_k]
