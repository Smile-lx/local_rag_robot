# 需求 47846874 技术设计（医院 SPD 智能客服智能体，Python RAG）

## 方案概述
- 技术栈：Python 3.10+，FastAPI + Uvicorn，Chroma（本地持久化向量库），SQLite（标准库 sqlite3），sentence-transformers（`BAAI/bge-small-zh-v1.5` 中文 embedding），前端原生 HTML+CSS+JS 零构建链，pytest 测试。
- 单机原型：无鉴权、无多租户、无生产部署、无流式输出（PRD 明确不做）。
- 架构三层：`backend/api`（HTTP 层，薄路由）→ `backend/rag`（RAG 管道，不感知 HTTP）→ 数据层（SQLite 关系库 + Chroma 向量库双写）。

## 环境事实（coder/tester 必读）
- Windows + Git Bash；Python 用 `py` 启动器，开工先 `py -3 --version` 确认 ≥3.10。
- 装依赖：`py -3 -m pip install -r requirements/47846874/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --timeout 120 --retries 5`（torch/sentence-transformers 首装体积大属预期；**官方 PyPI 实测读超时，必须走清华镜像**）。已装齐：fastapi 0.162.0 / uvicorn 0.54.0 / chromadb 1.5.9 / sentence-transformers 6.2.0 / torch 2.14.1 / transformers 5.20.2 / requests / python-dotenv / pytest 9.1.1。
- **2026-10-25 环境重建**：仓库迁至 E:\ai\ai 且本机仅剩 Python 3.10.0（D:\python\python.exe），3.12 环境及依赖全失；PM 按 design 约定命令在 3.10 重装上述同版本依赖（归档：reports/env-install-2026-10-25.{md,log}），并预下载 bge-small-zh-v1.5 模型至 HF 缓存。后续会话开工先 `py -3 -c "import chromadb, sentence_transformers, fastapi"` 探活，缺失即按本条重装。
- HuggingFace 直连可能不通：代码默认 `HF_ENDPOINT=https://hf-mirror.com`（config 提供 env 覆盖）；embedding 模型首次运行会下载到本地缓存。
- 所有 `open()` 显式 `encoding='utf-8'`（Windows 默认 GBK 会乱码）；跑测试前 `export PYTHONIOENCODING=utf-8`。
- SQLite/Chroma 数据文件都生成在 `requirements/47846874/data/` 下（A7 可复现）。

## 目录结构（全部新增，都在 requirements/47846874/ 内）
```
requirements/47846874/
├── backend/
│   ├── __init__.py
│   ├── config.py          # 配置集中：.env 读取、路径推导、参数
│   ├── db.py              # SQLite 连接 + DDL 初始化 + 通用查询助手
│   ├── api/
│   │   ├── __init__.py
│   │   ├── main.py        # FastAPI 装配、路由注册、CORS、静态托管
│   │   ├── routes_chat.py
│   │   ├── routes_conversations.py
│   │   └── routes_documents.py
│   └── rag/
│       ├── __init__.py
│       ├── chunker.py     # 切片器（纯函数，无 IO，可单测）
│       ├── embedder.py    # sentence-transformers 封装（模块级单例）
│       ├── vectorstore.py # Chroma 封装
│       ├── ingest.py      # 入库/删除：切片→向量→SQLite+Chroma 双写/双清
│       ├── llm_client.py  # 三级降级客户端
│       ├── qa.py          # 问答编排：检索→提示词→生成→引用/兜底/降级→落库
│       └── chat_store.py  # 会话/消息持久化 SQL
├── frontend/              # index.html knowledge.html style.css app.js knowledge.js
├── data/
│   ├── docs/              # spd_*.md ×4 + _qa_pairs.md（"_" 开头不入库）
│   └── vectorstore/       # Chroma 持久化目录（运行时生成）
├── sql/schema.sql
├── tools/eval_recall.py   # recall@k + MRR 评估
├── requirements.txt
├── .env.example
└── README.md
```
pytest 测试按仓库约定落 `E:\ai\ai\tests\test_47846874_*.py`（Tester 编写；2026-10-25 仓库自 D:i 迁至 E:ii）。

## 数据库设计（sql/schema.sql，T1 落地；4 表）
- `conversations(id INTEGER PK AUTOINCREMENT, label TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)`
- `messages(id PK AUTOINCREMENT, conversation_id INTEGER NOT NULL REFERENCES conversations(id), role TEXT NOT NULL CHECK(role IN ('user','assistant')), content TEXT NOT NULL, sources_json TEXT, created_at TEXT NOT NULL)`
- `documents(id PK AUTOINCREMENT, title TEXT NOT NULL, filename TEXT NOT NULL UNIQUE, content TEXT NOT NULL, created_at TEXT NOT NULL)`
- `chunks(id PK AUTOINCREMENT, doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE, seq INTEGER NOT NULL, text TEXT NOT NULL, section_path TEXT NOT NULL DEFAULT '', UNIQUE(doc_id, seq))`
- 时间统一 UTC ISO8601（`datetime.now(timezone.utc).isoformat()`）；外键需 `PRAGMA foreign_keys=ON`。
- 向量本身不入关系库；chunks 表存原文与元数据，与 Chroma 内 `doc_id:seq` 一一对应。

## 切片策略（chunker.py，T2）
- 输入全文，输出 `list[Chunk]`（dataclass：`seq / text / section_path`；filename 不入切片器签名，由 ingest 层掌握）。
- 第一刀按 Markdown 标题（`^#{1,6}\s` MULTILINE）切逻辑段，`section_path` = 沿途各级标题以 " / " 拼接。
- 段内 >500 字 → 递归字符切：分隔符优先级 `["\n\n", "\n", "。", "；", "，", ""]`（"" 即硬切兜底）；目标 400~500 字，相邻块重叠 60~80 字。
- <50 字的碎片并入相邻块（无下一段时兜底并入上一段），避免检索噪声；碎片并入后块长绝对上限 549 字。
- ingest 只处理 `data/docs/` 下非 `_` 开头文件（`_qa_pairs.md` 不入库）。

## 向量存储（embedder.py + vectorstore.py，T3）
- embedder：`SentenceTransformer("BAAI/bge-small-zh-v1.5")`，`encode(..., normalize_embeddings=True)`，模块级单例防重复加载。
- Chroma：`PersistentClient(path=data/vectorstore)`，collection `spd_chunks`，`metadata={"hnsw:space": "cosine"}`。
- 接口冻结（T4/T5 按此编程，不等实现）：
  - `add_chunks(chunks: list[Chunk], doc_id: int, doc_title: str)`
  - `search(query: str, k: int = 4) -> list[Hit]`；`Hit = dataclass(doc_id, seq, text, score, doc_title, section_path)`
  - `delete_by_doc(doc_id: int)`；`count() -> int`
- Chroma 记录 id 用 `f"{doc_id}:{seq}"`，metadata 存 doc_id/seq/section_path/doc_title。

## LLM 客户端三级降级（llm_client.py，T4）
按优先级探测，返回 `LLMResult(answer, mode)`：
1. `.env` 有 `LLM_API_KEY` → OpenAI 兼容 `POST {LLM_API_BASE}/chat/completions`（默认 `LLM_API_BASE=https://api.deepseek.com`，`LLM_MODEL=deepseek-chat`，timeout 60s），用 requests，不引 openai SDK。
2. 否则探测 `http://127.0.0.1:11434/api/tags`（2s 超时）通 → ollama `POST /api/chat`，模型 `OLLAMA_MODEL`（默认 `qwen2.5:7b`）。
3. 都不可用 → `mode="retrieval_only"`。
- 上级网络异常须捕获具体异常类型（`requests.RequestException` 等）落到下一级，禁止裸 `except:`。

## RAG 问答管道（qa.py，T4）
`answer(question, conversation_id=None) -> ChatResult(answer, sources, mode, conversation_id)`：
1. `vectorstore.search(question, k=RAG_TOP_K 默认 4)`；
2. 无命中或最高分 < `RAG_MIN_SCORE`（默认 0.30）→ `mode="refusal"`，文案："知识库中未找到相关内容，请尝试换一种问法，或先在知识管理页录入相关文档。"（不编造 SPD 事实）；
3. 提示词：系统提示"你是医院 SPD 耗材智能客服，只依据给定资料回答，引用资料时标注 [来源: 文档名#切片号]；资料不足时明确说明，不要编造"；user = 资料块（带 [来源] 前缀）+ 用户问题；
4. LLM 成功 → `mode="llm"`；未配置/失败 → `mode="retrieval_only"`，answer = "（未接入生成模型，以下为检索到的知识库原文）" + top 片段拼接（各带 [来源] 标注）；
5. 无论何种 mode，question 与 answer（含 sources_json）都经 `chat_store` 落库（会话不存在则自动创建，label 取科室/姓名可选参数或默认 ""）。

## 多轮对话上下文（T9，2026-10-25 用户追加）
- 每次 /api/chat 在 conversation_id 有效时回顾最近三轮对话：chat_store.recent_turns(cid, turns=3) 返回最近 3 组 user/assistant 消息对（时间升序，须在本次 question 落库前调用）。
- 检索侧（强信号守卫 + 弱信号救援双查询 max 融合）：原问单独检索 top1 ≥ RAG_MIN_SCORE → 原样使用（仅 1 次检索，上下文不进检索，结构性杜绝负优化）；否则用"当前问题+最近 1 条历史用户问题"（去重/单条 50 字/总量 300 字）二次检索；_fuse_hits 按 (doc_id,seq) 去重取较高分、降序取前 k——融合池 ⊇ 原问池，逐位分数单调不降。
- 生成侧：提示词在资料块与问题之间注入最近 3 轮区块（单条 200 字截断、区块总量 1200 字，头部声明"仅供理解追问指代、不作新事实来源"）；系统提示仅在有历史时追加"结合历史对话理解用户的追问"。
- 无 conversation_id / 首问：与单轮版同一路径逐字节一致；RESTful 契约字段不变（API 层零改动）。

## RESTful 契约（冻结；T5、T6 按此实现，验收以本表为准）
统一前缀 `/api`，JSON；未找到返回 404 `{detail}`：
| 方法 | 路径 | 请求体 | 响应 |
|---|---|---|---|
| GET | /api/health | - | `{"status":"ok"}` |
| POST | /api/chat | `{question:str, conversation_id?:int, label?:str}` | `{conversation_id:int, answer:str, mode:"llm"\|"retrieval_only"\|"refusal", sources:[{doc_id,doc_title,seq,section_path,score,snippet}]}` |
| GET | /api/conversations | - | `[{id,label,created_at,message_count}]` |
| GET | /api/conversations/{id} | - | `{id,label,created_at,messages:[{id,role,content,sources,created_at}]}` |
| GET | /api/documents | - | `[{id,title,filename,chunk_count,created_at}]` |
| POST | /api/documents | `{title:str, content:str}` | 201 `{id,title,chunk_count}` |
| GET | /api/documents/{id}/chunks | - | `[{id,doc_id,seq,text,section_path}]` |
| DELETE | /api/documents/{id} | - | `{deleted:true, chunks_removed:int}`（先清 Chroma 再删 SQLite，任一失败不落半态） |
- refusal 也是 200（mode 字段区分）；POST /api/chat 与 POST /api/documents 参数缺失返回 422（FastAPI 默认）。
- 静态托管：`app.mount("/", StaticFiles(directory=frontend, html=True))`（API 路由先注册）；CORS `allow_origins=["*"]`（本机原型）。

## 前端（T6，按契约编程，与后端并行开发）
- `index.html` 聊天页：左会话栏（新建会话 + 会话列表），主区消息流（答案 + mode 降级/拒答标注 + 来源列表），底部输入框（Enter 发送，失败显示错误）。
- `knowledge.html` 知识管理页：文档表格（标题 / 切片数 / 删除按钮）、录入表单（标题 + 正文 textarea）→ POST 后刷新列表。
- fetch 封装在 app.js / knowledge.js；style.css 共用；两页互链。无构建链，浏览器直接打开（由后端托管）。

## 评估脚本（tools/eval_recall.py，T7）
- 解析 `data/docs/_qa_pairs.md` 的问题→关键词对照；对 chunk 目标参数 300/400/600 三组：用 chunker 重新切片 + 临时向量库目录重建 → 逐题检索；
- 输出对比表：recall@3、recall@5、MRR；复用 ingest/chunker 内部函数，不重复实现切片逻辑。

## 任务拆分（7 卡，任意两卡文件不相交；并行波次 W1=[T1,T2,T6] W2=[T3] W3=[T4] W4=[T5] W5=[T7]）
- T1 地基：骨架/config/db/DDL/requirements.txt/.env.example
- T2 语料与切片器：data/docs 5 份 + chunker.py
- T3 检索与入库：embedder/vectorstore/ingest
- T4 LLM 客户端与问答：llm_client/qa/chat_store
- T5 FastAPI 后端：api/main + 3 组 routes
- T6 前端静态页：frontend/ 5 文件
- T7 README + 评估脚本：README.md + tools/eval_recall.py
- 依赖：T3←[T1,T2]；T4←[T1,T3]；T5←[T1,T3,T4]；T6←[]（只按契约）；T7←[T2,T5]

## 数据变更
`sql/schema.sql`（上述 4 表，含索引：messages(conversation_id)、chunks(doc_id)）；无迁移工具。
