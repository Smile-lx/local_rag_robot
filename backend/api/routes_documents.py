# -*- coding: utf-8 -*-
"""文档与切片路由（design.md『RESTful 契约』冻结表）。

- GET /api/documents：``[{id,title,filename,chunk_count,created_at}]``，
  chunk_count 由 SQL 聚合（documents LEFT JOIN chunks GROUP BY，无切片
  的文档计 0），新文档在前；
- POST /api/documents：201 ``{id,title,chunk_count}``，调
  ``ingest.ingest_document`` 完成切片与 SQLite/Chroma 双写；
- GET /api/documents/{id}/chunks：``[{id,doc_id,seq,text,section_path}]``，
  按 seq 升序；文档不存在 → 404；
- DELETE /api/documents/{id}：``{deleted:true, chunks_removed:int}``，
  调 ``ingest.delete_document``（先清向量库再删 SQLite，不落半态），
  文档不存在（LookupError）→ 404。

参数缺失/空白由 Pydantic 默认 422 拦截；ingest 层的语义性非法参数
（如标题推导出 ``_`` 前缀文件名）同样映射为 422，与契约族一致。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from ..db import init_db, query_all, query_one
from ..rag import ingest

__all__ = ["router", "DocumentCreate"]

router = APIRouter(prefix="/api", tags=["documents"])


class DocumentCreate(BaseModel):
    """POST /api/documents 请求体（契约：{title:str, content:str}）。"""

    title: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)

    @field_validator("title", "content")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        """拒绝空白串（空标题/空正文在入库层同样非法，提前拦为 422）。"""
        if not value.strip():
            raise ValueError("title/content 不能为空白")
        return value


@router.get("/documents")
def list_documents() -> list[dict[str, Any]]:
    """文档列表（chunk_count 为 documents LEFT JOIN chunks GROUP BY 聚合计数）。"""
    init_db()
    rows = query_all(
        "SELECT d.id AS id, d.title AS title, d.filename AS filename,"
        " d.created_at AS created_at, COUNT(c.id) AS chunk_count"
        " FROM documents d LEFT JOIN chunks c ON c.doc_id = d.id"
        " GROUP BY d.id, d.title, d.filename, d.created_at"
        " ORDER BY d.id DESC"
    )
    return [
        {
            "id": int(row["id"]),
            "title": str(row["title"]),
            "filename": str(row["filename"]),
            "chunk_count": int(row["chunk_count"]),
            "created_at": str(row["created_at"]),
        }
        for row in rows
    ]


@router.post("/documents", status_code=201)
def create_document(payload: DocumentCreate) -> dict[str, Any]:
    """录入文档：切片 → SQLite/Chroma 双写，201 返回 {id,title,chunk_count}。

    持 ingest.WRITE_LOCK 串行化双库写操作（并发加固，见 ingest 模块说明）。
    """
    try:
        with ingest.WRITE_LOCK:
            doc_id, chunk_count = ingest.ingest_document(
                payload.title, payload.content
            )
    except ValueError as exc:
        # 语义级非法参数（ingest 层拦截）：映射为 422，与参数校验同一族
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # ingest 内部会 strip 标题，返回入库后的实际值保持一致
    return {
        "id": doc_id,
        "title": payload.title.strip(),
        "chunk_count": chunk_count,
    }


@router.get("/documents/{doc_id}")
def get_document(doc_id: int) -> dict[str, Any]:
    """文档详情（含存档全文，知识页"编辑"原位加载用）；不存在 → 404。"""
    _ensure_document(doc_id)
    row = query_one(
        "SELECT d.id AS id, d.title AS title, d.filename AS filename,"
        " d.content AS content, d.created_at AS created_at,"
        " COUNT(c.id) AS chunk_count"
        " FROM documents d LEFT JOIN chunks c ON c.doc_id = d.id"
        " WHERE d.id = ? GROUP BY d.id",
        (doc_id,),
    )
    return {
        "id": int(row["id"]),
        "title": str(row["title"]),
        "filename": str(row["filename"]),
        "content": str(row["content"]),
        "chunk_count": int(row["chunk_count"]),
        "created_at": str(row["created_at"]),
    }


@router.get("/documents/{doc_id}/chunks")
def list_chunks(doc_id: int) -> list[dict[str, Any]]:
    """某文档的全部切片，按 seq 升序；文档不存在 → 404。"""
    _ensure_document(doc_id)
    rows = query_all(
        "SELECT id, doc_id, seq, text, section_path FROM chunks"
        " WHERE doc_id = ? ORDER BY seq ASC",
        (doc_id,),
    )
    return [
        {
            "id": int(row["id"]),
            "doc_id": int(row["doc_id"]),
            "seq": int(row["seq"]),
            "text": str(row["text"]),
            "section_path": str(row["section_path"]),
        }
        for row in rows
    ]


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: int) -> dict[str, Any]:
    """删除文档及全部切片；文档不存在 → 404（ingest 抛 LookupError）。

    持 ingest.WRITE_LOCK 串行化双库删除（并发加固，见 ingest 模块说明）。
    """
    try:
        with ingest.WRITE_LOCK:
            chunks_removed = ingest.delete_document(doc_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True, "chunks_removed": chunks_removed}


def _ensure_document(doc_id: int) -> None:
    """校验文档存在，不存在抛 404（响应体 {"detail": ...}）。"""
    init_db()
    if query_one("SELECT id FROM documents WHERE id = ?", (doc_id,)) is None:
        raise HTTPException(status_code=404, detail=f"文档不存在: id={doc_id}")
