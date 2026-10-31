"""Zotero 后端接口（registry kind = ``zotero_reader`` / ``zotero_writer``）。

读和写分开配置（settings.yaml → zotero.reader / zotero.writer），可以自由组合：
- 读：``local_api``（本机 Zotero 7，只读）或 ``web_api``（zotero.org）
- 写：``connector``（本机 Zotero，PDF 走你自己的附件同步，如坚果云 WebDAV）或 ``web_api``
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ...core.config import AppConfig


@dataclass
class ZoteroRef:
    key: str  # 条目 key，例如 ABCD1234
    library_id: str = "0"
    title: str = ""

    @property
    def select_link(self) -> str:
        return f"zotero://select/library/items/{self.key}"


@dataclass
class AddRequest:
    title: str
    authors: list[str]
    abstract: str = ""
    date: str = ""
    url: str = ""
    pdf_url: str = ""
    arxiv_id: str = ""
    doi: str = ""
    collection: str = ""  # 分类路径，例如 "DailyPaper/Conformal"；空 = 文库根目录
    tags: list[str] = field(default_factory=list)
    note_html: str = ""
    extra: str = ""


@dataclass
class AddResult:
    ok: bool
    message: str
    ref: ZoteroRef | None = None
    collection_used: str = ""
    pdf_attached: bool | None = None


class ZoteroReader(ABC):
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.zcfg = cfg.settings.zotero

    @abstractmethod
    def available(self) -> tuple[bool, str]:
        ...

    @abstractmethod
    def find(self, title: str, arxiv_id: str = "", doi: str = "") -> ZoteroRef | None:
        ...

    @abstractmethod
    def iter_items(self, collections: list[str] | None = None, limit: int = 3000) -> list[dict]:
        """返回顶层条目的 data 字典。collections 为分类名称列表，空 = 整个文库。"""

    @abstractmethod
    def get_item(self, key: str) -> dict:
        ...

    def pdf_path(self, key: str) -> str | None:
        """条目附带的本地 PDF 路径（没有时返回 None）。"""
        return None

    def collections(self) -> list[dict]:
        return []


class ZoteroWriter(ABC):
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.zcfg = cfg.settings.zotero

    @abstractmethod
    def available(self) -> tuple[bool, str]:
        ...

    @abstractmethod
    def add(self, req: AddRequest) -> AddResult:
        ...

    def collection_paths(self) -> list[str]:
        """可选的目标分类（用于审核页的下拉框）。"""
        return []

    def collection_tree(self) -> tuple[list[dict], str]:
        """「保存到 Zotero」对话框用：[{path, name, level}]（按树的顺序）和 Zotero 里当前选中的分类路径。"""
        out = []
        for path in sorted(self.collection_paths(), key=str.lower):
            parts = path.split("/")
            out.append({"path": path, "name": parts[-1], "level": len(parts) - 1})
        return out, ""

    def resolve(self, path: str) -> str:
        """配置或选择的分类路径实际会落到哪里（找不到时的去处）；空串 = 文库根目录。"""
        return path
