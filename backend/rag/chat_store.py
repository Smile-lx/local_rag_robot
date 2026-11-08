"""会话与消息持久化：conversations / messages 两表的增查助手。

- 复用 ``backend.db`` 的连接与查询助手（init_db / execute / query_*），
  不另建连接管理；时间统一 ``db.now_iso()``（UTC ISO8601）；
- sources 序列化约定：写入时 ``json.dumps(..., ensure_ascii=False)`` 存
  messages.sources_json；读出时反序列化为 ``list[dict]``，空值/损坏数据按
  ``[]`` 处理并记 warning（读路径防御，不中断会话查看）；
- 返回字段名与 design.md『RESTful 契约』一致，供 T5 路由层直接透传：
  列表项 ``{id,label,created_at,message_count}``；详情
  ``{id,label,created_at,messages:[{id,role,content,sources,created_at}]}``。

接口（冻结，供 qa / api/routes_conversations 使用）::

    create_conversation(label="") -> int
    ensure_conversation(conversation_id=None, label=None) -> int
    conversation_exists(conversation_id) -> bool
    add_message(conversation_id, role, content, sources=None) -> int
    get_conversations() -> list[dict]            # 新会话在前（id 降序）
    get_conversation(conversation_id) -> dict | None
    message_count(conversation_id) -> int
    recent_turns(conversation_id, turns=3) -> list[dict]   # T9 多轮上下文
"""
from __future__ import annotations

import json
import logging
from typing import Any, Literal, TypeAlias

from ..db import execute, init_db, now_iso, query_all, query_one

__all__ = [
    "Role",
    "create_conversation",
    "ensure_conversation",
    "conversation_exists",
    "add_message",
    "get_conversations",
    "get_conversation",
    "message_count",
    "recent_turns",
]

logger = logging.getLogger(__name__)

#: messages.role 仅允许的取值（与 schema.sql 的 CHECK 约束一致）
Role: TypeAlias = Literal["user", "assistant"]

_VALID_ROLES: tuple[str, ...] = ("user", "assistant")


def create_conversation(label: str = "") -> int:
    """新建会话，返回会话 id。

    Args:
        label: 会话展示名（科室/姓名等），默认空串。
    """
    init_db()
    return execute(
        "INSERT INTO conversations (label, created_at) VALUES (?, ?)",
        (label, now_iso()),
    )


def conversation_exists(conversation_id: int) -> bool:
    """判断会话是否存在。"""
    init_db()
    row = query_one("SELECT id FROM conversations WHERE id = ?", (conversation_id,))
    return row is not None


def ensure_conversation(
    conversation_id: int | None = None, label: str | None = None
) -> int:
    """取有效的会话 id；未指定或会话不存在时自动新建（design.md 约定）。

    Args:
        conversation_id: 调用方携带的会话 id（如 POST /api/chat 请求体）。
        label: 会话展示名，仅新建会话时生效；会话已存在时忽略。

    Returns:
        有效的会话 id（既存在则原样返回，否则返回新建会话的 id）。
    """
    if conversation_id is not None and conversation_exists(conversation_id):
        return conversation_id
    return create_conversation(label or "")


def add_message(
    conversation_id: int,
    role: Role,
    content: str,
    sources: list[dict[str, Any]] | None = None,
) -> int:
    """向会话追加一条消息，返回消息 id。

    Args:
        conversation_id: 目标会话 id。
        role: "user" 或 "assistant"。
        content: 消息正文。
        sources: 来源列表（assistant 消息携带，结构见 design.md 契约
            ``[{doc_id,doc_title,seq,section_path,score,snippet}]``）；
            None 时 sources_json 存 NULL，空列表存 "[]"。

    Raises:
        ValueError: role 不在 ("user", "assistant") 内。
        LookupError: 会话不存在。
    """
    if role not in _VALID_ROLES:
        raise ValueError(f"role 仅允许 {_VALID_ROLES}，实际为 {role!r}")
    init_db()
    if (
        query_one("SELECT id FROM conversations WHERE id = ?", (conversation_id,))
        is None
    ):
        raise LookupError(f"会话不存在: conversation_id={conversation_id}")
    sources_json = (
        json.dumps(sources, ensure_ascii=False) if sources is not None else None
    )
    return execute(
        "INSERT INTO messages (conversation_id, role, content, sources_json, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (conversation_id, role, content, sources_json, now_iso()),
    )


def get_conversations() -> list[dict[str, Any]]:
    """全部会话列表（GET /api/conversations 契约），新会话在前。

    Returns:
        ``[{id,label,created_at,message_count}]``，按 id 降序。
    """
    init_db()
    rows = query_all(
        "SELECT c.id AS id, c.label AS label, c.created_at AS created_at,"
        " (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id)"
        " AS message_count FROM conversations c ORDER BY c.id DESC"
    )
    return [
        {
            "id": int(row["id"]),
            "label": str(row["label"]),
            "created_at": str(row["created_at"]),
            "message_count": int(row["message_count"]),
        }
        for row in rows
    ]


def get_conversation(conversation_id: int) -> dict[str, Any] | None:
    """单个会话详情（GET /api/conversations/{id} 契约）。

    Returns:
        ``{id,label,created_at,messages:[{id,role,content,sources,created_at}]}``；
        会话不存在返回 None（API 层据此返回 404）。messages 按写入顺序
        （id 升序）返回。
    """
    init_db()
    row = query_one(
        "SELECT id, label, created_at FROM conversations WHERE id = ?",
        (conversation_id,),
    )
    if row is None:
        return None
    message_rows = query_all(
        "SELECT id, role, content, sources_json, created_at FROM messages"
        " WHERE conversation_id = ? ORDER BY id ASC",
        (conversation_id,),
    )
    messages = [
        {
            "id": int(item["id"]),
            "role": str(item["role"]),
            "content": str(item["content"]),
            "sources": _load_sources(item["sources_json"]),
            "created_at": str(item["created_at"]),
        }
        for item in message_rows
    ]
    return {
        "id": int(row["id"]),
        "label": str(row["label"]),
        "created_at": str(row["created_at"]),
        "messages": messages,
    }


def message_count(conversation_id: int) -> int:
    """会话内消息条数（会话不存在时为 0）。"""
    init_db()
    row = query_one(
        "SELECT COUNT(*) AS n FROM messages WHERE conversation_id = ?",
        (conversation_id,),
    )
    return int(row["n"]) if row is not None else 0


def recent_turns(conversation_id: int, turns: int = 3) -> list[dict[str, Any]]:
    """该会话最近 ``turns`` 组 user/assistant 对话消息（T9 多轮上下文）。

    以 assistant 消息作为一轮的收尾，从最新往回收满 ``turns`` 组（每组含
    其配对的 user），返回按时间**升序**排列，供提示词直接拼接；不足
    ``turns`` 组返回全量；会话不存在或 ``turns <= 0`` 返回空列表。

    调用时机约定（qa.answer 遵守）：必须在本次 question 落库**之前**调用，
    否则当前问题会被当作历史重复计入。

    Args:
        conversation_id: 会话 id。
        turns: 回顾的对话轮数（一组 = 一条 user + 一条 assistant）。

    Returns:
        ``[{role, content, created_at}, ...]``（升序，最多 ``turns*2`` 条）。
    """
    if turns <= 0:
        return []
    init_db()
    if (
        query_one("SELECT id FROM conversations WHERE id = ?", (conversation_id,))
        is None
    ):
        return []
    rows = query_all(
        "SELECT role, content, created_at FROM messages"
        " WHERE conversation_id = ? ORDER BY id DESC",
        (conversation_id,),
    )
    picked: list[Any] = []
    completed_turns = 0
    # 收满 turns 个 assistant 后，还要把它的配对 user 一并带上（该 user 在
    # 降序遍历中位于其后一步），否则最后一轮只剩半个对
    await_paired_user = False
    for row in rows:
        picked.append(row)
        role = str(row["role"])
        if role == "assistant":
            completed_turns += 1
            if completed_turns >= turns:
                await_paired_user = True
        elif await_paired_user and role == "user":
            break
    picked.reverse()  # 收集时新→旧，反转为时间升序
    return [
        {
            "role": str(row["role"]),
            "content": str(row["content"]),
            "created_at": str(row["created_at"]),
        }
        for row in picked
    ]


def _load_sources(raw: str | None) -> list[dict[str, Any]]:
    """反序列化 sources_json；空值/损坏/非列表数据按空列表处理并告警。"""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("sources_json 解析失败，按空来源处理: %r", raw[:100])
        return []
    if not isinstance(data, list):
        logger.warning("sources_json 不是列表，按空来源处理: %r", raw[:100])
        return []
    return [item for item in data if isinstance(item, dict)]
