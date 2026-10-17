import json
from pathlib import Path

import jieba
import numpy as np
from rank_bm25 import BM25Okapi

from core.models import Chunk
from preprocess.chunker import chunk_handbook
from rag.indexer import KnowledgeIndex

DOC_EXTS = (".md", ".txt", ".pdf", ".docx")


def read_document(path):
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in (".md", ".txt"):
        try:
            return path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            try:
                return path.read_text(encoding="gbk")
            except UnicodeDecodeError as ex:
                raise ValueError(f"无法解码文件（请另存为 UTF-8）：{path.name}") from ex
    if suffix == ".pdf":
        import pymupdf
        with pymupdf.open(path) as doc:
            return "\n".join(page.get_text() for page in doc)
    if suffix == ".docx":
        from docx import Document
        d = Document(str(path))
        parts = [p.text for p in d.paragraphs]
        for table in d.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells]
                if any(cells):
                    parts.append(" ｜ ".join(cells))
        return "\n".join(parts)
    raise ValueError(f"暂不支持的格式：{suffix}")


def load_handbook_chunks(folder, exclude=()):
    folder = Path(folder)
    chunks = []
    if not folder.exists():
        return chunks
    for f in sorted(folder.iterdir()):
        if f.is_file() and f.suffix.lower() in DOC_EXTS and f.name not in exclude:
            chunks.extend(chunk_handbook(read_document(f), f.stem))
    return chunks


def load_style_chunks(path, prefix="st"):
    p = Path(path)
    if not p.exists():
        return []
    try:
        items = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(items, list):
            raise ValueError("数据不是列表")
    except Exception:
        return []
    return [
        Chunk(
            chunk_id=f"{prefix}_{i}",
            text=it.get("body", ""),
            kind="style",
            metadata={"to": it.get("to", ""), "subject": it.get("subject", ""), "date": it.get("date", "")},
        )
        for i, it in enumerate(items, 1)
        if isinstance(it, dict) and it.get("body", "")
    ]


def sources_snapshot(folder, extra_files=()):
    snap = {}
    folder = Path(folder)
    if folder.exists():
        for f in sorted(folder.iterdir()):
            if f.is_file():
                snap[str(f.name)] = [round(f.stat().st_mtime, 2), f.stat().st_size]
    for f in extra_files:
        p = Path(f)
        if p.exists():
            snap[p.name] = [round(p.stat().st_mtime, 2), p.stat().st_size]
    return snap


def save_index(index, folder, sources=None):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    payload = {
        "signature": index.embedder.signature,
        "sources": sources or {},
        "kinds": {kind: [c.__dict__ for c in group] for kind, group in index.order.items()},
    }
    (folder / "chunks.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    np.savez(folder / "vectors.npz", **index.vec)


def load_index(folder, embedder=None, sources=None):
    folder = Path(folder)
    if not (folder / "chunks.json").exists():
        return None
    try:
        payload = json.loads((folder / "chunks.json").read_text(encoding="utf-8"))
    except Exception:
        return None
    index = KnowledgeIndex(embedder)
    if payload.get("signature") != index.embedder.signature:
        return None
    if payload.get("sources") != (sources or {}):
        return None
    vectors = np.load(folder / "vectors.npz")
    for kind, items in payload["kinds"].items():
        group = [Chunk(**it) for it in items]
        index.order[kind] = group
        index.chunks.extend(group)
        index.bm25[kind] = BM25Okapi([list(jieba.cut_for_search(c.text)) for c in group])
        index.vec[kind] = np.asarray(vectors[kind], dtype=np.float32)
    return index
