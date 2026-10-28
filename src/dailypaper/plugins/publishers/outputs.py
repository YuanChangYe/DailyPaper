"""有外部副作用的输出：审核队列、git 提交推送、复制到 Obsidian vault、同步到个人网站。"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from ...core import registry
from ...core.interfaces import Publisher
from ...core.models import Candidate
from ...core.registry import register
from ...integrations.review import QueueEntry, ReviewStore


@register("publisher", "review_queue")
class ReviewQueuePublisher(Publisher):
    """把精读完成的论文加入待审核队列（data/review_queue.json）。"""

    def run(self, items: list[Candidate]) -> list[Candidate]:
        entries = [
            QueueEntry(
                id=c.id,
                title=c.paper.title,
                date=self.ctx.run_date.isoformat(),
                authors=c.paper.authors[:20],
                topics=c.main_topics(),
                paper_type=c.screen.paper_type if c.screen else "",
                score=c.score,
                value_score=c.note.value_score,
                tldr=c.note.tldr,
                core_insight=c.note.core_insight,
                worth_reading=c.note.worth_reading,
                note_path=c.note_path,
                url_abs=c.paper.url_abs,
                url_pdf=c.paper.url_pdf,
                doi=c.paper.doi,
                abstract=c.paper.abstract,
                source=c.paper.source,
            )
            for c in items
            if c.note and c.note_path
        ]
        n = ReviewStore(self.config).append(entries)
        self.log.info("待审核队列新增 %d 篇", n)
        return items


@register("publisher", "git_commit")
class GitCommitPublisher(Publisher):
    class Params(BaseModel):
        paths: list[str] = Field(default_factory=lambda: ["docs", "data"])
        message: str = "daily: {date}"
        push: bool = False
        author_name: str = "dailypaper-bot"
        author_email: str = "dailypaper-bot@users.noreply.github.com"

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=self.config.root, capture_output=True, text=True)

    def run(self, items: list[Candidate]) -> list[Candidate]:
        self._git("add", "--", *self.p.paths)
        if self._git("diff", "--cached", "--quiet").returncode == 0:
            self.log.info("没有需要提交的变化")
            return items
        msg = self.p.message.format(date=self.ctx.run_date.isoformat())
        r = self._git(
            "-c", f"user.name={self.p.author_name}", "-c", f"user.email={self.p.author_email}", "commit", "-m", msg
        )
        if r.returncode != 0:
            raise RuntimeError(f"git commit 失败: {r.stderr.strip()}")
        if self.p.push:
            r = self._git("push")
            if r.returncode != 0:
                raise RuntimeError(f"git push 失败: {r.stderr.strip()}")
        self.log.info("已提交%s: %s", "并推送" if self.p.push else "", msg)
        return items


@register("publisher", "local_copy")
class LocalCopyPublisher(Publisher):
    """本地运行时把笔记直接复制进 Obsidian vault（不用 Git 同步时使用）。"""

    class Params(BaseModel):
        target: str | None = None  # 默认用 settings.yaml → obsidian.vault_path
        subdirs: list[str] = Field(default_factory=lambda: ["papers", "daily", "topics"])

    def run(self, items: list[Candidate]) -> list[Candidate]:
        target = self.p.target or self.config.settings.obsidian.vault_path
        if not target:
            self.log.warning("未配置 obsidian.vault_path，跳过 local_copy")
            return items
        dst = Path(target)
        for sub in self.p.subdirs:
            src = self.config.content_dir / sub
            if src.exists():
                shutil.copytree(src, dst / sub, dirs_exist_ok=True)
        self.log.info("已复制到 %s", dst)
        return items


@register("publisher", "site")
class SitePublisher(Publisher):
    """调用 settings.yaml → site.adapter 指定的站点适配器。"""

    def run(self, items: list[Candidate]) -> list[Candidate]:
        site = self.config.settings.site
        adapter = registry.get("site_adapter", site.adapter)(self.config, site.adapter_options)
        self.log.info(adapter.publish())
        return items
