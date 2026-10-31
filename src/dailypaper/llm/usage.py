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


# 用途分组：网页「运行与费用」按这几类显示花费
FEATURES = {"screen": "筛选", "read": "精读", "ask": "问答", "search": "检索", "report": "综述", "other": "其他"}


def feature_of(role: str, tag: str) -> str:
    for f in ("search", "report", "ask"):
        if tag.startswith(f):
            return f
    return role if role in ("screen", "read") else "other"


def record(cfg: AppConfig, model: str, input_tokens: int, output_tokens: int, day: date | None = None,
           feature: str = "other") -> None:
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
        ff = m.setdefault("features", {}).setdefault(feature, {"calls": 0, "usd": 0.0})
        ff["calls"] += 1
        ff["usd"] = round(ff["usd"] + (cost or 0.0), 4)
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


def history(cfg: AppConfig, months: int = 6) -> dict:
    """最近几个月的花费（云端 + 本地合并），以及本月按模型的明细。网页「运行与费用」用。"""
    from datetime import timedelta

    merged: dict[str, dict] = {}
    for name in ("usage.json", "usage.local.json"):
        for month, m in (read_json(cfg.data_dir / name, {}) or {}).items():
            t = merged.setdefault(month, {"usd": 0.0, "calls": 0, "models": {}, "features": {}})
            t["usd"] += m.get("usd", 0.0)
            t["calls"] += m.get("calls", 0)
            for f, ff in m.get("features", {}).items():
                x = t["features"].setdefault(f, {"calls": 0, "usd": 0.0})
                x["calls"] += ff.get("calls", 0)
                x["usd"] += ff.get("usd", 0.0)
            for model, mm in m.get("models", {}).items():
                x = t["models"].setdefault(model, {"in": 0, "out": 0, "usd": 0.0})
                x["in"] += mm.get("in", 0)
                x["out"] += mm.get("out", 0)
                x["usd"] += mm.get("usd", 0.0)
    today = date.today().replace(day=1)
    keys = []
    d = today
    for _ in range(months):
        keys.append(d.strftime("%Y-%m"))
        d = (d - timedelta(days=1)).replace(day=1)
    keys.reverse()
    cur = merged.get(keys[-1], {"usd": 0.0, "calls": 0, "models": {}})
    return {
        "month": keys[-1],
        "usd": round(cur["usd"], 4),
        "calls": cur["calls"],
        "budget": cfg.llm.monthly_budget_usd,
        "warn_ratio": cfg.llm.budget_warn_ratio,
        "models": {k: {**v, "usd": round(v["usd"], 4)} for k, v in sorted(cur["models"].items(), key=lambda kv: -kv[1]["usd"])},
        # 按用途（筛选 / 精读 / 问答 / 检索 / 综述）；这个功能上线前的花费没有分组
        "features": [{"id": f, "label": FEATURES.get(f, f), "calls": v["calls"], "usd": round(v["usd"], 4)}
                     for f, v in sorted(cur.get("features", {}).items(), key=lambda kv: -kv[1]["usd"])],
        "months": [{"month": k, "usd": round(merged.get(k, {}).get("usd", 0.0), 4), "calls": merged.get(k, {}).get("calls", 0)} for k in keys],
    }
