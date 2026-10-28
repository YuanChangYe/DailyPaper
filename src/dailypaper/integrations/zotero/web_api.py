"""通过 zotero.org Web API 写入（可选）。适合在云端直接入库；PDF 只保存为链接附件，不占 Zotero 存储空间。

需要：settings.yaml → zotero.web_api.library_id（zotero.org/settings/keys 页面显示的 userID）
      环境变量 ZOTERO_API_KEY（同一页面创建，勾选写权限）
"""

from __future__ import annotations

import os

import httpx

from ...core.registry import register
from .base import AddRequest, AddResult, ZoteroRef, ZoteroWriter
from .connector import _creators


@register("zotero_writer", "web_api")
class WebApiWriter(ZoteroWriter):
    def __init__(self, cfg) -> None:
        super().__init__(cfg)
        w = self.zcfg.web_api
        kind = "groups" if w.get("library_type") == "group" else "users"
        self.library_id = str(w.get("library_id", ""))
        self.base = f"https://api.zotero.org/{kind}/{self.library_id}"
        self.api_key = os.environ.get(w.get("api_key_env", "ZOTERO_API_KEY"), "")

    def _h(self) -> dict:
        return {"Zotero-API-Key": self.api_key, "Zotero-API-Version": "3"}

    def available(self) -> tuple[bool, str]:
        if not (self.library_id and self.api_key):
            return False, "未配置 zotero.web_api.library_id 或 ZOTERO_API_KEY"
        r = httpx.get(f"{self.base}/collections/top", params={"limit": 1}, headers=self._h(), timeout=20)
        return r.status_code == 200, f"Web API HTTP {r.status_code}"

    def _collections(self) -> list[dict]:
        out, start = [], 0
        while True:
            r = httpx.get(f"{self.base}/collections", params={"limit": 100, "start": start}, headers=self._h(), timeout=30)
            r.raise_for_status()
            batch = r.json()
            out += [{"key": c["key"], "name": c["data"]["name"], "parent": c["data"].get("parentCollection") or None} for c in batch]
            if len(batch) < 100:
                return out
            start += 100

    def collection_paths(self) -> list[str]:
        cols = {c["key"]: c for c in self._collections()}

        def path(c):
            return f"{path(cols[c['parent']])}/{c['name']}" if c["parent"] in cols else c["name"]

        return sorted(path(c) for c in cols.values())

    def _ensure_collection(self, path: str) -> str | None:
        """按路径查找分类，不存在则逐级创建，返回 collection key。"""
        if not path:
            return None
        cols = self._collections()
        parent = None
        for name in [p for p in path.split("/") if p]:
            hit = next((c for c in cols if c["name"] == name and c["parent"] == parent), None)
            if hit is None:
                r = httpx.post(f"{self.base}/collections", json=[{"name": name, "parentCollection": parent or False}],
                               headers=self._h(), timeout=30)
                r.raise_for_status()
                key = r.json()["successful"]["0"]["key"]
                hit = {"key": key, "name": name, "parent": parent}
                cols.append(hit)
            parent = hit["key"]
        return parent

    def add(self, req: AddRequest) -> AddResult:
        coll = self._ensure_collection(req.collection)
        item = {
            "itemType": "preprint" if req.arxiv_id else "journalArticle",
            "title": req.title,
            "creators": _creators(req.authors),
            "abstractNote": req.abstract,
            "date": req.date,
            "url": req.url,
            "DOI": req.doi,
            "extra": req.extra,
            "tags": [{"tag": t} for t in req.tags],
            "collections": [coll] if coll else [],
        }
        if req.arxiv_id:
            item.update({"repository": "arXiv", "archiveID": f"arXiv:{req.arxiv_id}"})
        r = httpx.post(f"{self.base}/items", json=[item], headers=self._h(), timeout=30)
        if r.status_code != 200 or not r.json().get("successful"):
            return AddResult(False, f"创建条目失败 HTTP {r.status_code}: {r.text[:200]}")
        key = r.json()["successful"]["0"]["key"]
        children = []
        if req.note_html:
            children.append({"itemType": "note", "parentItem": key, "note": req.note_html, "tags": []})
        if req.pdf_url:
            children.append({"itemType": "attachment", "linkMode": "linked_url", "parentItem": key,
                             "title": "Preprint PDF", "url": req.pdf_url, "contentType": "application/pdf", "tags": []})
        if children:
            httpx.post(f"{self.base}/items", json=children, headers=self._h(), timeout=30)
        return AddResult(True, f"已通过 Web API 加入 Zotero → {req.collection or '文库根目录'}",
                         ref=ZoteroRef(key=key, library_id=self.library_id, title=req.title),
                         collection_used=req.collection, pdf_attached=bool(req.pdf_url))
