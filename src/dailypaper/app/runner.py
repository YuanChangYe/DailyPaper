"""在后台子进程里运行 ``dailypaper run`` / ``dailypaper read``，供网页触发并轮询状态。"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

_ARXIV_RE = re.compile(r"^(?:arXiv:)?(\d{4}\.\d{4,5})(v\d+)?$", re.I)
_ZOTERO_RE = re.compile(r"^(?:zotero:)?([A-Z0-9]{8})$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RunManager:
    def __init__(self, root: Path, log_lines: int = 200) -> None:
        self.root = root
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self.pipeline = ""
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.returncode: int | None = None
        self.log: deque[str] = deque(maxlen=log_lines)

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, pipeline: str, ids: list[str] | None = None, date: str | None = None, extra: list[str] | None = None) -> str:
        if pipeline not in ("daily", "read", "backfill"):
            raise ValueError(f"未知流水线 {pipeline}")
        args = [sys.executable, "-m", "dailypaper.cli", "--root", str(self.root)]
        if pipeline == "read":
            clean = []
            for raw in ids or []:
                m = _ARXIV_RE.match(raw.strip()) or _ZOTERO_RE.match(raw.strip())
                if not m:
                    raise ValueError(f"无法识别的论文 ID：{raw!r}（需要 arXiv ID 如 2610.35877，或 Zotero 条目 key）")
                clean.append(m.group(1))
            if not clean:
                raise ValueError("请至少给一个论文 ID")
            args += ["read", *clean]
        else:
            args += ["run", "--pipeline", pipeline]
        if date:
            if not _DATE_RE.match(date):
                raise ValueError(f"日期格式应为 YYYY-MM-DD：{date!r}")
            args += ["--date", date]
        args += list(extra or [])

        with self._lock:
            if self.running:
                raise RuntimeError("已有任务在运行")
            self.pipeline, self.started_at, self.finished_at, self.returncode = pipeline, _now(), None, None
            self.log.clear()
            self.log.append("$ " + " ".join(args[2:]))
            self._proc = subprocess.Popen(
                args,
                cwd=self.root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"},
            )
        threading.Thread(target=self._pump, args=(self._proc,), daemon=True).start()
        return self.started_at

    def _pump(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            if "httpx" in line and "HTTP Request" in line:
                continue  # 太啰嗦
            self.log.append(line)
        proc.wait()
        self.returncode = proc.returncode
        self.finished_at = _now()
        self.log.append(f"==== 结束，退出码 {proc.returncode}")

    def status(self) -> dict:
        return {
            "running": self.running,
            "pipeline": self.pipeline,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "ok": None if self.returncode is None else self.returncode == 0,
            "returncode": self.returncode,
            "log_tail": list(self.log),
        }

    def wait(self, timeout: float | None = None) -> None:
        if self._proc is not None:
            self._proc.wait(timeout)
