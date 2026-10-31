"""列出某个模型服务当前可用的模型（给网页设置里的下拉框用），结果在进程内缓存 1 小时。

- anthropic      官方 Models API（client.models.list），按发布时间从新到旧
- openai_compat  ``GET {base_url}/models``（DeepSeek、Gemini、OpenRouter……都支持）；Gemini 只留能聊天的文本模型
- fake           固定返回 ["fake"]
没有 key 或请求失败时返回空列表和原因，网页会保留当前值。
"""

from __future__ import annotations

import re
import threading
import time

import httpx

from ..core.config import AppConfig

CACHE_SECONDS = 3600
_CACHE: dict[str, tuple[float, list[str], str]] = {}
_LOCK = threading.Lock()
# Gemini / Google 列表里不能用来做文本对话的模型
_NOT_CHAT = re.compile(r"(tts|image|imagen|embedding|embed|aqa|veo|live|audio|robotics|computer-use|lyria|learnlm|deep-research|antigravity|nano-banana|transcribe|customtools)", re.I)


def _version_key(name: str) -> tuple:
    """大致按版本号从新到旧：gemini-3-flash > gemini-2.5-pro；没有数字的排后面。"""
    nums = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)", name)[:2]]
    nums += [0.0] * (2 - len(nums))  # 补齐两位，避免数字和名字互相比较
    return (-nums[0], -nums[1], name)


def _anthropic(spec) -> list[str]:
    import os

    import anthropic

    key = os.environ.get(spec.api_key_env or "ANTHROPIC_API_KEY")
    if not key and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        raise RuntimeError(f"缺少 {spec.api_key_env}")
    kwargs = {"api_key": key} if key else {}
    if spec.base_url:
        kwargs["base_url"] = spec.base_url
    client = anthropic.Anthropic(max_retries=1, timeout=20, **kwargs)
    return [m.id for m in client.models.list(limit=100).data]


def _openai_compat(spec) -> list[str]:
    import os

    if not spec.base_url:
        raise RuntimeError("没有配置 base_url")
    key = os.environ.get(spec.api_key_env or "") if spec.api_key_env else None
    if spec.api_key_env and not key:
        raise RuntimeError(f"缺少 {spec.api_key_env}")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    r = httpx.get(spec.base_url.rstrip("/") + "/models", headers=headers, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:120]}")
    ids = [str(m.get("id", "")).removeprefix("models/") for m in r.json().get("data", [])]
    ids = [i for i in ids if i and not _NOT_CHAT.search(i)]
    if "googleapis.com" in spec.base_url:
        ids.sort(key=_version_key)
    return ids


def list_models(cfg: AppConfig, provider: str, refresh: bool = False) -> dict:
    spec = cfg.llm.providers.get(provider)
    if spec is None:
        return {"provider": provider, "models": [], "error": f"没有 {provider} 这个模型服务"}
    cache_key = f"{provider}|{spec.base_url}|{spec.api_key_env}"
    with _LOCK:
        hit = _CACHE.get(cache_key)
        if hit and not refresh and time.time() - hit[0] < CACHE_SECONDS:
            return {"provider": provider, "models": hit[1], "error": hit[2], "cached": True}
    error = ""
    try:
        if spec.type == "anthropic":
            models = _anthropic(spec)
        elif spec.type == "openai_compat":
            models = _openai_compat(spec)
        elif spec.type == "fake":
            models = ["fake"]
        else:
            models, error = [], f"不支持列出 {spec.type} 的模型"
    except Exception as e:  # noqa: BLE001 - 列不出来就让用户保留当前值
        models, error = [], str(e)[:200]
    with _LOCK:
        if not error:
            _CACHE[cache_key] = (time.time(), models, "")
    return {"provider": provider, "models": models, "error": error}


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()
