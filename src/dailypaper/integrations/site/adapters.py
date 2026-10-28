"""站点适配器（registry kind = ``site_adapter``）：把内容目录接到具体的网站框架上。

- ``mkdocs``        默认。docs/ 本身就是 MkDocs 站点源，由 GitHub Actions 构建并部署到 Pages，无需额外操作
- ``hugo``          复制到 Hugo 站点的 content 子目录（frontmatter 已兼容）
- ``jekyll``        复制到 Jekyll 站点（如 al-folio），笔记放进 ``_posts`` 风格的目录并补 layout
- ``generic_repo``  先克隆/更新你的网站仓库，按上面任一格式导出到指定目录，再 commit + push

settings.yaml → site.adapter_options 中的参数：
    target_dir: 目标目录（hugo / jekyll；generic_repo 中为仓库内的相对路径）
    repo_url: 网站仓库地址（generic_repo，推荐在 Actions 中用 deploy key 或 token）
    branch: main
    format: hugo | jekyll | plain（generic_repo 导出时使用的格式）
    layout: post（jekyll）
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path

from ...core.config import AppConfig
from ...core.io import join_frontmatter, read_markdown, write_text
from ...core.registry import register

log = logging.getLogger("dailypaper.site")


class SiteAdapter(ABC):
    def __init__(self, cfg: AppConfig, options: dict) -> None:
        self.cfg = cfg
        self.opts = options

    @abstractmethod
    def publish(self) -> str:
        """把内容同步到网站；返回一句结果说明。"""


def export_tree(cfg: AppConfig, target: Path, fmt: str, layout: str = "post") -> int:
    """把 papers / daily / topics 和 api、feed 导出到 target，按格式调整 frontmatter。"""
    src = cfg.content_dir
    n = 0
    for sub in ("papers", "daily", "topics"):
        for f in (src / sub).rglob("*.md") if (src / sub).exists() else []:
            fm, body = read_markdown(f)
            if fmt == "jekyll":
                fm.setdefault("layout", layout)
            if fmt == "hugo":
                fm.setdefault("draft", False)
            dest = target / f.relative_to(src)
            write_text(dest, join_frontmatter(fm, body))
            n += 1
    for extra in ("api", "feed.xml"):
        p = src / extra
        if p.is_dir():
            shutil.copytree(p, target / extra, dirs_exist_ok=True)
        elif p.exists():
            shutil.copy2(p, target / extra)
    return n


@register("site_adapter", "mkdocs")
class MkDocsAdapter(SiteAdapter):
    def publish(self) -> str:
        return "MkDocs：docs/ 即站点源，由 GitHub Actions 构建部署"


@register("site_adapter", "hugo")
class HugoAdapter(SiteAdapter):
    def publish(self) -> str:
        target = self.cfg.path(self.opts["target_dir"])
        return f"Hugo：导出 {export_tree(self.cfg, target, 'hugo')} 篇到 {target}"


@register("site_adapter", "jekyll")
class JekyllAdapter(SiteAdapter):
    def publish(self) -> str:
        target = self.cfg.path(self.opts["target_dir"])
        return f"Jekyll：导出 {export_tree(self.cfg, target, 'jekyll', self.opts.get('layout', 'post'))} 篇到 {target}"


@register("site_adapter", "generic_repo")
class GenericRepoAdapter(SiteAdapter):
    def _git(self, *args: str, cwd: Path) -> str:
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout

    def publish(self) -> str:
        repo_url = self.opts["repo_url"]
        branch = self.opts.get("branch", "main")
        work = self.cfg.cache_dir / "site_repo"
        if (work / ".git").exists():
            self._git("fetch", "origin", branch, cwd=work)
            self._git("reset", "--hard", f"origin/{branch}", cwd=work)
        else:
            work.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "clone", "--depth", "1", "-b", branch, repo_url, str(work)], check=True)
        target = work / self.opts.get("target_dir", "dailypaper")
        n = export_tree(self.cfg, target, self.opts.get("format", "plain"), self.opts.get("layout", "post"))
        self._git("add", "-A", cwd=work)
        if not self._git("status", "--porcelain", cwd=work).strip():
            return "网站仓库无变化"
        self._git("commit", "-m", "DailyPaper: update", cwd=work)
        self._git("push", "origin", branch, cwd=work)
        return f"已推送 {n} 篇到 {repo_url}"
