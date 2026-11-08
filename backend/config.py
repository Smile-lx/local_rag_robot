"""配置集中：.env 读取、路径推导与 RAG/LLM 参数。

- 所有路径常量基于 Path(__file__) 用 pathlib 推导，允许通过 .env 覆盖；
- .env 缺失时全部使用默认值（各默认值见 .env.example 注释）；
- HF_ENDPOINT 必须在任何 import sentence_transformers 的模块之前写入
  os.environ（huggingface_hub 在 import 时读取该变量）。本模块只提供
  常量与环境变量设置，不 import sentence_transformers；backend/rag 下
  的模块须先 import 本模块，再 import sentence_transformers。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------- 路径推导
# config.py 位于 <项目根>/backend/ 下，上跳两级即项目根（requirements/47846874/）
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

# 读取项目根下的 .env；文件不存在时 load_dotenv 静默跳过，全部走默认值
load_dotenv(PROJECT_ROOT / ".env")


def _env_str(name: str, default: str) -> str:
    """读取字符串环境变量；未设置或为空白时返回默认值。"""
    return (os.environ.get(name) or "").strip() or default


def _env_int(name: str, default: int) -> int:
    """读取整数环境变量；格式非法时快速失败，避免带病运行。"""
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"环境变量 {name} 应为整数，实际为 {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    """读取浮点环境变量；格式非法时快速失败，避免带病运行。"""
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"环境变量 {name} 应为浮点数，实际为 {raw!r}") from exc


def _env_path(name: str, default: Path) -> Path:
    """读取路径环境变量；未设置或为空白时返回 pathlib 推导的默认值。"""
    raw = (os.environ.get(name) or "").strip()
    return Path(raw) if raw else default


# ---------------------------------------------------------------- 路径配置
DATA_DIR: Path = _env_path("DATA_DIR", PROJECT_ROOT / "data")
DB_PATH: Path = _env_path("DB_PATH", DATA_DIR / "spd.db")
VECTOR_DIR: Path = _env_path("VECTOR_DIR", DATA_DIR / "vectorstore")
DOCS_DIR: Path = _env_path("DOCS_DIR", DATA_DIR / "docs")
FRONTEND_DIR: Path = _env_path("FRONTEND_DIR", PROJECT_ROOT / "frontend")

# ---------------------------------------------------------------- RAG 参数
RAG_TOP_K: int = _env_int("RAG_TOP_K", 4)
RAG_MIN_SCORE: float = _env_float("RAG_MIN_SCORE", 0.45)
CHUNK_TARGET: int = _env_int("CHUNK_TARGET", 450)
CHUNK_OVERLAP: int = _env_int("CHUNK_OVERLAP", 70)

# ---------------------------------------------------------- SQLite 连接加固
# 并发写等待上限（毫秒）：跨连接写冲突时等待而非立即报 database is locked
DB_BUSY_TIMEOUT_MS: int = _env_int("DB_BUSY_TIMEOUT_MS", 5000)
# 可选日志模式（WAL/DELETE/TRUNCATE/PERSIST/MEMORY）：留空用 SQLite 默认。
# 多读并发要求高的部署可设 WAL（会在库文件旁生成 -wal/-shm 附属文件）
JOURNAL_MODE: str = _env_str("JOURNAL_MODE", "").upper()
if JOURNAL_MODE not in {"WAL", "DELETE", "TRUNCATE", "PERSIST", "MEMORY"}:
    JOURNAL_MODE = ""

# ---------------------------------------------------------- HuggingFace 镜像
# huggingface_hub 在 import 时读取 HF_ENDPOINT，故必须先于
# import sentence_transformers 设置；默认走国内镜像，直连官方可改为
# https://huggingface.co
HF_ENDPOINT_DEFAULT: str = "https://hf-mirror.com"
HF_ENDPOINT: str = _env_str("HF_ENDPOINT", HF_ENDPOINT_DEFAULT)
if not (os.environ.get("HF_ENDPOINT") or "").strip():
    os.environ["HF_ENDPOINT"] = HF_ENDPOINT

# ---------------------------------------------------------- LLM 三级降级
# 第 1 级：OpenAI 兼容 HTTP API（配置了 LLM_API_KEY 才启用）
LLM_API_BASE: str = _env_str("LLM_API_BASE", "https://api.deepseek.com")
LLM_API_KEY: str = _env_str("LLM_API_KEY", "")
LLM_MODEL: str = _env_str("LLM_MODEL", "deepseek-chat")
# 第 2 级：本机 ollama（第 1 级未配置或请求失败时启用）
OLLAMA_BASE: str = _env_str("OLLAMA_BASE", "http://127.0.0.1:11434")
OLLAMA_MODEL: str = _env_str("OLLAMA_MODEL", "qwen2.5:7b")
# 第 3 级：以上均不可用时降级为"仅检索"（mode="retrieval_only"），无需配置

# ------------------------------------------- 多外部模型通道（2026-10-25 优化）
# LLM_CHANNELS 为逗号分隔的通道名列表；每个通道三变量：
#   LLM_CHANNEL_<名>_BASE / _KEY / _MODEL
# 通道按书写顺序即优先级依次尝试（前者失败自动切换后者），敏感外发闸
# 对全部外部通道统一生效。主流服务商接入参数已在 .env.example 预写好，
# 填 KEY 即用。未配置通道但设置了 LLM_API_KEY 时自动回退单通道（旧配置
# 兼容，model 标注为 "<模型>@openai-compat"）。
LLM_CHANNEL_NAMES: list[str] = [
    s.strip() for s in _env_str("LLM_CHANNELS", "").split(",") if s.strip()
]
LLM_CHANNELS: dict[str, dict[str, str]] = {}
for _name in LLM_CHANNEL_NAMES:
    _u = _name.upper()
    LLM_CHANNELS[_name] = {
        "base": _env_str(f"LLM_CHANNEL_{_u}_BASE", LLM_API_BASE),
        "key": _env_str(f"LLM_CHANNEL_{_u}_KEY", ""),
        "model": _env_str(f"LLM_CHANNEL_{_u}_MODEL", LLM_MODEL),
    }

# ------------------------------------------------- 数据安全（外发二次校验）
# 发往外部大模型 API 前扫描消息敏感信息（身份证/手机号/住院号等），
# 命中即拒绝外发并降级本地/仅检索；0/false/no/off 关闭（默认开启）
SENSITIVE_EGRESS_BLOCK: bool = (
    _env_str("SENSITIVE_EGRESS_BLOCK", "1").lower() not in {"0", "false", "no", "off"}
)

# ------------------------------------------------- 防幻觉多重校验（verify）
VERIFY_ANSWER: bool = _env_str("VERIFY_ANSWER", "1").lower() not in {"0", "false", "no", "off"}
# 生成答案忠实度（有依据字符占比）低于此值判 fail → 自动降级仅检索
FAITH_MIN: float = _env_float("FAITH_MIN", 0.5)
# 低于此值（但高于 FAITH_MIN）判 suspect → 返回但标注 verified 供人工核对
FAITH_SUSPECT: float = _env_float("FAITH_SUSPECT", 0.8)
