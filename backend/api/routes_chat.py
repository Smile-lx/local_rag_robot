# -*- coding: utf-8 -*-
"""聊天路由：POST /api/chat（design.md『RESTful 契约』冻结表）。

- 请求体 Pydantic 校验：question 必填且非空白（缺失/空串 → 422，
  FastAPI 默认机制，不自定义吞掉）；conversation_id / label 可选；
- 调 ``rag.qa.answer`` 完成检索→生成/降级/拒答→落库，按契约返回
  ``{conversation_id, answer, mode, sources}``；refusal 同样返回 200，
  以 mode 字段区分，不在路由层改写状态码。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator

from ..rag import qa

__all__ = ["router", "ChatRequest"]

router = APIRouter(prefix="/api", tags=["chat"])


class ChatRequest(BaseModel):
    """POST /api/chat 请求体（契约：{question, conversation_id?, label?}）。"""

    question: str = Field(..., min_length=1)
    conversation_id: int | None = None
    label: str | None = None

    @field_validator("question")
    @classmethod
    def _question_not_blank(cls, value: str) -> str:
        """拒绝空白串（min_length 只拦空串，拦不住纯空格/制表符）。"""
        if not value.strip():
            raise ValueError("question 不能为空白")
        return value


@router.post("/chat")
def chat(request: ChatRequest) -> dict[str, Any]:
    """问答端点：调 qa.answer 并透传契约字段（sources 为六字段 dict 列表）。

    qa.answer 为阻塞调用（embedding 检索 + LLM HTTP），同步 def 让
    FastAPI 放入线程池执行，不阻塞事件循环。
    """
    result = qa.answer(
        question=request.question,
        conversation_id=request.conversation_id,
        label=request.label,
    )
    return {
        "conversation_id": result.conversation_id,
        "answer": result.answer,
        "mode": result.mode,
        "sources": result.sources,
        "model": result.model,
        "verified": result.verified,
    }
