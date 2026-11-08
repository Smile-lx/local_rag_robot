# 设备环境清单与迁移部署备份（需求 47846874）

> 用途：新设备部署/迁移前的环境核对与备份操作手册。**用户要求：今后任何迁移/部署操作前必须先执行本文『迁移备份清单』一节。**

## 一、设备与系统要求
| 项 | 要求 | 说明 |
|---|---|---|
| 操作系统 | Windows 10/11（Git Bash）或 Linux 均可 | 开发实测 Windows + Git Bash；Linux 需把 `py -3` 换成 `python3` |
| 内存 | ≥8GB | torch 推理 + Chroma + FastAPI 同进程，实测常驻 ~1.5-2.5GB，留余量 |
| 磁盘 | ≥8GB 空闲 | 依赖 ~5GB（torch 为主）+ bge 模型缓存 ~0.1GB + 数据库/向量库（随语料增长） |
| 网络 | 首次安装需外网 | pypi 走清华镜像；HF 模型走 hf-mirror（见下）；之后可完全离线运行（不配 LLM key 时为检索增强模式） |

## 二、软件环境
| 项 | 版本/获取 | 核验命令 |
|---|---|---|
| Python | ≥3.10（实测 3.10.0） | `py -3 --version` |
| 依赖包 | requirements.txt（fastapi 0.162/uvicorn 0.54/chromadb 1.5.9/sentence-transformers 6.2.0/torch 2.14.1/transformers 5.20.2/requests/python-dotenv/pytest 9.1.1） | `py -3 -c "import chromadb, sentence_transformers, fastapi"` |
| 安装命令 | `py -3 -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --timeout 120 --retries 5` | 官方 PyPI 实测读超时，必须走清华镜像 |
| Node（可选） | 任意现代版 | 仅前端 JS 语法检查（node --check）用，非运行必需 |

## 三、模型资源
- `BAAI/bge-small-zh-v1.5`（中文 embedding，约 100MB），首次运行自动下载。
- **国内镜像坑**：`HF_ENDPOINT=https://hf-mirror.com` 且 `HF_HUB_DISABLE_XET=1`（hf-xet 协议直连官方 CAS 会 401，详见 knowledge/pitfalls/hf-mirror-xet-401.md）；下载完成后缓存加载不再需要联网。
- 缓存位置：`~/.cache/huggingface/hub/models--BAAI--bge-small-zh-v1.5`（迁移时可直接整目录拷贝以省下载）。

## 四、配置项（.env，全部可缺省；完整注释见 .env.example）
| 组 | 键 | 默认 | 说明 |
|---|---|---|---|
| RAG | RAG_TOP_K | 4 | 检索条数 |
| | RAG_MIN_SCORE | 0.45 | 拒答阈值（2026-10-25 由 0.30 上调，实测相关 0.75+/无关 0.30~0.38） |
| | CHUNK_TARGET / CHUNK_OVERLAP | 450 / 70 | 切片目标与重叠 |
| 模型源 | HF_ENDPOINT | https://hf-mirror.com | HF 镜像 |
| LLM | LLM_API_KEY / LLM_API_BASE / LLM_MODEL | 无 / deepseek / deepseek-chat | 单通道（向后兼容） |
| | LLM_CHANNELS | 无 | 多通道故障切换（DeepSeek/智谱/通义/Kimi/豆包/OpenAI/自定义网关模板见 .env.example） |
| | OLLAMA_MODEL / OLLAMA_BASE | qwen2.5:7b / 127.0.0.1:11434 | 本地 ollama 通道 |
| 超时 | LLM_TIMEOUT / OLLAMA_PROBE_TIMEOUT / OLLAMA_TIMEOUT | 60 / 2 / 120 秒 | 可按网络调小 |
| 安全 | SENSITIVE_EGRESS_BLOCK | 1（开） | 外发 LLM 前敏感信息扫描（身份证/手机号/银行卡/住院号），命中拒发降级 |
| 校验 | FAITH_MIN / FAITH_SUSPECT | 0.5 / 0.8 | 防幻觉忠实度阈值 |
| 路径 | DB_PATH / VECTOR_DIR / DOCS_DIR | data/ 下推导 | SQLite/Chroma/语料目录（可用绝对路径迁到数据盘） |

## 五、运行
```bash
cd requirements/47846874
py -3 -m uvicorn backend.api.main:app --port 8001   # 8000 常被打印服务占用，健康检查只认 /api/health
```
浏览器 http://127.0.0.1:8001（聊天页）/ knowledge.html（知识管理页）。

## 六、迁移备份清单（**每次迁移/部署前必做，做完再动环境**）
1. **代码与配置**：整仓 `E:\ai\ai` 打包（至少含 requirements/47846874/、scripts/、.claude/、knowledge/、tests/、design.md、CLAUDE.md、.mcp.json）。命令：`tar -czf backup-ai-repo-$(date +%Y%m%d).tar.gz -C /e ai/ai`
2. **数据资产**（在 1 内，但单独强调）：`requirements/47846874/data/spd.db`（SQLite 全部对话/文档）、`data/vectorstore/`（Chroma 向量库）、`data/docs/`（语料，含 `_qa_pairs.md`）。
3. **模型缓存**：`~/.cache/huggingface/hub/models--BAAI--bge-small-zh-v1.5` 整目录（省重新下载）。
4. **依赖清单快照**：`py -3 -m pip freeze > requirements/47846874/reports/pip-freeze-$(date +%Y%m%d).txt`（精确复现用；历史基线见 reports/env-install-2026-10-25.{md,log}）。
5. **恢复/迁移后探活**：`py -3 -c "import chromadb, sentence_transformers, fastapi"` → 起服务 → `curl http://127.0.0.1:8001/api/health` 返回 `{"status":"ok"}` → 知识管理页可见既有文档列表（数据迁移成功的标志）。
6. 备份产物统一放 `requirements/47846874/reports/backups/` 或仓库外安全位置，**不放进 data/ 运行目录**。
