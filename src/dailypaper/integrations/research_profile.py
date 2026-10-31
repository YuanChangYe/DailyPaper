"""课题档案（config/profile.md）：研究者现在在做什么、用什么方法、卡在哪里。

精读、问答、检索重排都会把它放进提示词，回答才能具体到"对你的课题有什么用"。
文件默认不提交（.gitignore），属于个人数据；GitHub Actions 云端运行时没有它，提示词里就不出现这一节。
"""

from __future__ import annotations

from pathlib import Path

from ..core.config import AppConfig

MAX_CHARS = 4000


def profile_path(cfg: AppConfig) -> Path:
    return cfg.path("config") / "profile.md"


def load_profile_text(cfg: AppConfig) -> str:
    p = profile_path(cfg)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8").strip()[:MAX_CHARS]


def save_profile_text(cfg: AppConfig, text: str) -> None:
    text = (text or "").strip()
    if len(text) > MAX_CHARS:
        raise ValueError(f"课题档案太长（{len(text)} 字），请控制在 {MAX_CHARS} 字以内")
    p = profile_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text + "\n" if text else "", encoding="utf-8", newline="\n")
