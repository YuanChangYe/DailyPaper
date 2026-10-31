"""每日运行完成后的提醒。

- Windows 通知（本机运行时默认开；GitHub Actions 里自动跳过）；
- 手机推送（可选，默认关）：在 .env 里填其中一个
    NTFY_URL=https://ntfy.sh/<你自己起的、别人猜不到的主题名>      手机装 ntfy App 订阅同一个主题
    SERVERCHAN_SENDKEY=SCT...                                      Server 酱（微信）
推送内容只有篇数和标题，不含笔记正文。
"""

from __future__ import annotations

import os
import subprocess
from xml.sax.saxutils import escape

import httpx
from pydantic import BaseModel

from ...core.interfaces import Publisher
from ...core.models import Candidate
from ...core.registry import register

_TOAST_PS = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml("<toast><visual><binding template='ToastGeneric'><text>$env:DP_TITLE</text><text>$env:DP_BODY</text></binding></visual></toast>")
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($xml))
"""


def windows_toast(title: str, body: str) -> None:
    env = {**os.environ, "DP_TITLE": escape(title), "DP_BODY": escape(body)}  # 经环境变量传，避免拼进脚本
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _TOAST_PS], env=env, timeout=20,
                   capture_output=True, check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def push(title: str, body: str, url: str = "") -> list[str]:
    """按 .env 里配置的渠道推送；返回成功的渠道名。"""
    sent = []
    ntfy = os.environ.get("NTFY_URL")
    if ntfy:
        httpx.post(ntfy, content=body.encode("utf-8"),
                   headers={"Title": title.encode("utf-8"), **({"Click": url} if url else {})}, timeout=15).raise_for_status()
        sent.append("ntfy")
    key = os.environ.get("SERVERCHAN_SENDKEY")
    if key:
        httpx.post(f"https://sctapi.ftqq.com/{key}.send", data={"title": title, "desp": body}, timeout=15).raise_for_status()
        sent.append("Server 酱")
    return sent


def channels() -> dict:
    return {"toast": os.name == "nt", "ntfy": bool(os.environ.get("NTFY_URL")), "serverchan": bool(os.environ.get("SERVERCHAN_SENDKEY"))}


@register("publisher", "notify")
class NotifyPublisher(Publisher):
    class Params(BaseModel):
        toast: bool = True
        push: bool = True
        min_papers: int = 0  # 少于这个篇数不提醒（0 = 没有新论文也提醒一句）

    def run(self, items: list[Candidate]) -> list[Candidate]:
        done = [c for c in self.selected(items) if c.note]
        if len(done) < self.p.min_papers:
            return items
        title = f"DailyPaper：今天精读了 {len(done)} 篇" if done else "DailyPaper：今天没有新的精读"
        body = "\n".join(f"· {c.paper.title[:70]}" for c in done[:6]) or "arXiv 当天没有合适的论文，顶会池也没有补足。"
        if self.p.toast and os.name == "nt" and os.environ.get("GITHUB_ACTIONS") != "true":
            try:
                windows_toast(title, body)
            except Exception as e:  # noqa: BLE001
                self.log.info("Windows 通知失败：%s", e)
        if self.p.push:
            try:
                sent = push(title, body, self.config.settings.site.base_url or "")
                if sent:
                    self.log.info("已推送：%s", "、".join(sent))
            except Exception as e:  # noqa: BLE001
                self.log.warning("手机推送失败：%s", e)
        return items
