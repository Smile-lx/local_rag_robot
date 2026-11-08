"""入库与删除编排：SQLite（documents/chunks）与 Chroma 向量库的双写/双清。

一致性策略（design.md『RESTful 契约』DELETE 条目：任一失败不落半态）：

- ``ingest_document``：documents/chunks 写入放在单个 SQLite 事务内，Chroma
  写入（先清旧向量再 upsert）发生在事务提交之前——Chroma 失败则 SQLite
  整体回滚；提交前任何异常都会触发补偿（按 doc_id 清 Chroma），新文档
  回到"完全未入库"状态；更新文档失败时旧 SQLite 行保留、该文档向量被清空，
  重新 ingest 同名文档即可恢复。
- ``delete_document``：先 Chroma 后 SQLite。Chroma 删除失败则 SQLite 完全
  未动（保持一致）；SQLite 删除失败时文档行仍在、向量已清，重试本函数
  即可收敛到完全删除（故采用"Chroma 先删"而非反序，反序会留下永久孤儿向量）。

文件名约定：``data/docs/`` 下 ``_`` 开头的文件（如 ``_qa_pairs.md``）为
非入库语料，``ingest_document`` 对 ``_`` 开头的 filename 一律拒绝。

接口（冻结，供 api/routes_documents 与启动时语料入库使用）::

    ingest_document(title, content, filename=None) -> (doc_id, chunk_count)
    delete_document(doc_id) -> int   # 返回删除的切片数
    ingest_docs_dir(docs_dir=None) -> [(filename, doc_id, chunk_count), ...]
"""
from __future__ import annotations

import logging
import re
import sqlite3
import threading

from contextlib import closing
from pathlib import Path

from ..config import DOCS_DIR
from ..db import get_conn, init_db, now_iso, query_all, query_one
from .chunker import Chunk, split
from .vectorstore import add_chunks, delete_by_doc, list_doc_ids

__all__ = [
    "ingest_document",
    "delete_document",
    "ingest_docs_dir",
    "filename_from_title",
    "reconcile",
    "WRITE_LOCK",
]

logger = logging.getLogger(__name__)

#: 进程内写互斥（2026-10-25 加固）：串行化双库结构性写操作（入库/删除/对账），
#: 防止并发请求交叉操作 SQLite 与 Chroma 产生半态。API 路由层与本模块
#: reconcile 均持此锁；跨进程场景由 SQLite busy_timeout（见 config）兜底。
WRITE_LOCK = threading.Lock()

# Windows 文件名非法字符与控制符：替换为下划线，保证 filename 可安全展示
_UNSAFE_FILENAME_RE = re.compile(r'[\\/:*?"<>|\r\n\t]+')
_WHITESPACE_RE = re.compile(r"\s+")
# 语料目录扫描时从正文提取标题：首个一级标题行
_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


def filename_from_title(title: str) -> str:
    """由标题推导确定性文件名（documents.filename 去重键）。

    非法字符与空白替换为下划线，同名标题映射到同一 filename，
    从而重复提交按"更新"幂等处理。
    """
    safe = _WHITESPACE_RE.sub("_", _UNSAFE_FILENAME_RE.sub("_", title.strip()))
    return f"{safe}.md"


def ingest_document(
    title: str, content: str, filename: str | None = None
) -> tuple[int, int]:
    """入库一份文档：切片 → SQLite documents/chunks 双写 → Chroma 向量写入。

    Args:
        title: 文档标题（写入 documents.title 与 Chroma metadata 的 doc_title）。
        content: 文档全文（Markdown 或纯文本）。
        filename: 去重键；缺省时由标题推导。同名 filename 已存在时按"更新"
            处理（doc_id 不变，旧切片整体替换），保证重复 ingest 幂等。

    Returns:
        ``(doc_id, chunk_count)``：文档主键与实际入库切片数。

    Raises:
        ValueError: 标题/内容为空，或 filename 以 ``_`` 开头（约定不入库）。
    """
    title = title.strip()
    if not title:
        raise ValueError("文档标题不能为空")
    if not content.strip():
        raise ValueError("文档内容不能为空")
    doc_filename = (
        filename.strip() if filename and filename.strip() else filename_from_title(title)
    )
    if doc_filename.startswith("_"):
        raise ValueError(
            f"文件名 {doc_filename!r} 以 '_' 开头，按约定保留给非入库文件（如 _qa_pairs.md），"
            "如需入库请更换标题或 filename"
        )
    chunks = split(content)
    init_db()  # 幂等：确保 documents/chunks 表存在（独立调用本函数也可用）

    existing = query_one("SELECT id FROM documents WHERE filename = ?", (doc_filename,))
    doc_id = int(existing["id"]) if existing is not None else -1
    try:
        # 单事务：documents/chunks 任意一步失败整体回滚；Chroma 写入放在
        # 提交前，失败同样回滚 SQLite，不落半态
        with closing(get_conn()) as conn, conn:
            if existing is not None:
                conn.execute(
                    "UPDATE documents SET title = ?, content = ? WHERE id = ?",
                    (title, content, doc_id),
                )
                conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            else:
                cursor = conn.execute(
                    "INSERT INTO documents (title, filename, content, created_at)"
                    " VALUES (?, ?, ?, ?)",
                    (title, doc_filename, content, now_iso()),
                )
                doc_id = int(cursor.lastrowid)
            conn.executemany(
                "INSERT INTO chunks (doc_id, seq, text, section_path) VALUES (?, ?, ?, ?)",
                [(doc_id, chunk.seq, chunk.text, chunk.section_path) for chunk in chunks],
            )
            if existing is not None:
                # 更新场景先清旧向量：新切片数变少时避免 Chroma 残留高 seq 旧记录
                delete_by_doc(doc_id)
            add_chunks(chunks, doc_id, title)
    except Exception:
        # 补偿：尽力清掉本次可能已写入 Chroma 的记录，保证新文档零残留；
        # 更新文档则回到"SQLite 旧行保留、向量待重灌"的可恢复状态
        _compensate_chroma(doc_id, context=f"ingest 失败回滚 (doc_id={doc_id})")
        raise
    logger.info("入库完成: doc_id=%s filename=%s chunks=%s", doc_id, doc_filename, len(chunks))
    return doc_id, len(chunks)


def delete_document(doc_id: int) -> int:
    """删除文档及其全部切片：先清 Chroma 向量，再删 SQLite 两表记录。

    Args:
        doc_id: SQLite documents 表主键。

    Returns:
        被删除的切片条数（SQLite chunks 实际删除行数）。

    Raises:
        LookupError: 文档不存在（API 层据此返回 404）。
        RuntimeError: SQLite 删除失败（此时向量已清、文档行仍在，
            重试本函数可收敛到完全删除）。
    """
    init_db()
    if query_one("SELECT id FROM documents WHERE id = ?", (doc_id,)) is None:
        raise LookupError(f"文档不存在: doc_id={doc_id}")
    # 1) Chroma 先清：此处失败则 SQLite 尚未动过，两侧保持一致，直接上抛
    delete_by_doc(doc_id)
    # 2) SQLite 单事务删除 chunks + documents（不依赖级联，显式两删保证原子）
    try:
        with closing(get_conn()) as conn, conn:
            removed = conn.execute(
                "DELETE FROM chunks WHERE doc_id = ?", (doc_id,)
            ).rowcount
            conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    except sqlite3.Error as exc:
        raise RuntimeError(
            f"SQLite 删除失败（向量已清除，文档行仍在）: doc_id={doc_id}；"
            "重试删除操作可收敛到完全删除"
        ) from exc
    logger.info("删除完成: doc_id=%s chunks_removed=%s", doc_id, removed)
    return int(removed)


def ingest_docs_dir(docs_dir: Path | None = None) -> list[tuple[str, int, int]]:
    """扫描语料目录，入库所有非 ``_`` 开头的 Markdown 文件（启动时语料装载）。

    同名文件重复执行按"更新"幂等处理；标题取正文首个一级标题，缺省回退
    文件名主干（不含扩展名）。

    Args:
        docs_dir: 语料目录，缺省为 ``config.DOCS_DIR``。

    Returns:
        ``(filename, doc_id, chunk_count)`` 列表，按文件名排序；目录不存在
        返回空列表。
    """
    root = docs_dir if docs_dir is not None else DOCS_DIR
    if not root.is_dir():
        logger.warning("语料目录不存在，跳过入库: %s", root)
        return []
    results: list[tuple[str, int, int]] = []
    for path in sorted(root.glob("*.md")):
        if path.name.startswith("_"):
            continue  # '_' 开头为评估/辅助语料，不入库
        content = path.read_text(encoding="utf-8")
        title_match = _H1_RE.search(content)
        title = title_match.group(1) if title_match else path.stem
        doc_id, chunk_count = ingest_document(title, content, filename=path.name)
        results.append((path.name, doc_id, chunk_count))
    return results


def reconcile() -> dict[str, int]:
    """双库对账自愈（启动时由 main 调用，也可独立执行）。

    - Chroma 有而 SQLite 无的 doc_id：孤儿向量，直接清除；
    - SQLite 有而 Chroma 无的 doc_id：按 chunks 表重建向量；chunks 表
      也空（半删半态）时按 documents.content 存档全文重切并补写 chunks。

    Returns:
        ``{"orphan_vectors_removed": int, "vectors_rebuilt": int, "skipped": 0|1}``
        （skipped=1 表示向量库不可读已跳过，仅告警不阻断）。
    """
    stats = {"orphan_vectors_removed": 0, "vectors_rebuilt": 0, "skipped": 0}
    with WRITE_LOCK:
        init_db()
        sqlite_ids = {int(r["id"]) for r in query_all("SELECT id FROM documents")}
        try:
            chroma_ids = list_doc_ids()
        except Exception as exc:  # noqa: BLE001 对账边界容错，见 docstring
            logger.warning("对账跳过（向量库读取失败）: %s", exc)
            stats["skipped"] = 1
            return stats
        for doc_id in sorted(chroma_ids - sqlite_ids):
            delete_by_doc(doc_id)
            stats["orphan_vectors_removed"] += 1
            logger.info("对账: 清理孤儿向量 doc_id=%s", doc_id)
        for doc_id in sorted(sqlite_ids - chroma_ids):
            doc = query_one(
                "SELECT title, content FROM documents WHERE id = ?", (doc_id,)
            )
            if doc is None:
                continue
            rows = query_all(
                "SELECT seq, text, section_path FROM chunks"
                " WHERE doc_id = ? ORDER BY seq ASC",
                (doc_id,),
            )
            if rows:
                chunks = [
                    Chunk(int(r["seq"]), str(r["text"]), str(r["section_path"]))
                    for r in rows
                ]
            else:
                chunks = split(str(doc["content"]))
                with closing(get_conn()) as conn, conn:
                    conn.executemany(
                        "INSERT INTO chunks (doc_id, seq, text, section_path)"
                        " VALUES (?, ?, ?, ?)",
                        [(doc_id, c.seq, c.text, c.section_path) for c in chunks],
                    )
            if not chunks:
                continue
            add_chunks(chunks, doc_id, str(doc["title"]))
            stats["vectors_rebuilt"] += 1
            logger.info("对账: 重建向量 doc_id=%s（%s 片）", doc_id, len(chunks))
    if stats["orphan_vectors_removed"] or stats["vectors_rebuilt"]:
        logger.info("对账完成: %s", stats)
    return stats


def _compensate_chroma(doc_id: int, context: str) -> None:
    """尽力补偿：清除某文档可能已写入 Chroma 的记录（不抛出、只告警）。

    补偿失败仅记录日志，绝不掩盖原始异常——最坏情况是 Chroma 残留待清理
    记录，重新 ingest/删除同一 doc_id 时会被覆盖或清除。
    """
    try:
        delete_by_doc(doc_id)
    except Exception as exc:  # 补偿属清理动作，失败只告警不上抛
        logger.warning("%s: Chroma 补偿清理失败 doc_id=%s: %s", context, doc_id, exc)
