# -*- coding: utf-8 -*-
"""检索质量评估脚本：chunk 目标参数 300/400/600 三组对比（recall@3/@5 + MRR）。

流程（design.md『评估脚本』一节）：

1. 解析 ``data/docs/_qa_pairs.md`` 的"问题 | 关键词1,关键词2"对照表；
2. 对 chunk 目标 300/400/600 三组参数，把 ``backend.rag.chunker`` 的模块常量
   （CHUNK_MIN/CHUNK_MAX/SECTION_MAX/OVERLAP，及派生常量 HARD_SPLIT_STEP）
   按组 setattr 覆盖后重新切片语料——chunker 内部函数均在调用时读模块常量，
   故无需参数化改造、更不重复实现切片逻辑；
3. 每组重建到 **tempfile 临时目录**：SQLite 与 Chroma（PersistentClient 路径）
   均重定向到临时目录，绝不写 ``data/spd.db`` 与 ``data/vectorstore`` 正式库；
4. 逐题 ``vectorstore.search(question, k=max(5, RAG_TOP_K))``，统计三指标：
   - 命中判定：返回切片**原文包含关键词**（子串匹配）；
   - recall@n：前 n 条结果内能找到的关键词数 / 关键词总数（按组汇总）；
   - MRR：每题取首个命中切片排名的倒数（无命中记 0）后取均值；
5. 打印 markdown 对比表（三组 × 三指标，附每组切片总数）到 stdout。

用法（cwd = requirements/47846874/）::

    export PYTHONIOENCODING=utf-8
    py -3 tools/eval_recall.py

复用关系：切片走 ``chunker.split``（常量覆盖见上）；入库走
``ingest.ingest_docs_dir``（内部完成 SQLite/Chroma 双写）；检索走
``vectorstore.search``；向量由 ``embedder``（bge-small-zh-v1.5）生成。
bge 模型首次加载需数十秒，属预期。
"""
from __future__ import annotations

import logging
import shutil
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

# 脚本以 `py -3 tools/eval_recall.py` 运行时 sys.path[0] 是 tools/，
# 须把项目根（requirements/47846874/）加入 sys.path 才能 import backend
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import chromadb  # noqa: E402  (须先完成上面的 sys.path 引导)
from chromadb.api.client import Client as ChromaClient  # noqa: E402

from backend import config, db  # noqa: E402
from backend.rag import chunker, ingest, vectorstore  # noqa: E402

# ---------------------------------------------------------------- 常量
#: 参与对比的 chunk 目标长度（字符），语义对应 .env 的 CHUNK_TARGET
CHUNK_GROUP_TARGETS: tuple[int, ...] = (300, 400, 600)

#: 评估集文件名（"_" 开头，按约定不参与向量化入库）
QA_PAIRS_FILENAME: str = "_qa_pairs.md"

#: 参数推导基准：线上默认目标 450 → 块长 400~500（带宽 ±50）、重叠 70
_BAND: int = 50
_REF_TARGET: int = 450
_REF_OVERLAP: int = 70

#: chunker 中需按组覆盖（并还原）的模块常量名，HARD_SPLIT_STEP 为派生量
_CHUNK_CONST_NAMES: tuple[str, ...] = (
    "CHUNK_MIN",
    "CHUNK_MAX",
    "SECTION_MAX",
    "OVERLAP",
    "HARD_SPLIT_STEP",
)


@dataclass
class QAPair:
    """一条评估样例：问题及其应在语料原文中命中的关键词列表。"""

    question: str
    keywords: list[str]


@dataclass
class GroupResult:
    """一个 chunk 参数组的评估结果（参数、切片规模与三项指标）。"""

    target: int
    chunk_min: int
    chunk_max: int
    section_max: int
    overlap: int
    chunk_total: int
    recall_at_3: float
    recall_at_5: float
    mrr: float


def parse_qa_pairs(path: Path) -> list[QAPair]:
    """解析"问题 | 关键词1,关键词2"对照表。

    Args:
        path: ``_qa_pairs.md`` 路径；``#`` 注释行与空行跳过。

    Returns:
        QAPair 列表（至少 5 组，每组至少 1 个关键词，否则退出报错）。
    """
    if not path.is_file():
        raise SystemExit(f"评估集不存在: {path}")
    pairs: list[QAPair] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        question, sep, keyword_part = line.partition("|")
        keywords = [kw.strip() for kw in keyword_part.split(",") if kw.strip()]
        question = question.strip()
        if not sep or not question or not keywords:
            raise SystemExit(
                f"{path}:{line_no} 格式应为『问题 | 关键词1,关键词2』，实际: {line!r}"
            )
        pairs.append(QAPair(question=question, keywords=keywords))
    if len(pairs) < 5:
        raise SystemExit(f"评估集至少需 5 组，实际解析到 {len(pairs)} 组: {path}")
    return pairs


def _group_params(target: int) -> tuple[int, int, int, int]:
    """由 chunk 目标长度推导 (CHUNK_MIN, CHUNK_MAX, SECTION_MAX, OVERLAP)。

    对照线上默认（目标 450 → 块长 400~500、重叠 70）：块长带宽固定 ±50，
    SECTION_MAX 与 CHUNK_MAX 同值（段长不超块长硬上限），重叠按目标等比缩放。
    """
    overlap = round(target * _REF_OVERLAP / _REF_TARGET)
    return target - _BAND, target + _BAND, target + _BAND, overlap


@contextmanager
def _override_chunk_params(
    chunk_min: int, chunk_max: int, section_max: int, overlap: int
) -> Iterator[None]:
    """临时覆盖 chunker 模块常量（含派生常量 HARD_SPLIT_STEP），退出时还原。"""
    saved = {name: getattr(chunker, name) for name in _CHUNK_CONST_NAMES}
    try:
        chunker.CHUNK_MIN = chunk_min
        chunker.CHUNK_MAX = chunk_max
        chunker.SECTION_MAX = section_max
        chunker.OVERLAP = overlap
        chunker.HARD_SPLIT_STEP = chunk_max - overlap  # 派生常量须同步重算
        yield
    finally:
        for name, value in saved.items():
            setattr(chunker, name, value)


@contextmanager
def _temp_storage(tag: str) -> Iterator[Path]:
    """把 SQLite 与 Chroma 存储重定向到 tempfile 临时目录（退出时还原+清理）。

    ``db.DB_PATH`` 与 ``vectorstore.VECTOR_DIR`` 都是模块级绑定，setattr
    覆盖即重定向；``vectorstore._client`` 是进程级单例，指向新目录前须置
    None。Windows 下 Chroma 会锁临时目录文件，退出前清 chromadb 系统缓存
    再删目录；清理失败仅遗留 %TEMP% 下垃圾，绝不影响 data/ 正式库。
    """
    tmp_root = Path(tempfile.mkdtemp(prefix=f"spd_eval_{tag}_"))
    saved_vector_dir = vectorstore.VECTOR_DIR
    saved_db_path = db.DB_PATH
    vectorstore.VECTOR_DIR = tmp_root / "vectorstore"
    vectorstore._client = None  # noqa: SLF001  单例须重置才会指向新目录
    db.DB_PATH = tmp_root / "eval.db"
    try:
        yield tmp_root
    finally:
        vectorstore._client = None  # noqa: SLF001
        vectorstore.VECTOR_DIR = saved_vector_dir
        db.DB_PATH = saved_db_path
        try:
            ChromaClient.clear_system_cache()
        except Exception as exc:  # 清缓存失败只影响临时目录清理，不影响结果
            logging.warning("清理 chromadb 系统缓存失败: %s", exc)
        shutil.rmtree(tmp_root, ignore_errors=True)


def _keywords_found(
    hits: list[vectorstore.Hit], keywords: list[str], top_n: int
) -> int:
    """统计前 top_n 条返回切片原文中能找到（子串包含）的关键词个数。"""
    texts = [hit.text for hit in hits[:top_n]]
    return sum(1 for kw in keywords if any(kw in text for text in texts))


def _first_hit_rank(hits: list[vectorstore.Hit], keywords: list[str]) -> int:
    """返回首个原文含任一关键词的切片排名（1 起）；前 k 条无命中返回 0。"""
    for rank, hit in enumerate(hits, start=1):
        if any(kw in hit.text for kw in keywords):
            return rank
    return 0


def evaluate_group(target: int, pairs: list[QAPair], k: int) -> GroupResult:
    """评估一个 chunk 目标参数组：重切片 → 临时库重建 → 逐题检索统计。

    Args:
        target: chunk 目标长度（字符）。
        pairs: 评估集（问题 + 关键词）。
        k: 每题检索条数（max(5, RAG_TOP_K)，故 recall@5 分母恒有效）。

    Returns:
        GroupResult（含该组参数、切片总数与 recall@3 / recall@5 / MRR）。
    """
    chunk_min, chunk_max, section_max, overlap = _group_params(target)
    started = time.perf_counter()
    with _temp_storage(tag=f"t{target}"):
        # 常量覆盖只需作用于切片阶段；检索在临时库目录重定向内进行
        with _override_chunk_params(chunk_min, chunk_max, section_max, overlap):
            ingested = ingest.ingest_docs_dir()
        if not ingested:
            raise SystemExit(f"语料入库 0 份，请检查 {config.DOCS_DIR} 下 spd_*.md")
        chunk_total = sum(count for _, _, count in ingested)
        keywords_total = sum(len(pair.keywords) for pair in pairs)
        found_at_3 = found_at_5 = 0
        reciprocal_rank_sum = 0.0
        for pair in pairs:
            hits = vectorstore.search(pair.question, k=k)
            found_at_3 += _keywords_found(hits, pair.keywords, 3)
            found_at_5 += _keywords_found(hits, pair.keywords, 5)
            rank = _first_hit_rank(hits, pair.keywords)
            if rank:
                reciprocal_rank_sum += 1.0 / rank
    elapsed = time.perf_counter() - started
    print(
        f"[chunk 目标 {target}] 语料 {len(ingested)} 份 / {chunk_total} 切片，"
        f"{len(pairs)} 题检索完成，耗时 {elapsed:.1f}s"
    )
    return GroupResult(
        target=target,
        chunk_min=chunk_min,
        chunk_max=chunk_max,
        section_max=section_max,
        overlap=overlap,
        chunk_total=chunk_total,
        recall_at_3=found_at_3 / keywords_total,
        recall_at_5=found_at_5 / keywords_total,
        mrr=reciprocal_rank_sum / len(pairs),
    )


def _print_report(results: list[GroupResult], pairs: list[QAPair], k: int) -> None:
    """打印 markdown 对比表（三组 × 三指标，附每组参数与切片总数）。"""
    keywords_total = sum(len(pair.keywords) for pair in pairs)
    lines = [
        "## chunk 目标参数 × 检索质量对比",
        "",
        f"- 评估集：{len(pairs)} 题 / {keywords_total} 个关键词"
        f"（data/docs/{QA_PAIRS_FILENAME}）",
        f"- 检索：k = max(5, RAG_TOP_K) = {k}；命中判定 = 返回切片原文包含关键词",
        "- 指标：recall@n = 前 n 条内找到的关键词占比；"
        "MRR = 首个命中切片排名倒数的均值（无命中记 0）",
        "",
        "| chunk 目标 | CHUNK_MIN~CHUNK_MAX | SECTION_MAX | OVERLAP"
        " | 切片总数 | recall@3 | recall@5 | MRR |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.target} | {r.chunk_min}~{r.chunk_max} | {r.section_max}"
            f" | {r.overlap} | {r.chunk_total}"
            f" | {r.recall_at_3:.3f} | {r.recall_at_5:.3f} | {r.mrr:.3f} |"
        )
    print("\n".join(lines))


def main() -> None:
    """入口：解析评估集 → 三组参数逐一评估 → 输出对比表。"""
    if hasattr(sys.stdout, "reconfigure"):  # 防 Windows 控制台 GBK 编码崩溃
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    qa_path = config.DOCS_DIR / QA_PAIRS_FILENAME
    pairs = parse_qa_pairs(qa_path)
    k = max(5, config.RAG_TOP_K)
    print(
        f"评估集: {qa_path}（{len(pairs)} 题）；检索 k={k}；"
        "bge 模型首次加载需数十秒，请稍候…"
    )
    results = [evaluate_group(target, pairs, k) for target in CHUNK_GROUP_TARGETS]
    _print_report(results, pairs, k)


if __name__ == "__main__":
    main()
