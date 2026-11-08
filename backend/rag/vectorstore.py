"""Chroma 向量库封装：持久化集合 ``spd_chunks`` 的增删查。

- 使用 chromadb.PersistentClient 持久化到 ``config.VECTOR_DIR``（默认
  ``data/vectorstore``）；
- 集合名 ``spd_chunks``，距离度量余弦：metadata ``{"hnsw:space": "cosine"}``；
- 记录 id 用 ``f"{doc_id}:{seq}"``，metadata 存
  ``doc_id / seq / section_path / doc_title``，与 SQLite 的 chunks 表一一对应；
- 向量一律由本包 ``embedder``（bge-small-zh-v1.5）生成后显式传入
  （``embeddings=`` / ``query_embeddings=``），不使用 Chroma 内置的英文默认
  嵌入函数，否则中文语料检索质量失效；
- Chroma 返回的距离为 ``1 - 余弦相似度``，score 越大越相关，按 score 降序返回。

接口（冻结，供 ingest / qa / api-documents 使用）::

    add_chunks(chunks: list[Chunk], doc_id: int, doc_title: str) -> None
    search(query: str, k: int = 4) -> list[Hit]
    delete_by_doc(doc_id: int) -> None
    count() -> int
"""
from __future__ import annotations

from dataclasses import dataclass

import chromadb
from chromadb import Collection, PersistentClient
from chromadb.errors import NotFoundError

from ..config import VECTOR_DIR
from . import embedder
from .chunker import Chunk

__all__ = ["Hit", "add_chunks", "search", "delete_by_doc", "count", "list_doc_ids"]

COLLECTION_NAME: str = "spd_chunks"
_METADATA: dict[str, str] = {"hnsw:space": "cosine"}

# 进程级单例：PersistentClient 打开锁定的存储目录，避免重复创建
_client: PersistentClient | None = None


@dataclass
class Hit:
    """一次检索命中的切片：定位信息、原文与相关度分数。"""

    doc_id: int
    seq: int
    text: str
    score: float
    doc_title: str
    section_path: str


def _get_client() -> PersistentClient:
    """懒加载并缓存全局唯一的 PersistentClient。"""
    global _client
    if _client is not None:
        return _client
    VECTOR_DIR.mkdir(parents=True, exist_ok=True)
    _client = chromadb.PersistentClient(path=str(VECTOR_DIR))
    return _client


def _get_collection() -> Collection:
    """取集合，不存在则按余弦度量创建（幂等）。"""
    return _get_client().get_or_create_collection(
        name=COLLECTION_NAME, metadata=_METADATA
    )


def _find_collection() -> Collection | None:
    """查询用：集合不存在（尚未入库过任何文档）时返回 None 而不是抛错。"""
    try:
        return _get_client().get_collection(name=COLLECTION_NAME)
    except NotFoundError:
        return None


def _id(doc_id: int, seq: int) -> str:
    """Chroma 记录 id：与 SQLite chunks 表 (doc_id, seq) 一一对应。"""
    return f"{doc_id}:{seq}"


def add_chunks(chunks: list[Chunk], doc_id: int, doc_title: str) -> None:
    """把一批切片写入向量库（仅 Chroma，SQLite 由 ingest 负责）。

    向量先经 ``embedder.embed_documents`` 批量编码（L2 归一化），再以
    ``embeddings=`` 显式传入 upsert；覆盖写语义，同 id 记录会被替换。

    Args:
        chunks: 切片列表（来自 chunker.split，seq 已连续编号）。
        doc_id: SQLite documents 表主键。
        doc_title: 文档标题，写入每条记录的 metadata 便于溯源。

    Raises:
        ValueError: chunks 为空时拒绝入库，避免脏写入。
    """
    if not chunks:
        raise ValueError("add_chunks 收到空切片列表，拒绝写入")
    embeddings = embedder.embed_documents([chunk.text for chunk in chunks])
    _get_collection().upsert(
        ids=[_id(doc_id, chunk.seq) for chunk in chunks],
        embeddings=embeddings.tolist(),
        documents=[chunk.text for chunk in chunks],
        metadatas=[
            {
                "doc_id": doc_id,
                "seq": chunk.seq,
                "section_path": chunk.section_path,
                "doc_title": doc_title,
            }
            for chunk in chunks
        ],
    )


def search(query: str, k: int = 4) -> list[Hit]:
    """检索与 query 最相关的 k 条切片，按相关度降序返回。

    查询向量由 ``embedder.embed_query`` 生成（中文 bge 模型），以
    ``query_embeddings=`` 传入，不使用 Chroma 默认英文嵌入。

    Args:
        query: 用户查询文本。
        k: 返回条数（默认 4）。

    Returns:
        Hit 列表，score 为余弦相似度（越大越相关），按 score 降序；
        集合尚不存在或 k<=0 时返回空列表。
    """
    if k <= 0:
        return []
    collection = _find_collection()
    if collection is None:
        return []
    query_embedding = embedder.embed_query(query).tolist()
    result = collection.query(
        query_embeddings=[query_embedding],
        n_results=k,
        include=["metadatas", "documents", "distances"],
    )
    ids: list[str] = result["ids"][0]
    metas: list[dict | None] = result["metadatas"][0]
    documents: list[str] = result["documents"][0]
    distances: list[float] = result["distances"][0]
    hits: list[Hit] = []
    for index, hit_id in enumerate(ids):
        meta = metas[index] or {}
        distance = float(distances[index])
        hits.append(
            Hit(
                doc_id=int(meta.get("doc_id", 0)),
                seq=int(meta.get("seq", 0)),
                text=str(documents[index]),
                score=1.0 - distance,  # 余弦空间下 distance = 1 - 相似度
                doc_title=str(meta.get("doc_title", "")),
                section_path=str(meta.get("section_path", "")),
            )
        )
    hits.sort(key=lambda hit: hit.score, reverse=True)
    return hits


def delete_by_doc(doc_id: int) -> None:
    """删除某个文档在向量库中的所有切片记录（Chroma 侧双清前置）。

    集合不存在时静默返回（无记录可删）。metadata 里持久化了 doc_id，
    先按 where 过滤取回 id 列表再删除，兼容各版本 Chroma 的删除语义。

    Args:
        doc_id: SQLite documents 表主键。
    """
    collection = _find_collection()
    if collection is None:
        return
    existing = collection.get(where={"doc_id": {"$eq": doc_id}})
    if existing["ids"]:
        collection.delete(ids=existing["ids"])


def count() -> int:
    """返回当前向量库中的切片记录总数（集合不存在时为 0）。"""
    collection = _find_collection()
    if collection is None:
        return 0
    return int(collection.count())


def list_doc_ids() -> set[int]:
    """返回向量库中存在切片记录的 doc_id 集合（双库对账用）。

    集合不存在时返回空集；分页拉取全部 metadata（每页 1000 条），
    规模以"文档数"计远小于切片数，仅供启动对账等低频场景调用。
    """
    collection = _find_collection()
    if collection is None:
        return set()
    doc_ids: set[int] = set()
    offset = 0
    while True:
        result = collection.get(limit=1000, offset=offset, include=["metadatas"])
        ids = result["ids"]
        if not ids:
            break
        doc_ids.update(
            int((meta or {}).get("doc_id", 0)) for meta in result["metadatas"]
        )
        if len(ids) < 1000:
            break
        offset += 1000
    return doc_ids
