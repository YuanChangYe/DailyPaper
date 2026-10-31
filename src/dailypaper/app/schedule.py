"""定时运行开关：Windows 任务计划程序里的「DailyPaper」任务 + 本地的「暂停到某天」。

三种状态：
- on     任务启用，每天按时运行
- pause  任务保持启用，但到 paused_until 之前每天触发后直接退出（不调用模型、不花钱），到期自动恢复
- off    任务禁用，不再触发

「暂停到某天」记在 data/schedule.json；任务计划程序调用的是 ``dailypaper run --scheduled``，它先检查这个文件。
只操作当前用户注册的任务，不需要管理员权限。非 Windows 系统只报告不支持（请用 cron）。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

from ..core.config import AppConfig
from ..core.io import read_json, write_json

TASK_NAME = "DailyPaper"
_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_CACHE: dict = {"at": 0.0, "info": None}
CACHE_SECONDS = 20


def supported() -> bool:
    return sys.platform == "win32"


def _ps(script: str, timeout: float = 30) -> subprocess.CompletedProcess:
    """运行一段 PowerShell。输出只用 ASCII（日期、数字、状态名），避免控制台编码问题。"""
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


# ---------------------------------------------------------------- 暂停到某天（data/schedule.json）


def _state_path(cfg: AppConfig) -> Path:
    return cfg.data_dir / "schedule.json"


def paused_until(cfg: AppConfig) -> date | None:
    # GitHub Actions 里用仓库变量（.github/workflows/daily.yml 传入环境变量），本机用 data/schedule.json
    raw = os.environ.get("DAILYPAPER_PAUSED_UNTIL") or (read_json(_state_path(cfg), {}) or {}).get("paused_until")
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def is_paused(cfg: AppConfig, today: date | None = None) -> bool:
    """paused_until 当天恢复运行：今天 < paused_until 才算暂停。"""
    until = paused_until(cfg)
    return bool(until and (today or date.today()) < until)


def set_paused_until(cfg: AppConfig, until: date | None) -> None:
    write_json(_state_path(cfg), {"paused_until": until.isoformat() if until else None,
                                  "updated_at": datetime.now().isoformat(timespec="seconds")})


# ---------------------------------------------------------------- 任务计划程序


def task_info(refresh: bool = False) -> dict:
    """{registered, enabled, time, next_run, last_run, last_result}；不支持或查询失败时 registered=False。"""
    if not supported():
        return {"registered": False}
    if not refresh and _CACHE["info"] is not None and time.time() - _CACHE["at"] < CACHE_SECONDS:
        return dict(_CACHE["info"])
    script = (
        f"$t = Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue; "
        "if (-not $t) { '{\"registered\": false}'; exit 0 }; "
        "$i = $t | Get-ScheduledTaskInfo; "
        "$f = { param($d) if ($d -and $d.Year -gt 1999) { $d.ToString('s') } else { $null } }; "
        "[pscustomobject]@{ registered = $true; state = $t.State.ToString(); "
        "start = ($t.Triggers | Select-Object -First 1).StartBoundary; "
        "next_run = (& $f $i.NextRunTime); last_run = (& $f $i.LastRunTime); last_result = $i.LastTaskResult } | ConvertTo-Json -Compress"
    )
    try:
        r = _ps(script)
        data = json.loads((r.stdout or "").strip() or "{}")
    except (subprocess.SubprocessError, OSError, ValueError):
        return {"registered": False, "error": "查询任务计划程序失败"}
    if not data.get("registered"):
        info = {"registered": False}
    else:
        m = re.search(r"T(\d{2}):(\d{2})", str(data.get("start") or ""))
        info = {
            "registered": True,
            "enabled": data.get("state") != "Disabled",
            "state": data.get("state"),
            "time": f"{m.group(1)}:{m.group(2)}" if m else "",
            "next_run": data.get("next_run"),
            "last_run": data.get("last_run"),
            "last_result": data.get("last_result"),
        }
    _CACHE.update(at=time.time(), info=info)
    return dict(info)


def _invalidate() -> None:
    _CACHE.update(at=0.0, info=None)


def set_enabled(enabled: bool) -> None:
    verb = "Enable" if enabled else "Disable"
    r = _ps(f"{verb}-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction Stop | Out-Null")
    _invalidate()
    if r.returncode != 0:
        raise RuntimeError(f"{'启用' if enabled else '禁用'}定时任务失败：{(r.stderr or r.stdout).strip()[:200]}")


def set_time(hhmm: str) -> None:
    if not _TIME_RE.match(hhmm):
        raise ValueError(f"时间格式应为 HH:MM：{hhmm!r}")
    r = _ps(f"$tr = New-ScheduledTaskTrigger -Daily -At '{hhmm}'; Set-ScheduledTask -TaskName '{TASK_NAME}' -Trigger $tr -ErrorAction Stop | Out-Null")
    _invalidate()
    if r.returncode != 0:
        raise RuntimeError(f"修改运行时间失败：{(r.stderr or r.stdout).strip()[:200]}")


def register(cfg: AppConfig, hhmm: str = "14:00") -> None:
    if not _TIME_RE.match(hhmm):
        raise ValueError(f"时间格式应为 HH:MM：{hhmm!r}")
    script = cfg.root / "scripts" / "install_task.ps1"
    if not script.exists():
        raise RuntimeError(f"找不到 {script}")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Time", hhmm],
                       capture_output=True, text=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    _invalidate()
    if r.returncode != 0:
        raise RuntimeError(f"注册定时任务失败：{(r.stderr or r.stdout).strip()[:200]}")


# ---------------------------------------------------------------- 组合视图与操作


def view(cfg: AppConfig, refresh: bool = False) -> dict:
    info = task_info(refresh) if supported() else {"registered": False}
    until = paused_until(cfg)
    paused = bool(until and date.today() < until)
    if not info.get("registered"):
        mode = "unregistered"
    elif not info.get("enabled"):
        mode = "off"
    elif paused:
        mode = "pause"
    else:
        mode = "on"
    return {"supported": supported(), "task_name": TASK_NAME, "mode": mode, **info,
            "paused_until": until.isoformat() if until else None}


def apply(cfg: AppConfig, mode: str, until: str | None = None, hhmm: str | None = None) -> list[str]:
    if not supported():
        raise RuntimeError("只有 Windows 支持在网页里开关定时运行；其他系统请用 cron")
    info = task_info(refresh=True)
    if not info.get("registered"):
        raise RuntimeError("还没有注册定时任务，先点「注册定时任务」")
    msgs: list[str] = []
    if hhmm and hhmm != info.get("time"):
        set_time(hhmm)
        msgs.append(f"运行时间改为每天 {hhmm}")
    if mode == "on":
        set_paused_until(cfg, None)
        set_enabled(True)
        msgs.append("定时运行已开启")
    elif mode == "pause":
        try:
            d = date.fromisoformat(str(until))
        except ValueError as e:
            raise ValueError("请选择暂停到哪一天") from e
        if d <= date.today():
            raise ValueError("暂停日期需要在今天之后")
        set_paused_until(cfg, d)
        set_enabled(True)  # 保持启用，到期才能自动恢复
        msgs.append(f"已暂停到 {d.isoformat()}，那天起自动恢复（期间不调用模型、不花钱）")
    elif mode == "off":
        set_paused_until(cfg, None)
        set_enabled(False)
        msgs.append("定时运行已关闭")
    else:
        raise ValueError(f"未知的模式 {mode!r}")
    return msgs
