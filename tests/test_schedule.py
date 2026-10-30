"""定时运行开关：暂停到某天、三种模式、run --scheduled 跳过、接口。PowerShell 调用全部替换成假的，不动真实任务。"""

from __future__ import annotations

import json
import subprocess
from datetime import date, timedelta

import pytest

from dailypaper.app import schedule as sch


class FakeTask:
    """模拟任务计划程序：记录 Enable / Disable / Set 调用。"""

    def __init__(self, registered=True, state="Ready", start="2026-10-28T14:00:00"):
        self.registered, self.state, self.start, self.calls = registered, state, start, []

    def __call__(self, script, timeout=30):
        self.calls.append(script)
        out, code = "", 0
        if script.startswith("$t = Get-ScheduledTask"):
            out = json.dumps({"registered": False}) if not self.registered else json.dumps(
                {"registered": True, "state": self.state, "start": self.start, "next_run": "2026-10-31T14:00:00", "last_run": "2026-10-29T14:00:01", "last_result": 0})
        elif script.startswith("Enable-ScheduledTask"):
            self.state = "Ready"
        elif script.startswith("Disable-ScheduledTask"):
            self.state = "Disabled"
        elif "New-ScheduledTaskTrigger" in script:
            hhmm = script.split("-At '")[1][:5]
            self.start = f"2026-10-28T{hhmm}:00"
        return subprocess.CompletedProcess([], code, stdout=out, stderr="")


@pytest.fixture
def task(monkeypatch):
    t = FakeTask()
    monkeypatch.setattr(sch, "_ps", t)
    monkeypatch.setattr(sch, "supported", lambda: True)
    sch._invalidate()
    yield t
    sch._invalidate()


def test_modes_and_pause(cfg, task):
    v = sch.view(cfg, refresh=True)
    assert v["mode"] == "on" and v["time"] == "14:00" and v["last_result"] == 0
    sch.apply(cfg, "off")
    assert sch.view(cfg)["mode"] == "off" and task.state == "Disabled"
    until = (date.today() + timedelta(days=10)).isoformat()
    msgs = sch.apply(cfg, "pause", until=until, hhmm="09:30")
    assert task.state == "Ready" and any("09:30" in m for m in msgs)  # 暂停时任务保持启用，到期才能自动恢复
    v = sch.view(cfg)
    assert v["mode"] == "pause" and v["paused_until"] == until and v["time"] == "09:30"
    assert sch.is_paused(cfg) and not sch.is_paused(cfg, date.today() + timedelta(days=10))  # 那天起恢复
    with pytest.raises(ValueError):
        sch.apply(cfg, "pause", until=date.today().isoformat())
    with pytest.raises(ValueError):
        sch.apply(cfg, "pause", until="not-a-date")
    with pytest.raises(ValueError):
        sch.set_time("25:00")
    sch.apply(cfg, "on")
    assert sch.view(cfg)["mode"] == "on" and sch.paused_until(cfg) is None


def test_unregistered_and_unsupported(cfg, task, monkeypatch):
    task.registered = False
    sch._invalidate()
    assert sch.view(cfg)["mode"] == "unregistered"
    with pytest.raises(RuntimeError):
        sch.apply(cfg, "on")
    monkeypatch.setattr(sch, "supported", lambda: False)
    v = sch.view(cfg)
    assert not v["supported"] and v["mode"] == "unregistered"


def test_run_scheduled_skips_when_paused(cfg, project, monkeypatch, caplog):
    from dailypaper import cli

    sch.set_paused_until(cfg, date.today() + timedelta(days=3))
    called = []
    monkeypatch.setattr(cli.Pipeline, "from_config", lambda ctx: called.append(1))
    caplog.set_level("INFO")
    assert cli.main(["--root", str(project), "run", "--scheduled", "--provider", "fake"]) == 0
    assert not called and "暂停到" in caplog.text
    sch.set_paused_until(cfg, None)


def test_schedule_api(cfg, task):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from dailypaper.app.server import create_app

    client = TestClient(create_app(cfg), base_url="http://127.0.0.1")
    H = {"X-DailyPaper": "1"}
    assert client.get("/api/schedule").json()["mode"] == "on"
    until = (date.today() + timedelta(days=7)).isoformat()
    r = client.put("/api/schedule", json={"mode": "pause", "until": until, "time": "14:00"}, headers=H).json()
    assert r["mode"] == "pause" and r["paused_until"] == until and r["messages"]
    assert client.put("/api/schedule", json={"mode": "weird"}, headers=H).status_code == 400
    assert client.put("/api/schedule", json={"mode": "off"}).status_code == 403  # 缺请求头
    assert client.put("/api/schedule", json={"mode": "off"}, headers=H).json()["mode"] == "off"
