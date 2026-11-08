"""LLM 客户端三级降级（design.md『LLM 客户端三级降级』）。

按优先级依次探测，任一级成功即返回 ``mode="llm"``；全部不可用时返回
``mode="retrieval_only"``（answer 为空串，由 qa 层拼接检索原文兜底）：

1. ``.env`` 配置了 ``LLM_API_KEY`` → OpenAI 兼容
   ``POST {LLM_API_BASE}/chat/completions``（默认 deepseek，模型
   ``LLM_MODEL``，requests 直调，不引入 openai SDK）；
2. 否则（或第 1 级请求失败后）探测本机 ollama ``{OLLAMA_BASE}/api/tags``，
   通则 ``POST {OLLAMA_BASE}/api/chat``，模型 ``OLLAMA_MODEL``；
3. 都不可用 → ``mode="retrieval_only"``。

逐级下落只捕获具体异常类型（``requests.RequestException``（含 Timeout /
ConnectionError / HTTPError）、``ValueError``（JSON 解析）、``KeyError`` /
``TypeError`` / ``IndexError``（响应结构缺字段）），禁止裸 ``except:``；
每次下落记 warning 日志，不静默吞异常。

可测试性约定：

- 配置项（LLM_API_KEY / LLM_API_BASE / LLM_MODEL / OLLAMA_BASE /
  OLLAMA_MODEL）通过 ``from .. import config`` 在调用时读取，monkeypatch
  ``backend.config`` 属性即可生效（.env 覆盖天然支持）；
- 超时秒数优先读环境变量 ``LLM_TIMEOUT`` / ``OLLAMA_PROBE_TIMEOUT`` /
  ``OLLAMA_TIMEOUT``（.env 同名变量亦可），未设置时用模块常量默认值
  （design.md：接口 60s、探测 2s），monkeypatch 常量同样生效。

接口（冻结，供 qa 使用）::

    chat(messages: list[dict[str, str]]) -> LLMResult   # LLMResult(answer, mode)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import requests

from .. import config
from . import safety

__all__ = [
    "LLMResult",
    "chat",
    "status",
    "LLM_TIMEOUT_SECONDS",
    "OLLAMA_PROBE_TIMEOUT_SECONDS",
    "OLLAMA_CHAT_TIMEOUT_SECONDS",
]

logger = logging.getLogger(__name__)

#: 第 1 级 OpenAI 兼容接口超时（秒），design.md 约定 60s；env LLM_TIMEOUT 覆盖
LLM_TIMEOUT_SECONDS: int = 60
#: 第 2 级 ollama /api/tags 探测超时（秒），design.md 约定 2s；env OLLAMA_PROBE_TIMEOUT 覆盖
OLLAMA_PROBE_TIMEOUT_SECONDS: float = 2.0
#: 第 2 级 ollama /api/chat 生成超时（秒），本地推理较慢故大于探测；env OLLAMA_TIMEOUT 覆盖
OLLAMA_CHAT_TIMEOUT_SECONDS: float = 120.0

# 逐级下落时捕获的具体异常：网络族（requests）+ 响应解析/结构族
_FALLBACK_EXCEPTIONS: tuple[type[BaseException], ...] = (
    requests.RequestException,  # 含 Timeout / ConnectionError / HTTPError
    ValueError,                 # resp.json() 解析失败（JSONDecodeError 是其子类）
    KeyError,
    TypeError,
    IndexError,
)


@dataclass
class LLMResult:
    """一次对话补全的结果。

    Attributes:
        answer: 模型回答文本（已 strip）；mode 为 "retrieval_only" 时为空串。
        mode: "llm"（第 1/2 级成功）或 "retrieval_only"（全部不可用）。
        model: 实际生成通道标注（多模型 check，2026-10-25）：
            ``"{模型名}@openai-compat"`` / ``"{模型名}@ollama"``；
            retrieval_only 时为空串。
    """

    answer: str
    mode: str
    model: str = ""


def _env_timeout(env_name: str, default: float) -> float:
    """读取超时环境变量（秒）；未设置用默认值，非法值快速失败。"""
    raw = (os.environ.get(env_name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"环境变量 {env_name} 应为数字（秒），实际为 {raw!r}") from exc


def _external_channels() -> list[dict[str, str]]:
    """按优先级返回可用外部通道（已过滤未填 KEY 的通道）。

    命名通道（``LLM_CHANNELS``，2026-10-25 多模型预配置）按声明顺序优先；
    未配置命名通道且 ``LLM_API_KEY`` 存在时回退为单通道（旧配置兼容，
    通道名 openai-compat，model 标注 ``{模型}@openai-compat``）。
    """
    out: list[dict[str, str]] = []
    channels_cfg = getattr(config, "LLM_CHANNELS", {}) or {}
    for name in getattr(config, "LLM_CHANNEL_NAMES", []) or []:
        cfg = dict(channels_cfg.get(name, {}))
        if cfg.get("key"):
            cfg["name"] = name
            out.append(cfg)
    if not out and config.LLM_API_KEY:
        out.append(
            {
                "name": "openai-compat",
                "base": config.LLM_API_BASE,
                "key": config.LLM_API_KEY,
                "model": config.LLM_MODEL,
            }
        )
    return out


def chat(messages: list[dict[str, str]]) -> LLMResult:
    """多通道降级对话补全：外部通道（按优先级）→ 本机 ollama → retrieval_only。

    Args:
        messages: OpenAI 格式消息列表，如
            ``[{"role": "system", "content": ...}, {"role": "user", "content": ...}]``，
            ollama /api/chat 兼容同一格式。

    Returns:
        任一通道成功 → ``LLMResult(answer, "llm", model="{模型}@{通道名}")``；
        全部不可用 → ``LLMResult("", "retrieval_only")``。网络/解析类异常不上抛。
    """
    channels = _external_channels()
    if channels:
        if _egress_blocked_by_safety(messages):
            logger.warning(
                "敏感信息二次校验未通过：已拒绝全部外部通道外发，降级到本机 ollama / 仅检索"
            )
        else:
            for channel in channels:
                try:
                    return LLMResult(
                        answer=_chat_channel(channel, messages),
                        mode="llm",
                        model=f"{channel['model']}@{channel['name']}",
                    )
                except _FALLBACK_EXCEPTIONS as exc:
                    logger.warning(
                        "外部通道 %s（%s）失败，尝试下一通道/降级: %s: %s",
                        channel["name"],
                        channel["model"],
                        type(exc).__name__,
                        exc,
                    )
    try:
        return LLMResult(
            answer=_chat_ollama(messages), mode="llm", model=f"{config.OLLAMA_MODEL}@ollama"
        )
    except _FALLBACK_EXCEPTIONS as exc:
        logger.warning(
            "第 2 级 ollama 不可用，降级为仅检索: %s: %s",
            type(exc).__name__,
            exc,
        )
    return LLMResult(answer="", mode="retrieval_only")


def status() -> dict:
    """多模型通道诊断（只读探测，2026-10-25）：供 GET /api/models 使用。

    返回各通道配置/连通状态、当前生效通道与敏感外发闸状态::

        {
          "sensitive_gate": bool,          # 外发二次校验是否开启
          "channels": [
            {"level": 1, "type": "openai_compatible", "configured": bool,
             "base": str, "model": str},   # 外部通道不实测连通（避免真实计费请求）
            {"level": 2, "type": "ollama", "base": str, "model": str,
             "reachable": bool, "available_models": list[str] | None},
          ],
          "effective": "openai_compatible" | "ollama" | "retrieval_only",
        }

    effective 为"无敏感信息时将走哪条通道"；含敏感信息的单条消息会被
    外发闸强制降级（见 safety 模块），不影响本探测结果。
    """
    # 外部通道：命名通道（多模型预配置）逐个上报；未配置命名通道时上报
    # 旧单通道（LLM_API_*）作占位，保持 /api/models 结构稳定
    names = list(getattr(config, "LLM_CHANNEL_NAMES", []) or [])
    channels_cfg = getattr(config, "LLM_CHANNELS", {}) or {}
    if names:
        ext_entries = [
            {
                "level": 1,
                "type": "openai_compatible",
                "name": name,
                "base": channels_cfg.get(name, {}).get("base", config.LLM_API_BASE),
                "model": channels_cfg.get(name, {}).get("model", config.LLM_MODEL),
                "configured": bool(channels_cfg.get(name, {}).get("key")),
            }
            for name in names
        ]
    else:
        ext_entries = [
            {
                "level": 1,
                "type": "openai_compatible",
                "name": "openai-compat",
                "base": config.LLM_API_BASE,
                "model": config.LLM_MODEL,
                "configured": bool(config.LLM_API_KEY),
            }
        ]
    ext_available = any(entry["configured"] for entry in ext_entries)
    ollama_reachable = False
    ollama_models: list[str] | None = None
    try:
        probe = requests.get(
            f"{config.OLLAMA_BASE.rstrip('/')}/api/tags",
            timeout=_env_timeout("OLLAMA_PROBE_TIMEOUT", OLLAMA_PROBE_TIMEOUT_SECONDS),
        )
        probe.raise_for_status()
        ollama_models = sorted(
            {
                str(m.get("name", ""))
                for m in probe.json().get("models", [])
                if m.get("name")
            }
        )
        ollama_reachable = True
    except _FALLBACK_EXCEPTIONS as exc:
        logger.info("模型诊断: ollama 不可达: %s: %s", type(exc).__name__, exc)
    if ext_available:
        effective = "openai_compatible"
    elif ollama_reachable:
        effective = "ollama"
    else:
        effective = "retrieval_only"
    return {
        "sensitive_gate": bool(config.SENSITIVE_EGRESS_BLOCK),
        "channels": ext_entries
        + [
            {
                "level": 2,
                "type": "ollama",
                "base": config.OLLAMA_BASE,
                "model": config.OLLAMA_MODEL,
                "reachable": ollama_reachable,
                "available_models": ollama_models,
            }
        ],
        "effective": effective,
    }


def _egress_blocked_by_safety(messages: list[dict[str, str]]) -> bool:
    """外发二次校验：消息内容命中敏感模式时拒绝走外部 API。

    仅在 ``config.SENSITIVE_EGRESS_BLOCK`` 开启时生效（默认开启）；扫描
    对象为全部消息拼接文本（用户问题 + 检索片段 + 提示词）。审计日志只
    记模式名与脱敏预览（见 safety._mask_preview），不落敏感原文。
    """
    if not config.SENSITIVE_EGRESS_BLOCK:
        return False
    joined = "\n".join(str(m.get("content", "")) for m in messages)
    findings = safety.scan(joined)
    if findings:
        logger.warning(
            "敏感信息外发拦截（审计）: %s",
            "; ".join(f"{f.pattern}@{f.preview}" for f in findings),
        )
        return True
    return False


def _chat_channel(channel: dict[str, str], messages: list[dict[str, str]]) -> str:
    """按通道配置 POST {base}/chat/completions（OpenAI 兼容，多通道通用）。

    可抛出的异常类型均在 ``_FALLBACK_EXCEPTIONS`` 内，由 ``chat`` 捕获后
    尝试下一通道或降级，本函数不做兜底。
    """
    url = f"{channel['base'].rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {channel['key']}",
        "Content-Type": "application/json",
    }
    response = requests.post(
        url,
        json={"model": channel["model"], "messages": messages, "stream": False},
        headers=headers,
        timeout=_env_timeout("LLM_TIMEOUT", LLM_TIMEOUT_SECONDS),
    )
    response.raise_for_status()
    data = response.json()
    content = data["choices"][0]["message"]["content"]
    if not isinstance(content, str) or not content.strip():
        raise ValueError(f"{channel['model']} 返回了空回答")
    return content.strip()


def _chat_openai_compatible(messages: list[dict[str, str]]) -> str:
    """旧单通道路径（LLM_API_* 三变量），转发到通用通道实现，保持兼容。"""
    return _chat_channel(
        {
            "name": "openai-compat",
            "base": config.LLM_API_BASE,
            "key": config.LLM_API_KEY,
            "model": config.LLM_MODEL,
        },
        messages,
    )


def _chat_ollama(messages: list[dict[str, str]]) -> str:
    """第 2 级：先探测 /api/tags，通则 POST {OLLAMA_BASE}/api/chat。

    探测通但对话失败（如模型未 pull）同样按异常上抛，由 ``chat`` 落到
    第 3 级。
    """
    base = config.OLLAMA_BASE.rstrip("/")
    probe = requests.get(
        f"{base}/api/tags",
        timeout=_env_timeout("OLLAMA_PROBE_TIMEOUT", OLLAMA_PROBE_TIMEOUT_SECONDS),
    )
    probe.raise_for_status()
    response = requests.post(
        f"{base}/api/chat",
        json={"model": config.OLLAMA_MODEL, "messages": messages, "stream": False},
        timeout=_env_timeout("OLLAMA_TIMEOUT", OLLAMA_CHAT_TIMEOUT_SECONDS),
    )
    response.raise_for_status()
    data = response.json()
    content = data["message"]["content"]
    if not isinstance(content, str) or not content.strip():
        raise ValueError(f"ollama {config.OLLAMA_MODEL} 返回了空回答")
    return content.strip()
