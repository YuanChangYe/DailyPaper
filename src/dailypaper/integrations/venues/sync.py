"""同步编排：对每个启用的会议 × 年份，调用适配器拉取并写入论文池，记录 sync_log。

- 年份：当年往前 years_back 年。
- 不 force 时，距上次同步不到 auto_sync_days 的 venue-year 跳过。
- 适配器抛 VenueUnavailable 时，若会议配置了 ``fallback``（另一个适配器名）就换它再试；仍失败只记录，不中断其他项。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from ...core import registry
from ...core.config import AppConfig, VenueSpec
from .adapters.base import VenueUnavailable
from .pool import VenuePool

log = logging.getLogger("dailypaper.venues.sync")


def target_years(cfg: AppConfig, today: date | None = None) -> list[int]:
    y = (today or date.today()).year
    return [y - i for i in range(cfg.venues.years_back + 1)]


def _stale(pool: VenuePool, spec: VenueSpec, year: int, days: int) -> bool:
    last = pool.last_sync(spec.id, year)
    if not last:
        return True
    try:
        t = datetime.fromisoformat(last)
    except ValueError:
        return True
    return (datetime.now(timezone.utc) - t).days >= days


def _adapter(cfg: AppConfig, name: str):
    try:
        cls = registry.get("venue_adapter", name)
    except KeyError as e:
        raise VenueUnavailable(f"没有 {name} 这种数据源适配器") from e
    return cls(cfg)


def sync_one(cfg: AppConfig, pool: VenuePool, spec: VenueSpec, year: int) -> dict:
    tried = []
    for name in [spec.adapter, *([spec.param("fallback")] if spec.param("fallback") else [])]:
        tried.append(name)
        adapter = _adapter(cfg, name)
        try:
            papers = adapter.list_papers(spec, year)
        except VenueUnavailable as e:
            log.warning("%s %d 用 %s 失败：%s", spec.name, year, name, e)
            last_err = str(e)
            continue
        new = pool.upsert(papers, spec.id, year, adapter=name)
        partial = bool(getattr(adapter, "partial", False))
        if not partial:  # 有卷没拿到：数据先入池，但不记录"已同步"，下次补
            pool.log_sync(spec.id, year, len(papers), adapter=name)
        return {"venue": spec.id, "year": year, "adapter": name, "fetched": len(papers), "new": new, "ok": True, "partial": partial}
    return {"venue": spec.id, "year": year, "adapter": "/".join(tried), "fetched": 0, "new": 0, "ok": False, "error": last_err}


def sync_venues(cfg: AppConfig, venues: list[str] | None = None, years: list[int] | None = None, force: bool = False,
                pool: VenuePool | None = None, progress=None) -> list[dict]:
    pool = pool or VenuePool(cfg)
    specs = [v for v in cfg.venues.enabled() if not venues or v.id in venues]
    years = years or target_years(cfg)
    results = []
    for spec in specs:
        for year in years:
            if not force and not _stale(pool, spec, year, cfg.venues.auto_sync_days):
                results.append({"venue": spec.id, "year": year, "skipped": True, "ok": True})
                continue
            if progress:
                progress(f"{spec.name} {year} …")
            r = sync_one(cfg, pool, spec, year)
            results.append(r)
            if progress:
                tail = "（部分卷未取到，下次补）" if r.get("partial") else ""
                progress(f"{spec.name} {year}: " + (f"{r['fetched']} 篇，新增 {r['new']}{tail}" if r["ok"] else f"失败 {r.get('error', '')}"))
    return results


def needs_sync(cfg: AppConfig, pool: VenuePool | None = None) -> bool:
    """daily 运行时用：有任何启用的 venue-year 超过 auto_sync_days 没同步就返回 True。"""
    pool = pool or VenuePool(cfg)
    return any(_stale(pool, v, y, cfg.venues.auto_sync_days) for v in cfg.venues.enabled() for y in target_years(cfg))
