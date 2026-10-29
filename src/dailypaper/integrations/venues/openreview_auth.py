"""OpenReview 登录令牌（进程内缓存一次）。API 和 PDF 下载都需要它：2026 年起匿名请求会被人机验证拦截。

凭据来自环境变量 OPENREVIEW_USERNAME / OPENREVIEW_PASSWORD（.env）。没有凭据或登录失败时返回 None，调用方按匿名处理。
"""

from __future__ import annotations

import logging
import os
import threading

import httpx

LOGIN_URL = "https://api2.openreview.net/login"
_LOCK = threading.Lock()
_CACHE: dict[str, str | None] = {}
log = logging.getLogger("dailypaper.venues.openreview")


def credentials() -> tuple[str, str] | None:
    user = (os.environ.get("OPENREVIEW_USERNAME") or "").strip().strip("\"'")
    pwd = (os.environ.get("OPENREVIEW_PASSWORD") or "").strip().strip("\"'")
    return (user, pwd) if user and pwd else None


def token(client: httpx.Client | None = None) -> str | None:
    """返回 Bearer token；同一进程只登录一次（OpenReview 对频繁登录会 429）。"""
    creds = credentials()
    if not creds:
        return None
    user, pwd = creds
    with _LOCK:
        if user in _CACHE:
            return _CACHE[user]
        try:
            if client is not None:
                r = client.post(LOGIN_URL, json={"id": user, "password": pwd})
            else:
                r = httpx.post(LOGIN_URL, json={"id": user, "password": pwd}, headers={"User-Agent": "DailyPaper/0.1"}, timeout=30)
        except httpx.HTTPError as e:
            log.warning("OpenReview 登录请求失败：%s", e)
            return None  # 网络问题不缓存，下次再试
        if r.status_code != 200:
            hint = "账号或密码不对；用 Google 登录注册的账号没有密码，需要在 OpenReview 设置里设一个" if r.status_code == 400 else "登录请求被限流，等几分钟再试" if r.status_code == 429 else f"HTTP {r.status_code}"
            log.warning("OpenReview 登录失败：%s", hint)
            _CACHE[user] = None
            return None
        _CACHE[user] = r.json().get("token")
        return _CACHE[user]


def login_error() -> str | None:
    """登录失败时的说明（供适配器报错用）。"""
    creds = credentials()
    if not creds:
        return "没有配置 OPENREVIEW_USERNAME / OPENREVIEW_PASSWORD"
    return "OpenReview 登录失败：账号或密码不对，或登录被限流（等几分钟再试）；用 Google 注册的账号需要先在 OpenReview 设置里设密码"


def auth_headers(url: str) -> dict[str, str]:
    """给 openreview.net 的请求附上 Authorization；其他域名返回空。"""
    if "openreview.net" not in url:
        return {}
    t = token()
    return {"Authorization": f"Bearer {t}"} if t else {}


def reset() -> None:
    with _LOCK:
        _CACHE.clear()
