-- ============================================================
-- 医院 SPD 智能客服智能体 —— SQLite 表结构（schema.sql）
-- 设计要点（见 requirements/47846874/design.md 数据库设计一节）：
--   * 全部幂等：CREATE TABLE / INDEX IF NOT EXISTS，可重复执行；
--   * 时间统一 UTC ISO8601（由 backend.db.now_iso() 生成）；
--   * 外键约束按连接生效，backend.db.get_conn() 内统一开启
--     PRAGMA foreign_keys=ON；
--   * 向量本身不入关系库：chunks 存原文与元数据，与 Chroma 内
--     id "{doc_id}:{seq}" 一一对应。
-- ============================================================

-- 会话：label 为会话展示名（科室/姓名，默认空串）
CREATE TABLE IF NOT EXISTS conversations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    label      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

-- 消息：role 仅允许 user/assistant；sources_json 存来源列表 JSON（可为 NULL）
CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    role            TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content         TEXT NOT NULL,
    sources_json    TEXT,
    created_at      TEXT NOT NULL
);

-- 文档：filename 唯一，作为入库去重键
CREATE TABLE IF NOT EXISTS documents (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    filename   TEXT NOT NULL UNIQUE,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- 切片：seq 为文档内切片序号，与 Chroma id "{doc_id}:{seq}" 对应；
-- 删除文档时级联清理切片
CREATE TABLE IF NOT EXISTS chunks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id       INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    seq          INTEGER NOT NULL,
    text         TEXT NOT NULL,
    section_path TEXT NOT NULL DEFAULT '',
    UNIQUE (doc_id, seq)
);

-- 索引：按会话查消息、按文档查切片是两类高频查询
CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages (conversation_id);
CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON chunks (doc_id);
