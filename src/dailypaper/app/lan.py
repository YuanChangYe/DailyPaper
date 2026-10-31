"""局域网模式：让同一 Wi-Fi 下的手机也能用本地助手（默认关闭）。

- 开启后本地助手监听 0.0.0.0（需要重启生效）；本机（127.0.0.1）照常访问。
- 从其他设备访问时：必须带配对令牌（第一次用 ``?lan_token=…`` 打开，之后存在 Cookie 里），
  且来源 IP 必须是局域网地址（192.168.x.x、10.x.x.x、172.16–31.x.x）。写操作仍要求 X-DailyPaper 请求头。
- 设置保存在 config/lan.json（不提交）；可以随时关闭或更换令牌，旧令牌立即失效。
"""

from __future__ import annotations

import ipaddress
import secrets
import socket
from pathlib import Path

from ..core.config import AppConfig
from ..core.io import read_json, write_json

COOKIE = "dp_lan"


def lan_path(cfg: AppConfig) -> Path:
    return cfg.path("config") / "lan.json"


def load_lan(cfg: AppConfig) -> dict:
    data = read_json(lan_path(cfg), None) or {}
    return {"enabled": bool(data.get("enabled")), "token": str(data.get("token") or "")}


def save_lan(cfg: AppConfig, enabled: bool, rotate: bool = False) -> dict:
    cur = load_lan(cfg)
    token = cur["token"] if cur["token"] and not rotate else secrets.token_urlsafe(18)
    write_json(lan_path(cfg), {"enabled": bool(enabled), "token": token})
    return load_lan(cfg)


def lan_ips() -> list[str]:
    ips: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:  # 默认路由那块网卡的地址（不会真的发包）
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.168.0.1", 9))
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    return sorted(ip for ip in ips if is_private(ip) and not ip.startswith("127."))


def is_ip(ip: str | None) -> bool:
    try:
        ipaddress.ip_address(ip or "")
        return True
    except ValueError:
        return False


def is_private(ip: str | None) -> bool:
    """局域网地址（不含本机回环和链路本地）。"""
    try:
        a = ipaddress.ip_address(ip or "")
    except ValueError:
        return False
    return a.is_private and not a.is_loopback and not a.is_link_local


def is_loopback(ip: str | None) -> bool:
    try:
        return bool(ip) and ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False


def token_ok(cfg: AppConfig, token: str | None) -> bool:
    lan = load_lan(cfg)
    return bool(lan["enabled"] and lan["token"] and token and secrets.compare_digest(token, lan["token"]))
