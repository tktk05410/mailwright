"""手册切块：按标题分层并保留条款号。"""

import re

from core.models import Chunk

MD_HEAD = re.compile(r"^(#{1,6})\s+(.+?)\s*$")  # Markdown 标题
NUM_HEAD = re.compile(r"^(\d+(?:\.\d+)*)[\.、]?\s+(\S.*)$")  # 编号标题
CLAUSE = re.compile(r"\d+(?:\.\d+)+")  # 提取条款号
END_PUNCT = ("。", "！", "？", "，", "；", "：")
SENT_SPLIT = re.compile(r"(?<=[。！？；])")  # 按句切分
SPLIT_OVERLAP = 50  # 重叠 50 字
PREAMBLE = "前言"


def _split_sentence(text, limit):
    """按句切分并留重叠。"""
    parts, buf = [], ""
    for sent in SENT_SPLIT.split(text):
        sent = sent.strip()
        if not sent:
            continue
        # 超长单句硬切
        while len(sent) > limit:
            if buf:
                parts.append(buf)
                buf = ""
            parts.append(sent[:limit])
            sent = sent[limit - SPLIT_OVERLAP:]
        if buf and len(buf) + len(sent) + 1 > limit:
            parts.append(buf)
            buf = buf[-SPLIT_OVERLAP:] + sent
        else:
            buf = f"{buf}{sent}" if buf else sent
    if buf:
        parts.append(buf)
    return parts


def _split_long(text, limit=500):
    """按空行分段并限长。"""
    if len(text) <= limit:
        return [text]
    parts, buf = [], ""
    for para in text.split("\n\n"):
        if len(para) > limit:
            if buf:
                parts.append(buf)
                buf = ""
            parts.extend(_split_sentence(para, limit))
            continue
        if buf and len(buf) + len(para) + 2 > limit:
            parts.append(buf)
            buf = para
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        parts.append(buf)
    return parts


def chunk_handbook(text, doc="员工手册"):
    """切成带章节路径的块。"""
    stack, blocks, cur, path = [], [], [], PREAMBLE

    # 第一遍：按标题分组
    for line in text.splitlines():
        md = MD_HEAD.match(line)
        num = None
        stripped = line.strip()
        # 排除正文句子
        if md is None and len(stripped) < 50 and not stripped.endswith(END_PUNCT):
            num = NUM_HEAD.match(stripped)
        if md or num:
            if cur:
                blocks.append({"path": path, "lines": cur})
            if md:
                level, title = len(md.group(1)), md.group(2).strip()
            else:
                level = 1 + num.group(1).count(".")
                title = f"{num.group(1)} {num.group(2).strip()}"
            # 维护标题层级栈
            stack = [s for s in stack if s[0] < level]
            stack.append((level, title))
            path = "/".join(t for _, t in stack)
            cur = []
        else:
            cur.append(line)
    if cur:
        blocks.append({"path": path, "lines": cur})

    # 第二遍：切分并编号
    chunks, seq = [], 0
    safe_doc = re.sub(r"[^\w\u4e00-\u9fa5-]", "_", doc or "doc")
    for b in blocks:
        body = "\n".join(b["lines"]).strip()
        if not body:
            continue
        m = CLAUSE.search(b["path"])
        clause = m.group(0) if m else ""
        for i, part in enumerate(_split_long(body), 1):
            seq += 1
            # 无条款号用序号
            key = clause or f"s{seq}"
            chunks.append(
                Chunk(
                    chunk_id=f"hb_{safe_doc}_{key}_{i}",
                    text=part,
                    kind="handbook",
                    metadata={
                        "doc": doc,
                        "section_path": b["path"],
                        "clause_no": clause,
                    },
                )
            )
    return chunks
