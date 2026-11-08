"""Markdown 文本切片器（纯函数模块：无 IO、无内部依赖、仅标准库）。

切片策略（对应 design.md『切片策略』一节）：
1. 先按 Markdown 标题（正则 ``^#{1,6}\\s``，MULTILINE）把全文切成逻辑段，
   ``section_path`` 为沿途各级标题以 " / " 拼接（标题层级回退时自动截断）；
2. 段内超过 SECTION_MAX(500) 字时按分隔符优先级
   ``["\\n\\n", "\\n", "。", "；", "，", ""]`` 递归字符切，
   贪心打包成 CHUNK_MIN~CHUNK_MAX(400~500) 字的块，相邻块重叠 OVERLAP(70) 字；
3. 长度小于 MERGE_MIN(50) 字的碎片并入相邻块：非末块并入下一段、
   末块并入上一段，避免检索噪声。

块长约定：常规块 ≤ CHUNK_MAX(500) 字；若碎片被并入相邻块，
绝对上限为 CHUNK_MAX + MERGE_MIN - 1(549) 字。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SECTION_MAX: int = 500  # 逻辑段超过该长度才进入递归字符切
CHUNK_MIN: int = 400  # 打包目标块长下限
CHUNK_MAX: int = 500  # 单块长度硬上限
MERGE_MIN: int = 50  # 小于该长度的碎片并入相邻块
OVERLAP: int = 70  # 相邻块重叠字数（要求 60~80）
HARD_SPLIT_STEP: int = CHUNK_MAX - OVERLAP  # 无分隔符时硬切兜底步长

_SEPARATORS: tuple[str, ...] = ("\n\n", "\n", "。", "；", "，", "")
_HEADING_RE = re.compile(r"^(#{1,6})\s(.*)$", re.MULTILINE)


@dataclass
class Chunk:
    """切片结果载体：块序号、原文文本、所属标题路径。"""

    seq: int
    text: str
    section_path: str


def split(text: str) -> list[Chunk]:
    """把 Markdown 全文切成带标题路径的切片列表。

    Args:
        text: 文档全文（Markdown 或纯文本）。

    Returns:
        切片列表，seq 从 0 连续编号；空白文本返回空列表。
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.strip():
        return []
    sections = _merge_small_sections(_extract_sections(normalized))
    chunks: list[Chunk] = []
    for path, content in sections:
        for piece in _split_section(content):
            chunks.append(Chunk(seq=len(chunks), text=piece, section_path=path))
    return chunks


def _extract_sections(text: str) -> list[tuple[str, str]]:
    """按标题切逻辑段，返回 (section_path, 段内容含标题行) 列表。"""
    matches = list(_HEADING_RE.finditer(text))
    sections: list[tuple[str, str]] = []
    first_start = matches[0].start() if matches else len(text)
    preamble = text[:first_start].strip()
    if preamble:
        sections.append(("", preamble))
    stack: dict[int, str] = {}
    for index, match in enumerate(matches):
        level = len(match.group(1))
        stack[level] = match.group(2).strip()
        for deeper in [key for key in stack if key > level]:
            del stack[deeper]
        path = " / ".join(stack[key] for key in sorted(stack))
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end():end].strip()
        heading_line = match.group(0).strip()
        content = heading_line if not body else f"{heading_line}\n{body}"
        sections.append((path, content))
    return sections


def _merge_small_sections(sections: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """把长度小于 MERGE_MIN 的整段并入相邻段（非末段并入下一段，末段并入上一段）。"""
    merged: list[tuple[str, str]] = []
    pending: tuple[str, str] | None = None
    for path, content in sections:
        if pending is not None:
            content = f"{pending[1]}\n{content}" if content else pending[1]
            pending = None
        if len(content) < MERGE_MIN:
            pending = (path, content)
            continue
        merged.append((path, content))
    if pending is not None:
        if merged:
            last_path, last_content = merged[-1]
            merged[-1] = (last_path, f"{last_content}\n{pending[1]}")
        else:
            merged.append(pending)
    return merged


def _split_section(content: str) -> list[str]:
    """切单个逻辑段：不超过 SECTION_MAX 整段保留，否则递归切分后重叠打包。"""
    if len(content) <= SECTION_MAX:
        return [content]
    pieces, joins = _recursive_split(content, _SEPARATORS)
    return _pack_pieces(pieces, joins)


def _recursive_split(
    text: str, separators: tuple[str, ...]
) -> tuple[list[str], list[str]]:
    """按分隔符优先级递归切分超长文本为原子片段。

    Args:
        text: 待切分文本（长度大于 CHUNK_MAX）。
        separators: 分隔符优先级列表，"" 表示无分隔符硬切兜底。

    Returns:
        (片段列表, 相邻片段间的连接符列表)；连接符长度为片段数减一，
        用于打包时原样还原文本。片段长度均不超过 CHUNK_MAX。
    """
    if len(text) <= CHUNK_MAX:
        return [text], []
    for index, sep in enumerate(separators):
        if sep == "":
            step = HARD_SPLIT_STEP
            pieces = [text[start:start + step] for start in range(0, len(text), step)]
            return pieces, [""] * (len(pieces) - 1)
        if sep not in text:
            continue
        pieces: list[str] = []
        joins: list[str] = []
        for part in text.split(sep):
            part = part.strip()
            if not part:
                continue
            if len(part) > CHUNK_MAX:
                sub_pieces, sub_joins = _recursive_split(part, separators[index + 1:])
            else:
                sub_pieces, sub_joins = [part], []
            if pieces:
                joins.append(sep)
            pieces.extend(sub_pieces)
            joins.extend(sub_joins)
        return pieces, joins
    return [text], []


def _pack_pieces(pieces: list[str], joins: list[str]) -> list[str]:
    """把原子片段贪心打包成 CHUNK_MIN~CHUNK_MAX 字的块，相邻块重叠 OVERLAP 字。"""
    chunks: list[str] = []
    current = ""
    for index, piece in enumerate(pieces):
        if not piece:
            continue
        join = joins[index - 1] if index > 0 else ""
        if not current:
            current = piece
        elif len(current) + len(join) + len(piece) <= CHUNK_MAX:
            current = f"{current}{join}{piece}"
        else:
            chunks.append(current)
            tail = current[-OVERLAP:]
            if len(tail) + len(join) + len(piece) <= CHUNK_MAX:
                current = f"{tail}{join}{piece}"
            else:
                current = piece
    if current:
        chunks.append(current)
    return _merge_small_chunks(chunks)


def _merge_small_chunks(chunks: list[str]) -> list[str]:
    """把长度小于 MERGE_MIN 的碎片并入相邻块（非末块并入下一段，末块并入上一段）。"""
    result: list[str] = []
    pending = ""
    for chunk in chunks:
        if pending:
            chunk = f"{pending}\n{chunk}" if chunk else pending
            pending = ""
        if len(chunk) < MERGE_MIN:
            pending = chunk
            continue
        result.append(chunk)
    if pending:
        if result:
            result[-1] = f"{result[-1]}\n{pending}"
        else:
            result.append(pending)
    return result
