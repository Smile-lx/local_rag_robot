# 测试报告 T1（项目地基）

结果: **PASS**
覆盖验收标准: A1 ✓ / A2 ✓ / A3 ✓ / A4 ✓ / A5 ✓ / A6 ✓（6/6 通过）

- 被测对象: `backend/{__init__,config,db}.py`、`backend/api/__init__.py`、`backend/rag/__init__.py`、`sql/schema.sql`、`requirements.txt`、`.env.example`（全部未改动）
- 测试环境: Windows 11 + Git Bash，`py -3` = Python 3.12.2（≥3.10 满足），pytest 9.1.1
- 测试文件: `D:\ai\tests\test_47846874_T1_foundation.py`（15 个用例，15 passed in 17.19s）
- 运行命令: `cd /d/ai && export PYTHONIOENCODING=utf-8 && py -3 -m pytest tests/test_47846874_T1_foundation.py -v`

## 逐条验收证据

### A1 导入成功 ✓
在 `requirements/47846874/` 下实测：
```
py -3 -c "from backend import config, db"   → IMPORT_OK
```
路径推导正确：`config.PROJECT_ROOT = D:\ai\requirements\47846874`，`DB_PATH = <PROJECT_ROOT>/data/spd.db`，`db.SCHEMA_PATH = <PROJECT_ROOT>/sql/schema.sql`。`backend.api`、`backend.rag` 包也可导入。

### A2 init_db 建表/索引 + 幂等 ✓
- CLI 实测连续 3 次 `db.init_db()` 无报错（含已存在的 data/spd.db 场景）。
- pytest 用隔离测试库（`.tmp_t1f_tester/test_spd.db`）断言：4 表 `conversations/messages/documents/chunks` 齐全；索引 `idx_messages_conversation_id`（messages.conversation_id）、`idx_chunks_doc_id`（chunks.doc_id）存在且列正确（PRAGMA index_info 验证）；重复 init_db 前后 `sqlite_master` 快照一致。

### A3 依赖可导入 ✓（含安装过程，属验收一部分而非失败）
- 首验缺依赖（`ModuleNotFoundError: No module named 'fastapi'`，此前仅 python-dotenv 1.2.4 已装）。
- 第 1 次安装 `py -3 -m pip install -r requirements/47846874/requirements.txt`（PyPI 官方源）**失败**：下载 chromadb 1.5.9（23.5 MB）时对 files.pythonhosted.org 读超时（实测网速约 33.9 kB/s），`pip._vendor.urllib3.exceptions.ReadTimeoutError`。
- 第 2 次改用清华镜像成功：`py -3 -m pip install -r requirements/47846874/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --timeout 120 --retries 5` → PIP_EXIT=0，Successfully installed。
- 关键版本：fastapi 0.162.0 / uvicorn 0.54.0 / chromadb 1.5.9 / sentence-transformers 6.2.0（torch 2.14.1、transformers 5.20.2 为传递依赖）/ requests 2.34.2 / python-dotenv 1.2.4。
- 复验 `py -3 -c "import fastapi, uvicorn, chromadb, sentence_transformers, requests, dotenv"` → DEPS_OK。
- 安装日志存档：`D:\ai\.tmp_t1f_tester\pip-install.log`。

### A4 .env.example 覆盖全部配置项 ✓
- 逐项核对 design.md『环境事实』『RESTful 契约』配置：DATA_DIR、DB_PATH、VECTOR_DIR、DOCS_DIR、FRONTEND_DIR、RAG_TOP_K、RAG_MIN_SCORE、CHUNK_TARGET、CHUNK_OVERLAP、HF_ENDPOINT、LLM_API_KEY、LLM_API_BASE、LLM_MODEL、OLLAMA_BASE、OLLAMA_MODEL 全部在 `.env.example` 出现，均带中文注释；PROJECT_ROOT 有"自动推导，无需填写"说明。
- config 默认值与 design.md 一致（pytest 用干净环境子进程验证，排除测试进程 env 污染）：RAG_TOP_K=4、RAG_MIN_SCORE=0.30、CHUNK_TARGET=450、CHUNK_OVERLAP=70、HF_ENDPOINT=https://hf-mirror.com、LLM_API_BASE=https://api.deepseek.com、LLM_API_KEY=''、LLM_MODEL=deepseek-chat、OLLAMA_BASE=http://127.0.0.1:11434、OLLAMA_MODEL=qwen2.5:7b、DB_PATH 默认推导 `<PROJECT_ROOT>/data/spd.db`。
- 附加验证：config.py 确实在 import sentence_transformers 之前把 HF_ENDPOINT 写入 os.environ（模块注释与实现一致）。

### A5 代码规范（knowledge/python.md）✓
对 backend/config.py、backend/db.py 静态检查 + 运行时检查：
- 公共函数（get_conn/init_db/query_all/query_one/execute/now_iso）参数与返回值类型注解齐全（inspect 签名验证）；函数命名 snake_case，常量 UPPER_SNAKE。
- 路径操作全部 pathlib（6 个路径常量均为 `pathlib.Path` 实例），无字符串拼接路径。
- 无 `open()` 调用；文件读取用 `Path.read_text(encoding="utf-8")` 显式 utf-8。
- 无裸 `except:`（config.py 仅捕获具体 `ValueError` 并 `raise ... from exc`）；无吞异常。
- 无业务 print；无 `import *`；单行 ≤100 字符（awk 实测 0 行超标）；import 三组分组、无可变默认参数（默认 `params=()` 为不可变元组）。

### A6 schema.sql 与 design.md『数据库设计』一致 ✓
pytest 行为级验证（隔离库 + `PRAGMA foreign_keys=ON`）：
- conversations：id INTEGER PK AUTOINCREMENT、label TEXT NOT NULL DEFAULT ''（实测默认 ''）、created_at TEXT NOT NULL。
- messages：conversation_id NOT NULL REFERENCES conversations(id)（插入不存在会话 → IntegrityError）、role CHECK IN ('user','assistant')（插入 'system' → IntegrityError）、content NOT NULL、sources_json 可为 NULL、created_at TEXT NOT NULL。
- documents：filename UNIQUE（重复插入 → IntegrityError）。
- chunks：doc_id REFERENCES documents(id) ON DELETE CASCADE（删文档实测级联清空切片）、UNIQUE(doc_id, seq)（重复 → IntegrityError）、section_path NOT NULL DEFAULT ''（实测默认 ''）。
- 三表 created_at 均为 TEXT 类型且 NOT NULL（PRAGMA table_info 验证）。

## 失败详情
无。

## 遗留风险
1. pip 首装走官方源超时是环境问题：若后续卡（T3 embedding 下载 HF 模型）网络同样受限，建议沿用 hf-mirror（config 默认已设）与国内 pip 镜像。
2. `data/spd.db` 是 coder 中断前生成的，本报告 A2 已在其上实测幂等；pytest 另用隔离库验证，互不影响。
3. requirements.txt 只锁兼容范围（如 fastapi<2.0），本次实际解析到 sentence-transformers 6.2.0 / chromadb 1.5.9 等较新版本，T2+ 若用到已废弃 API 需另行发现（T1 范围内无影响）。
4. `.env.example` 中 DATA_DIR/DB_PATH 等 5 个路径项以注释形式给出（`#DATA_DIR=`），属"有默认值可不填"的设计，判定为覆盖；若 PM 期望必须为激活行，需Coder 微调（不构成 T1 失败）。

## 夹具与复现
- pytest 文件: `D:\ai\tests\test_47846874_T1_foundation.py`
- 夹具/临时产物目录（保留）: `D:\ai\.tmp_t1f_tester\`（test_spd.db 隔离测试库、pip-install.log 安装日志）
- 复现: `cd /d/ai && export PYTHONIOENCODING=utf-8 && py -3 -m pytest tests/test_47846874_T1_foundation.py -v`
