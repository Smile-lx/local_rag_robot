# -*- coding: utf-8 -*-
"""FastAPI 应用装配：API 路由注册、CORS、启动语料装载与静态托管。

装配顺序（design.md『RESTful 契约』冻结）：

1. GET /api/health 健康检查；
2. 三组 API 路由（chat / conversations / documents）注册；
3. CORS 中间件（``allow_origins=["*"]``，本机原型）；
4. ``StaticFiles(html=True)`` 挂载 "/" 托管 ``frontend/``——必须在全部
   API 路由注册之后，否则 "/" 的静态兜底会抢在 /api/* 之前命中。

启动语料装载：``create_app()`` 内调用一次 ``ingest.ingest_docs_dir()``
（只处理 ``data/docs`` 下非 ``_`` 开头的 Markdown，符合 design 意图：
起服即可问答，语料兜底灌入）；同名文档幂等更新属预期；失败仅告警不
阻断起服（空库可先经知识管理页手工录入文档）。

启动方式（监听 127.0.0.1:8000，README 由 T7 注明）::

    cd requirements/47846874
    py -3 -m backend.api.main                             # __main__ 入口
    py -3 -m uvicorn backend.api.main:app --port 8000     # 等价方式
"""
from __future__ import annotations

import logging
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .. import config
from ..rag import ingest, llm_client
from . import routes_chat, routes_conversations, routes_documents

__all__ = ["create_app", "app", "HOST", "PORT"]

logger = logging.getLogger(__name__)

#: uvicorn 入口监听地址与端口（本机原型，无生产部署）
HOST: str = "127.0.0.1"
PORT: int = 8000


def create_app() -> FastAPI:
    """构建 FastAPI 应用：健康检查 → API 路由 → CORS → 语料装载 → 静态托管。"""
    app = FastAPI(title="医院 SPD 智能客服智能体", version="1.0.0")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        """健康检查端点：GET /api/health → {"status": "ok"}。"""
        return {"status": "ok"}

    @app.get("/api/models")
    def models() -> dict[str, Any]:
        """多模型通道诊断（2026-10-25）：外部 API / 本机 ollama 配置与连通
        状态、当前生效通道、敏感外发闸状态（只读探测，不改任何状态）。"""
        return llm_client.status()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(routes_documents.router)
    app.include_router(routes_conversations.router)
    app.include_router(routes_chat.router)

    _load_startup_corpus()

    # 静态托管挂在最后；目录缺失时兜底创建，避免 StaticFiles(check_dir)
    # 在 import 阶段直接抛错阻断起服（路径取 config.FRONTEND_DIR，不依赖 cwd）
    config.FRONTEND_DIR.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/",
        StaticFiles(directory=str(config.FRONTEND_DIR), html=True),
        name="frontend",
    )
    return app


def _load_startup_corpus() -> None:
    """启动时装载 data/docs 语料（幂等更新；任何失败不阻断起服）。

    语料装载属"起服即可问答"的增强步骤：损坏语料/向量库异常不应拖垮
    HTTP 服务，故在边界处宽捕获并告警（各具体异常已由下层记录或上抛）。

    装载后执行双库对账自愈（ingest.reconcile，2026-10-25 加固）：清理
    孤儿向量、重建缺失向量，使 SQLite 与 Chroma 收敛到一致状态。
    """
    try:
        results = ingest.ingest_docs_dir()
        stats = ingest.reconcile()
        if stats.get("orphan_vectors_removed") or stats.get("vectors_rebuilt"):
            logger.info(
                "启动对账自愈: 清理孤儿向量 %s 份, 重建向量 %s 份",
                stats["orphan_vectors_removed"],
                stats["vectors_rebuilt"],
            )
    except Exception as exc:  # noqa: BLE001 启动边界容错，见 docstring
        logger.warning("启动语料装载失败（服务继续运行）: %s", exc)
        return
    if results:
        logger.info(
            "启动语料装载完成: %s 份文档 %s",
            len(results),
            [(name, count) for name, _, count in results],
        )
    else:
        logger.warning("启动语料装载 0 份（目录为空或不存在）: %s", config.DOCS_DIR)


#: 模块级 app 供 ``uvicorn backend.api.main:app`` 部署使用
app = create_app()


if __name__ == "__main__":
    # 模块直接运行（py -3 -m backend.api.main）时起服务，端口 8000
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host=HOST, port=PORT)
