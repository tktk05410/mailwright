"""向量与 BM25 双索引。"""

import os
import zlib

import jieba
import numpy as np
from rank_bm25 import BM25Okapi

from core.models import Chunk

MODEL_NAME = "BAAI/bge-small-zh-v1.5"
QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："  # bge 查询前缀
BGE_DIM = 512
HASH_DIM = 512


class Embedder:
    """文本转向量，失败兜底。"""

    def __init__(self, model_name=MODEL_NAME):
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        self.name = model_name
        self.model = None
        try:
            # 先按离线加载
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
            from sentence_transformers import SentenceTransformer
            self.model = SentenceTransformer(model_name)
        except Exception:
            # 失败再联网，再失败用哈希
            os.environ.pop("HF_HUB_OFFLINE", None)
            os.environ.pop("TRANSFORMERS_OFFLINE", None)
            try:
                from sentence_transformers import SentenceTransformer
                self.model = SentenceTransformer(model_name)
            except Exception:
                self.model = None
        self.dim = BGE_DIM if self.model else HASH_DIM

    @property
    def signature(self):
        """索引缓存签名。"""
        return f"{self.name}|{self.dim}" if self.model else f"hash|{self.dim}"

    def _encode(self, texts, prefix=""):
        """编码并归一化。"""
        if self.model is not None:
            batch = [prefix + t for t in texts]
            return np.asarray(self.model.encode(batch, normalize_embeddings=True), dtype=np.float32)
        # 兜底：2-gram 哈希
        vecs = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            grams = {t[j:j + 2] for j in range(len(t) - 1)} or {t}
            for g in grams:
                vecs[i, zlib.crc32(g.encode("utf-8")) % self.dim] += 1.0
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vecs / norms

    def encode(self, texts):
        """批量编码知识库。"""
        return self._encode(list(texts))

    def encode_query(self, query):
        """编码查询。"""
        prefix = QUERY_PREFIX if self.model is not None else ""
        return self._encode([query], prefix=prefix)[0]


class KnowledgeIndex:
    """按 kind 维护两套索引。"""

    def __init__(self, embedder=None):
        self.embedder = embedder or Embedder()
        self.chunks = []
        self.bm25 = {}
        self.vec = {}
        self.order = {}

    def rebuild(self, chunks):
        """全量重建索引。"""
        self.chunks = list(chunks)
        self.bm25, self.vec, self.order = {}, {}, {}
        for kind in {c.kind for c in self.chunks}:
            group = [c for c in self.chunks if c.kind == kind]
            corpus = [list(jieba.cut_for_search(c.text)) for c in group]
            self.order[kind] = group
            self.bm25[kind] = BM25Okapi(corpus)
            self.vec[kind] = self.embedder.encode([c.text for c in group])

    def bm25_search(self, query, kind, top_k):
        """按 BM25 取前 k。"""
        # 单篇时 IDF 为负，跳过
        if kind not in self.bm25 or len(self.order.get(kind, [])) < 2:
            return []
        scores = self.bm25[kind].get_scores(list(jieba.cut_for_search(query)))
        idx = np.argsort(scores)[::-1][:top_k]
        return [(self.order[kind][i], float(scores[i])) for i in idx if scores[i] > 0]

    def vector_search(self, query, kind, top_k, min_cos=0.0):
        """按余弦相似度取前 k。"""
        if kind not in self.vec or len(self.vec[kind]) == 0:
            return []
        q = self.embedder.encode_query(query)
        sims = self.vec[kind] @ q
        idx = np.argsort(sims)[::-1][:top_k]
        return [(self.order[kind][i], float(sims[i])) for i in idx if sims[i] >= min_cos]
