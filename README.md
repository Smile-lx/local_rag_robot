# 医院 SPD 智能客服智能体（需求 47846874）

面向医院 SPD（Supply / Processing / Distribution，医用耗材供应链）场景的智能客服原型：
**RAG 检索问答 + LLM 三级降级生成 + 知识管理**。

- **RAG 检索问答**：`data/docs/` 语料按 Markdown 标题 + 递归字符切片（约 400~500 字、
  相邻块重叠 70 字），`BAAI/bge-small-zh-v1.5` 中文向量化，Chroma 余弦检索 top-k 片段；
  检索最高分低于阈值即拒答，不编造 SPD 事实。
- **三级降级生成**：OpenAI 兼容 API（如 DeepSeek）→ 本机 ollama → 仅检索模式
  （`mode="retrieval_only"`）。未配置任何 LLM 也能运行，返回检索原文属预期而非故障。
- **知识管理**：知识管理页支持手工录入与原始文件上传（.md / .txt，本地读取后
  预填表单）、文档分片查看与删除；切片实时重建（SQLite 与 Chroma 双写 / 双清，不落半态）。

技术栈：Python 3.10+ / FastAPI / Uvicorn / Chroma / SQLite（标准库 sqlite3）/
sentence-transformers；前端原生 HTML+CSS+JS 零构建链，由后端直接托管。
单机原型：无鉴权、无多租户、无流式输出（PRD 明确不做）。

## 快速开始

前置：Windows + Python 3.10+（`py -3 --version` 确认）；以下命令在 Git Bash 中执行，
且 `cd requirements/47846874/`（即本目录，启动命令依赖该 cwd）。

```bash
# 1. 安装依赖（官方 PyPI 实测读超时，务必走清华镜像；torch 等首装体积大属预期）
py -3 -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --timeout 120 --retries 5

# 2.（可选）复制配置文件；所有配置项均有默认值，不复制也能直接运行
cp .env.example .env

# 3. 启动后端（等价方式：py -3 -m backend.api.main）
py -3 -m uvicorn backend.api.main:app --port 8000
#    若 8000 被占用（本机常见：C-Lodop 打印服务 CLodopPrint32.exe 等），改用 --port 8001，并同步把访问地址换成 http://127.0.0.1:8001
```

4. 浏览器打开 <http://127.0.0.1:8000>（聊天页）；
   知识管理页 <http://127.0.0.1:8000/knowledge.html>。

- 首次启动会自动装载 `data/docs/` 语料并加载 bge embedding 模型，**数十秒属预期**；
  就绪标志：`curl http://127.0.0.1:8000/api/health` 返回 `{"status":"ok"}`。
- 运行数据全部落在 `data/`（`spd.db` + `vectorstore/`），删除即完全重置，
  重启后按语料目录自动重建。

## 接口清单

统一前缀 `/api`，JSON；未找到返回 404 `{detail}`：

| 方法 | 路径 | 请求体 | 响应 |
|---|---|---|---|
| GET | /api/health | - | `{"status":"ok"}` |
| GET | /api/models | - | `{sensitive_gate:bool, channels:[{level,type,configured,base,model,reachable,available_models}], effective:"openai_compatible"\|"ollama"\|"retrieval_only"}` |
| POST | /api/chat | `{question:str, conversation_id?:int, label?:str}` | `{conversation_id:int, answer:str, mode:"llm"\|"retrieval_only"\|"refusal", sources:[{doc_id,doc_title,seq,section_path,score,snippet}], model:str, verified:"pass"\|"suspect"\|"fail"\|"skipped"}` |
| GET | /api/conversations | - | `[{id,label,created_at,message_count}]` |
| GET | /api/conversations/{id} | - | `{id,label,created_at,messages:[{id,role,content,sources,created_at}]}` |
| GET | /api/documents | - | `[{id,title,filename,chunk_count,created_at}]` |
| GET | /api/documents/{id} | - | `{id,title,filename,content,chunk_count,created_at}`（知识页“编辑”加载全文用） |
| POST | /api/documents | `{title:str, content:str}` | 201 `{id,title,chunk_count}` |
| GET | /api/documents/{id}/chunks | - | `[{id,doc_id,seq,text,section_path}]` |
| DELETE | /api/documents/{id} | - | `{deleted:true, chunks_removed:int}`（先清 Chroma 再删 SQLite，任一失败不落半态） |

- refusal 也是 200（`mode` 字段区分）；POST /api/chat 与 POST /api/documents
  参数缺失返回 422（FastAPI 默认）。
- 静态托管：前端页面挂载在 `/`（API 路由先注册）；CORS `allow_origins=["*"]`（本机原型）。

## 配置说明

全部配置项及默认值见 [.env.example](.env.example)（复制为 `.env` 后修改，重启后端生效）。

### LLM 三级降级

| 优先级 | 触发条件 | 行为 |
|---|---|---|
| 1 | 配置了任一外部通道 KEY | 按 `LLM_CHANNELS` 声明顺序依次尝试各 OpenAI 兼容通道（DeepSeek / 智谱 GLM / 通义千问 / Kimi / 豆包 / OpenAI / 自定义本地网关的接入模板已在 `.env.example` 预写好，填 KEY 即用），前者失败自动切后者；回答标注 `{模型}@{通道名}`。也可只用旧三变量 `LLM_API_KEY/BASE/MODEL`（单通道，标注 `@openai-compat`） |
| 2 | 外部通道全不可用（或未配置） | 探测本机 ollama `{OLLAMA_BASE}/api/tags`（默认 `http://127.0.0.1:11434`），通则用 `OLLAMA_MODEL`（默认 `qwen2.5:7b`）生成 |
| 3 | 以上均不可用 | **`mode="retrieval_only"`：返回检索到的知识库原文——无 Key 时降级属预期行为，不是故障** |

### 检索与镜像

- `RAG_TOP_K`（默认 4）：每次问答检索返回的切片数。
- `RAG_MIN_SCORE`（默认 0.45，2026-10-25 由 0.30 上调）：命中最低余弦相似度，最高分低于该值即拒答；实测相关 0.75+ / 无关 0.30~0.38，0.45 干净分离。
- `HF_ENDPOINT`（默认 `https://hf-mirror.com`）：bge 模型下载镜像，
  可直连官方时改为 `https://huggingface.co`。

### 超时覆盖（单位秒，`.env` 同名变量或环境变量均可）

- `LLM_TIMEOUT`（默认 60）：第 1 级 OpenAI 兼容接口超时。
- `OLLAMA_PROBE_TIMEOUT`（默认 2）：ollama 探测超时。
- `OLLAMA_TIMEOUT`（默认 120）：ollama 对话生成超时。

### 防幻觉多重校验（生成答案三重把关）

- `VERIFY_ANSWER`（默认 1，开启）：大模型生成的答案在返回前经三层校验，
  全部只对照本次检索命中的原文——**忠实度**（逐句 bigram 覆盖率，无依据
  句占比过高判失败）、**引用真实性**（`[来源: 文档名#切片号]` 必须真实存在
  于本次命中集合，编造出处直接判失败）、**数字一致性**（答案中的阿拉伯
  数字须能在原文找到）。判失败（`verified:"fail"`）的答案**弃用并自动降级
  为仅检索**（返回原文，绝不出幻觉答案）；`suspect` 正常返回但经 `verified`
  字段标注供人工核对。
- `FAITH_MIN`（默认 0.5）：忠实度低于此值判 fail；`FAITH_SUSPECT`
  （默认 0.8）：低于此值判 suspect。`verified:"skipped"` 表示未走生成
  （拒答/仅检索）或校验已关闭。

### 数据安全（外部 API 外发二次校验）

- `SENSITIVE_EGRESS_BLOCK`（默认 1，开启）：发往**外部大模型 API**前，
  对完整消息（用户问题 + 检索到的知识片段）做敏感信息扫描——身份证号 /
  手机号 / 银行卡号 / 住院·病历·就诊号；命中即**拒绝外发**，自动降级
  本机 ollama 或仅检索模式（服务不中断），并记录脱敏审计日志（只记
  模式名与掩码预览，不落敏感原文）。本机 ollama 与纯检索不经网络外发，
  不受此闸限制。医疗数据场景务必保持开启。

## 目录结构

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
│   └── vectorstore/       # Chroma 持久化目录（运行时生成；同级还有 spd.db）
├── sql/schema.sql
├── tools/eval_recall.py   # recall@k + MRR 评估
├── reports/               # 测试报告与环境重建归档
├── requirements.txt
├── .env.example
└── README.md
```

pytest 测试按仓库约定落在仓库根 `E:\ai\ai\tests\`（`test_47846874_*.py`）。

## 测试与评估

### 运行测试（pytest）

```bash
cd /e/ai/ai                    # 仓库根（Windows 路径 E:\ai\ai）
export PYTHONIOENCODING=utf-8  # Windows 下中文输出防乱码
py -3 -m pytest tests/ -v
```

### 检索质量评估（recall@3 / recall@5 / MRR）

```bash
cd requirements/47846874
export PYTHONIOENCODING=utf-8
py -3 tools/eval_recall.py
```

脚本对 chunk 目标参数 **300 / 400 / 600** 三组重新切片语料并重建到 **tempfile
临时目录**（SQLite 与 Chroma 均临时，绝不写 `data/` 正式库），逐题检索后输出
三组 × 三指标（recall@3、recall@5、MRR）的 markdown 对比表，附每组切片总数。
评估集为 `data/docs/_qa_pairs.md`（问题 | 关键词对照，≥5 组）；每题检索条数
k = max(5, RAG_TOP_K)；命中判定为返回切片原文包含关键词。bge 模型首次加载
数十秒属预期。

## 已知限制

1. **无鉴权、单机原型**：不做身份认证 / 权限 / 多租户 / 生产部署（PRD 明确不做）。
2. **拒答阈值已上调至 0.45**（2026-10-25，原 0.30）：实测相关问题 top1 得分
   约 0.75+、无关问题 0.30~0.38，0.45 可干净分离，防语料增多后闲聊类问题
   不被拒答（数据见 `reports/T4-test-report.md`）；语料规模显著变化后可复核微调。
3. **`mode="llm"` 需自配 LLM**：配置 `LLM_API_KEY`（或本机 ollama）才会走模型生成，
   否则固定为 `retrieval_only` 仅检索模式。
4. **上传编码自动识别（2026-10-25 加固）**：按 UTF-8 读取检出乱码替换符时
   自动回退按 GBK 重读并提示识别结果；极少数其他本地编码仍请先转存 UTF-8。
5. **并发写已做单进程加固**（2026-10-25）：入库/删除经进程内互斥串行执行、
   连接统一 busy_timeout（默认 5s，可配 `DB_BUSY_TIMEOUT_MS`）、启动时
   SQLite 与向量库对账自愈（清孤儿向量、重建缺失向量）。更高并发部署可在
   `.env` 设 `JOURNAL_MODE=WAL`；多人生产使用建议二期换 MySQL/PostgreSQL。

## 环境重建归档

- [reports/env-install-2026-10-25.md](reports/env-install-2026-10-25.md)：
  2026-10-25 依赖重装记录（Python 3.10.0、完整依赖清单与清华镜像安装命令）。
