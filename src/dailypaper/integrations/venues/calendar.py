"""会议日程：顶会页的「即将召开」。

数据来自 ccfddl/ccf-deadlines（GitHub 上社区维护的 CCF 会议截稿与会期，YAML），每 7 天更新一次缓存；
venues.yaml 里手写的 ``next: {date, place, deadline}`` 优先。拿不到时这一项不显示，不影响其他功能。
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime

import httpx
import yaml

from ...core.config import AppConfig, VenueSpec
from ...core.io import read_json, write_json

log = logging.getLogger("dailypaper.venues")
RAW = "https://raw.githubusercontent.com/ccfddl/ccf-deadlines/main/conference/{path}"
CACHE_DAYS = 7
_ALIAS = {"neurips": "nips"}
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _start_date(text: str, year: int) -> date | None:
    """"February 25 - March 4, 2025" / "Dec 2-7, 2026" / "2026-12-02" → 开始日期。"""
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text or "")
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    m = re.search(r"([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})", text or "")
    y = re.search(r"(\d{4})", text or "")
    if m and m.group(1).lower() in _MONTHS:
        try:
            return date(int(y.group(1)) if y else year, _MONTHS[m.group(1).lower()], int(m.group(2)))
        except ValueError:
            return None
    return None


def _deadline(conf: dict) -> date | None:
    today = date.today()
    out = []
    for t in conf.get("timeline") or []:
        for k in ("deadline", "abstract_deadline"):
            v = str(t.get(k) or "")
            if re.match(r"\d{4}-\d{2}-\d{2}", v):
                d = datetime.strptime(v[:10], "%Y-%m-%d").date()
                if d >= today:
                    out.append(d)
    return min(out) if out else None


def _from_ccfddl(data: list | dict) -> dict | None:
    item = (data[0] if isinstance(data, list) and data else data) or {}
    today = date.today()
    best = None
    for conf in item.get("confs") or []:
        start = _start_date(str(conf.get("date") or ""), int(conf.get("year") or today.year))
        dl = _deadline(conf)
        if (start and start >= today) or dl:
            cand = {"year": conf.get("year"), "date": start.isoformat() if start else "", "date_text": conf.get("date") or "",
                    "place": conf.get("place") or "", "deadline": dl.isoformat() if dl else "", "link": conf.get("link") or ""}
            key = start or dl
            if best is None or key < best[0]:
                best = (key, cand)
    return best[1] if best else None


def calendar(cfg: AppConfig, refresh: bool = False) -> list[dict]:
    path = cfg.cache_dir / "venues_calendar.json"
    cache = read_json(path, None) or {}
    fresh = not refresh and time.time() - cache.get("_ts", 0) < CACHE_DAYS * 86400
    out, changed = [], False
    with httpx.Client(timeout=20, headers={"User-Agent": "DailyPaper/0.1"}, follow_redirects=True) as client:
        for v in cfg.venues.venues:
            manual = v.param("next")
            if isinstance(manual, dict) and manual.get("date"):
                out.append(_row(v, {"date": str(manual["date"]), "date_text": str(manual["date"]), "place": manual.get("place", ""),
                                    "deadline": str(manual.get("deadline") or ""), "link": manual.get("link", ""), "manual": True}))
                continue
            rel = v.param("ccfddl") or f"AI/{_ALIAS.get(v.id, v.id)}.yml"
            if fresh and rel in cache:
                info = cache[rel]
            else:
                try:
                    r = client.get(RAW.format(path=rel))
                    info = _from_ccfddl(yaml.safe_load(r.text)) if r.status_code == 200 else None
                except Exception as e:  # noqa: BLE001
                    log.info("会议日程 %s 读取失败：%s", rel, e)
                    info = cache.get(rel)
                cache[rel] = info
                changed = True
            if info:
                out.append(_row(v, info))
    if changed:
        cache["_ts"] = time.time()
        write_json(path, cache)
    out.sort(key=lambda x: x["date"] or x["deadline"] or "9999")
    return out


def _row(v: VenueSpec, info: dict) -> dict:
    return {"id": v.id, "name": v.name, "enabled": v.enabled, "ccf": v.ccf, "core": v.core, **info}
