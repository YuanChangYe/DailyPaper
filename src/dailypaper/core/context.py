"""一次运行的上下文：配置、日期、运行目录、命令行输入，以及按需创建的共享服务。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, TypeVar

from .config import AppConfig

T = TypeVar("T")


@dataclass
class RunContext:
    config: AppConfig
    run_date: date
    pipeline: str = "daily"
    dry_run: bool = False  # 为 True 时跳过所有 publisher
    provider_override: str | None = None  # 例如 "fake"：所有 LLM 角色都用它
    inputs: dict[str, Any] = field(default_factory=dict)  # 命令行传入的额外参数（如 read 的论文 ID）
    _services: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def run_dir(self) -> Path:
        d = self.config.cache_dir / "runs" / f"{self.run_date.isoformat()}_{self.pipeline}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def service(self, key: str, factory: Callable[[], T]) -> T:
        """进程内单例：同一次运行里共享 LLM 客户端、Zotero 连接等。"""
        if key not in self._services:
            self._services[key] = factory()
        return self._services[key]

    def logger(self, name: str) -> logging.Logger:
        return logging.getLogger(f"dailypaper.{name}")
