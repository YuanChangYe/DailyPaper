"""VenueAdapter 接口与公共 HTTP 工具。"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import httpx

from ....core.config import AppConfig, VenueSpec
from ....core.models import Paper

USER_AGENT = "DailyPaper/0.1 (personal research digest; https://github.com/YuanChangYe/DailyPaper)"
log = logging.getLogger("dailypaper.venues")


class VenueUnavailable(RuntimeError):
    """数据源暂时拿不到（限流、需要登录、网络问题）；同步时记录并跳过，不算程序错误。"""


class VenueAdapter(ABC):
    name = ""

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.timeout = cfg.venues.timeout

    partial = False  # 本次 list_papers 是否有一部分没拿到（调用方据此不记录同步完成，下次再试）

    def client(self, **headers: str) -> httpx.Client:
        return httpx.Client(headers={"User-Agent": USER_AGENT, **headers}, timeout=self.timeout, follow_redirects=True)

    @staticmethod
    def get_retry(client: httpx.Client, url: str, tries: int = 3, **kw) -> httpx.Response:
        """网络抖动（断连、超时、5xx、429）时重试几次，间隔递增；最后一次仍失败就抛出。"""
        import time

        last: Exception | None = None
        for i in range(tries):
            try:
                r = client.get(url, **kw)
                if r.status_code in (429, 500, 502, 503, 504) and i < tries - 1:
                    time.sleep(2 * (i + 1))
                    continue
                return r
            except httpx.HTTPError as e:
                last = e
                time.sleep(2 * (i + 1))
        raise VenueUnavailable(f"{url}: {last}")

    @abstractmethod
    def list_papers(self, spec: VenueSpec, year: int) -> list[Paper]:
        """某个会议某一年的全部录用论文（标题、摘要、作者、链接）。拿不到时抛 VenueUnavailable。"""
