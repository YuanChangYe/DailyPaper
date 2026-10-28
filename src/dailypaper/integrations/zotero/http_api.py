"""Zotero 的 HTTP API 读取：本机 Zotero 7 的 local API 与 zotero.org Web API 格式相同，共用实现。

本地 API 需在 Zotero 设置 → 高级 中勾选"允许此计算机上的其他应用程序与 Zotero 通信"。
"""

from __future__ import annotations

import os
import re
from urllib.parse import unquote, urlparse

import httpx

from ...core.registry import register
from .base import ZoteroReader, ZoteroRef
from .profile import norm_title

_ARXIV_RE = re.compile(r"(\d{4}\.\d{4,5})")


class _HttpReader(ZoteroReader):
    base: str = ""
    prefix: str = ""

    def headers(self) -> dict:
        return {"Zotero-API-Version": "3"}

    def _get(self, path: str, **params) -> httpx.Response:
        r = httpx.get(f"{self.base}{self.prefix}{path}", params=params, headers=self.headers(), timeout=30)
        r.raise_for_status()
        return r

    def available(self) -> tuple[bool, str]:
        try:
            r = self._get("/items/top", limit=1)
            return True, f"可读取（共 {r.headers.get('Total-Results', '?')} 条）"
        except Exception as e:  # noqa: BLE001
            return False, f"不可用: {e}"

    def find(self, title: str, arxiv_id: str = "", doi: str = "") -> ZoteroRef | None:
        queries = [q for q in (arxiv_id, title[:120]) if q]
        want = norm_title(title)
        for q in queries:
            for it in self._get("/items", q=q, qmode="everything" if q == arxiv_id else "titleCreatorYear",
                                limit=25, format="json", itemType="-attachment").json():
                d = it["data"]
                if d.get("itemType") == "note":
                    continue
                ids = _ARXIV_RE.findall(" ".join(str(d.get(k, "")) for k in ("url", "extra", "archiveID", "number")))
                if (arxiv_id and arxiv_id in ids) or (doi and d.get("DOI", "").lower() == doi.lower()) or norm_title(d.get("title", "")) == want:
                    return ZoteroRef(key=it["key"], library_id=str(it.get("library", {}).get("id", "0")), title=d.get("title", ""))
        return None

    def collections(self) -> list[dict]:
        out, start = [], 0
        while True:
            batch = self._get("/collections", limit=100, start=start, format="json").json()
            out += [{"key": c["key"], "name": c["data"]["name"], "parent": c["data"].get("parentCollection") or None} for c in batch]
            if len(batch) < 100:
                return out
            start += 100

    def _collection_keys(self, names: list[str]) -> list[str]:
        wanted = set(names)
        return [c["key"] for c in self.collections() if c["name"] in wanted]

    def iter_items(self, collections: list[str] | None = None, limit: int = 3000) -> list[dict]:
        paths = [f"/collections/{k}/items/top" for k in self._collection_keys(collections)] if collections else ["/items/top"]
        out: list[dict] = []
        for path in paths:
            start = 0
            while len(out) < limit:
                batch = self._get(path, limit=100, start=start, format="json").json()
                out += [b["data"] for b in batch if b["data"].get("itemType") not in ("attachment", "note")]
                if len(batch) < 100:
                    break
                start += 100
        return out[:limit]

    def get_item(self, key: str) -> dict:
        return self._get(f"/items/{key}", format="json").json()["data"]

    def items_in_collection(self, key: str | None, limit: int = 500) -> list[dict]:
        """某个分类（None = 未分类的顶层条目）下的顶层条目，整理成网页需要的字段。"""
        path = f"/collections/{key}/items/top" if key else "/items/top"
        out: list[dict] = []
        start = 0
        while len(out) < limit:
            batch = self._get(path, limit=100, start=start, format="json").json()
            for it in batch:
                d = it["data"]
                if d.get("itemType") in ("attachment", "note"):
                    continue
                if key is None and d.get("collections"):
                    continue
                ids = _ARXIV_RE.findall(" ".join(str(d.get(k, "")) for k in ("url", "extra", "archiveID", "number")))
                out.append({
                    "key": it["key"],
                    "title": d.get("title", ""),
                    "authors": [c.get("name") or f"{c.get('firstName', '')} {c.get('lastName', '')}".strip()
                                for c in d.get("creators", []) if c.get("creatorType", "author") == "author"],
                    "date": d.get("date", ""),
                    "item_type": d.get("itemType", ""),
                    "arxiv_id": ids[0] if ids else "",
                    "doi": d.get("DOI", ""),
                    "url": d.get("url", ""),
                    "tags": [t.get("tag", "") for t in d.get("tags", [])],
                    "date_added": d.get("dateAdded", ""),
                })
            if len(batch) < 100:
                break
            start += 100
        return out[:limit]

    def pdf_path(self, key: str) -> str | None:
        for child in self._get(f"/items/{key}/children", format="json").json():
            d = child["data"]
            if d.get("contentType") != "application/pdf":
                continue
            href = child.get("links", {}).get("enclosure", {}).get("href", "")
            if href.startswith("file:"):
                path = unquote(urlparse(href).path)
                if re.match(r"^/[A-Za-z]:", path):  # Windows: /D:/... → D:/...
                    path = path[1:]
                if os.path.exists(path):
                    return path
        return None


@register("zotero_reader", "local_api")
class LocalApiReader(_HttpReader):
    def __init__(self, cfg) -> None:
        super().__init__(cfg)
        self.base = self.zcfg.local_api.get("base_url", "http://127.0.0.1:23119").rstrip("/")
        self.prefix = "/api/users/0"


@register("zotero_reader", "web_api")
class WebApiReader(_HttpReader):
    def __init__(self, cfg) -> None:
        super().__init__(cfg)
        w = self.zcfg.web_api
        self.base = "https://api.zotero.org"
        kind = "groups" if w.get("library_type") == "group" else "users"
        self.prefix = f"/{kind}/{w.get('library_id', '')}"
        self.api_key = os.environ.get(w.get("api_key_env", "ZOTERO_API_KEY"), "")

    def headers(self) -> dict:
        return {**super().headers(), "Zotero-API-Key": self.api_key}
