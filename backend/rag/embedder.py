"""embedding 封装：基于 sentence-transformers 的中文向量生成。

- 使用 ``BAAI/bge-small-zh-v1.5`` 中文 embedding 模型（512 维）；
- 模块级单例：首次调用时懒加载（不在 import 时实例化，避免 import 即触发
  数百 MB 模型下载），进程内复用同一个 SentenceTransformer 实例；
- 必须先 import ``backend.config`` 再 import sentence_transformers：config
  在 import 阶段把 ``HF_ENDPOINT``（默认 https://hf-mirror.com 镜像）写入
  os.environ，而 huggingface_hub 在 import 时读取该变量。本模块位于
  ``backend/rag/`` 下，config 在 ``backend/`` 下，故用 ``..config``。

接口（冻结，供 vectorstore / ingest / qa 使用）::

    embed_documents(texts: list[str]) -> numpy.ndarray   # shape (n, dim)
    embed_query(text: str) -> numpy.ndarray              # shape (dim,)
"""
from __future__ import annotations

import os
from typing import Any

# config 必须先于 sentence_transformers 导入：config 在 import 阶段写入 HF_ENDPOINT
from ..config import PROJECT_ROOT  # noqa: F401  (确保镜像环境变量已设置)

import numpy as np

import torch
from numpy.typing import NDArray
from sentence_transformers import SentenceTransformer

__all__ = ["embed_documents", "embed_query", "MODEL_NAME", "EMBEDDING_DIM"]

MODEL_NAME: str = "BAAI/bge-small-zh-v1.5"

# bge-small-zh-v1.5 的输出维度（文档值，供类型标注/文档参考，不参与计算）
EMBEDDING_DIM: int = 512

# 单例缓存：首次调用时赋值，进程生命周期内复用
_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    """懒加载并缓存全局唯一的 SentenceTransformer 实例。

    首次调用会从 HF_ENDPOINT 下载模型（约 100MB 量级，属预期耗时），缓存到
    本地（默认 ``~/.cache/huggingface``）后后续进程直接复用。下载/加载失败
    抛出 ``RuntimeError``，附带镜像与预下载指引。
    """
    global _model
    if _model is not None:
        return _model
    endpoint = os.environ.get("HF_ENDPOINT", "")
    try:
        _model = SentenceTransformer(MODEL_NAME, device=_pick_device())
    except Exception as exc:  # 下载/加载失败原因多样（网络/OSError/协议），统一转为带指引的报错
        raise RuntimeError(
            f"embedding 模型 {MODEL_NAME} 下载/加载失败。"
            f"当前 HF_ENDPOINT={endpoint or '(未设置)'}。"
            "排查步骤：1) 确认网络可达镜像站（默认 https://hf-mirror.com，"
            "可在 .env 或环境变量 HF_ENDPOINT 修改）；"
            "2) 手动预下载一次：PYTHONIOENCODING=utf-8 py -3 -c \""
            f"from sentence_transformers import SentenceTransformer as S; "
            f"S('{MODEL_NAME}')\"。"
            f"原始错误: {exc}"
        ) from exc
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    return _model


def _pick_device() -> str:
    """返回可用设备（cuda 优先，否则 cpu）。"""
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def embed_documents(texts: list[str]) -> NDArray[Any]:
    """把一段文本列表批量编码为归一化向量。

    Args:
        texts: 待编码文本列表；空列表返回 shape ``(0, EMBEDDING_DIM)`` 的空数组。

    Returns:
        shape ``(len(texts), dim)`` 的 float32 数组，向量已 L2 归一化
        （``normalize_embeddings=True``，与余弦度量配套）。
    """
    if not texts:
        return np.empty((0, EMBEDDING_DIM), dtype=np.float32)
    return _get_model().encode(
        texts,
        normalize_embeddings=True,
        batch_size=16,
        convert_to_numpy=True,
        show_progress_bar=False,
    )


def embed_query(text: str) -> NDArray[Any]:
    """把单个查询文本编码为归一化向量。

    Args:
        text: 用户查询 / 自测样例句子。

    Returns:
        shape ``(dim,)`` 的 float32 向量，已 L2 归一化。
    """
    return embed_documents([text])[0]
