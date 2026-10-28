"""按月累计 LLM 用量与花费（data/usage.json，随 git 提交，云端和本地都能看到），并做预算提醒。

价格来自 llm.yaml → pricing（美元 / 百万 token）；没有配置价格的模型只记 token，不记金额。
命中本地缓存的调用不计入。
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import date

from ..core.config import AppConfig
from ..core.io import read_json, write_json

log = logging.getLogger("dailypaper.usage")
_LOCK = threading.Lock()


def _path(cfg: AppConfig):
    # 云端和本地各写各的文件，避免 git 冲突；统计时合并
    name = "usage.json" if os.environ.get("GITHUB_ACTIONS") == "true" else "usage.local.json"
    return cfg.data_dir / name


def cost_of(cfg: AppConfig, model: str, input_tokens: int, output_tokens: int) -> float | None:
    price = cfg.llm.pricing.get(model)
    if not price or len(price) < 2:
        return None
    return (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000


def record(cfg: AppConfig, model: str, input_tokens: int, output_tokens: int, day: date | None = None) -> None:
    month = (day or date.today()).strftime("%Y-%m")
    cost = cost_of(cfg, model, input_tokens, output_tokens)
    with _LOCK:
        data = read_json(_path(cfg), {}) or {}
        m = data.setdefault(month, {"usd": 0.0, "calls": 0, "models": {}})
        m["calls"] += 1
        m["usd"] = round(m["usd"] + (cost or 0.0), 4)
        mm = m["models"].setdefault(model, {"in": 0, "out": 0, "usd": 0.0})
        mm["in"] += input_tokens
        mm["out"] += output_tokens
        mm["usd"] = round(mm["usd"] + (cost or 0.0), 4)
        write_json(_path(cfg), data)


def month_summary(cfg: AppConfig, month: str | None = None) -> dict:
    """合并云端与本地两份记录。"""
    month = month or date.today().strftime("%Y-%m")
    total = {"usd": 0.0, "calls": 0}
    for name in ("usage.json", "usage.local.json"):
        m = (read_json(cfg.data_dir / name, {}) or {}).get(month)
        if m:
            total["usd"] += m["usd"]
            total["calls"] += m["calls"]
    return total


def budget_line(cfg: AppConfig) -> str:
    """一行本月花费说明；超过预算阈值时以警告级别记录日志。"""
    s = month_summary(cfg)
    budget = cfg.llm.monthly_budget_usd
    line = f"本月 LLM 花费约 ${s['usd']:.2f}（{s['calls']} 次调用）"
    if budget:
        ratio = s["usd"] / budget
        line += f"，预算 ${budget:.0f} 的 {ratio:.0%}"
        if ratio >= cfg.llm.budget_warn_ratio:
            log.warning("本月 LLM 花费已达预算的 %.0f%%（$%.2f / $%.0f），可在 llm.yaml 调低精读篇数或换模型",
                        ratio * 100, s["usd"], budget)
    return line
