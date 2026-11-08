# -*- coding: utf-8 -*-
"""会话路由（design.md『RESTful 契约』冻结表）。

- GET /api/conversations：全部会话列表 ``[{id,label,created_at,
  message_count}]``（chat_store 已按 id 降序，新会话在前）；
- GET /api/conversations/{id}：会话详情（含全量消息，assistant 消息的
  sources 为 chat_store 反序列化 sources_json 的结果）；不存在 → 404
  ``{"detail": ...}``。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ..rag import chat_store

__all__ = ["router"]

router = APIRouter(prefix="/api", tags=["conversations"])


@router.get("/conversations")
def list_conversations() -> list[dict[str, Any]]:
    """全部会话列表（含 message_count，新会话在前）。"""
    return chat_store.get_conversations()


@router.get("/conversations/{conversation_id}")
def conversation_detail(conversation_id: int) -> dict[str, Any]:
    """单个会话详情：{id,label,created_at,messages:[...]}；不存在返回 404。"""
    conversation = chat_store.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(
            status_code=404, detail=f"会话不存在: id={conversation_id}"
        )
    return conversation
