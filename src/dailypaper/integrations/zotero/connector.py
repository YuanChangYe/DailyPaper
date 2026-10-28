"""通过本机 Zotero 的 connector 接口写入（与浏览器插件"保存到 Zotero"走同一条路）。

流程：
1. ``/connector/saveItems`` 保存条目（含标签、子笔记、PDF 附件链接）
2. ``/connector/updateSession`` 把这次保存的条目移到目标分类
3. 若 Zotero 没有自动下载 PDF，再下载 PDF 并通过 ``/connector/saveAttachment`` 附加

PDF 存进 Zotero 自己的存储，之后按你的设置同步（例如坚果云 WebDAV），不需要 zotero.org API key。
connector 不能新建分类：目标分类不存在时退回到最接近的已有上级分类或文库根目录，并在结果里说明。
"""

from __future__ import annotations

import json
import time
import uuid

import httpx

from ...core.registry import register
from .base import AddRequest, AddResult, ZoteroRef, ZoteroWriter


def _creators(authors: list[str]) -> list[dict]:
    out = []
    for a in authors:
        parts = a.strip().rsplit(" ", 1)
        if len(parts) == 2:
            out.append({"firstName": parts[0], "lastName": parts[1], "creatorType": "author"})
        else:
            out.append({"name": a.strip(), "creatorType": "author"})
    return out


@register("zotero_writer", "connector")
class ConnectorWriter(ZoteroWriter):
    def __init__(self, cfg) -> None:
        super().__init__(cfg)
        self.base = self.zcfg.local_api.get("base_url", "http://127.0.0.1:23119").rstrip("/") + "/connector"

    def _post(self, ep: str, payload: dict | None = None, **kw) -> httpx.Response:
        if payload is not None:
            kw["json"] = payload
        return httpx.post(f"{self.base}/{ep}", timeout=kw.pop("timeout", 60), **kw)

    def available(self) -> tuple[bool, str]:
        try:
            r = httpx.get(f"{self.base}/ping", timeout=5)
            return ("Zotero is running" in r.text), "Zotero 桌面端正在运行" if r.status_code == 200 else f"HTTP {r.status_code}"
        except Exception as e:  # noqa: BLE001
            return False, f"连不上 Zotero（请先打开 Zotero 桌面端）: {e}"

    def targets(self) -> list[dict]:
        """所有可写位置：[{id: "L1"/"C12", name, level, path}]，path 为 "A/B/C"。"""
        r = self._post("getSelectedCollection", {})
        r.raise_for_status()
        stack: list[str] = []
        out = []
        for t in r.json().get("targets", []):
            level = int(t.get("level", 0))
            stack = stack[:level] + [t["name"]]
            out.append({**t, "path": "/".join(stack[1:])})  # 去掉文库名
        return out

    def collection_paths(self) -> list[str]:
        try:
            return [t["path"] for t in self.targets() if t["id"].startswith("C")]
        except Exception:  # noqa: BLE001
            return []

    def _resolve_target(self, path: str) -> tuple[str, str]:
        """返回 (target id, 实际使用的路径)。"""
        targets = self.targets()
        root = next((t for t in targets if t["id"].startswith("L")), {"id": "L1"})
        if not path:
            return root["id"], ""
        parts = [p for p in path.split("/") if p]
        for n in range(len(parts), 0, -1):
            want = "/".join(parts[:n])
            hit = next((t for t in targets if t["path"].lower() == want.lower()), None)
            if hit:
                return hit["id"], hit["path"]
        # 也允许只写分类名（不含上级路径）
        hit = next((t for t in targets if t["name"].lower() == parts[-1].lower()), None)
        return (hit["id"], hit["path"]) if hit else (root["id"], "")

    def add(self, req: AddRequest) -> AddResult:
        session = uuid.uuid4().hex[:12]
        item_id = uuid.uuid4().hex[:8]
        item = {
            "id": item_id,
            "itemType": "preprint" if req.arxiv_id else "journalArticle",
            "title": req.title,
            "creators": _creators(req.authors),
            "abstractNote": req.abstract,
            "date": req.date,
            "url": req.url,
            "DOI": req.doi,
            "extra": req.extra,
            "tags": [{"tag": t} for t in req.tags],
            "notes": [{"note": req.note_html}] if req.note_html else [],
            "attachments": [],
        }
        if req.arxiv_id:
            item.update({"repository": "arXiv", "archiveID": f"arXiv:{req.arxiv_id}", "publisher": "arXiv"})
        if req.pdf_url and self.zcfg.attach_pdf:
            item["attachments"] = [{"title": "Preprint PDF", "url": req.pdf_url, "mimeType": "application/pdf"}]

        r = self._post("saveItems", {"sessionID": session, "uri": req.url or req.pdf_url, "items": [item]}, timeout=120)
        if r.status_code not in (200, 201):
            return AddResult(False, f"saveItems 失败 HTTP {r.status_code}: {r.text[:200]}")

        target_id, used = self._resolve_target(req.collection)
        u = self._post("updateSession", {"sessionID": session, "target": target_id, "tags": req.tags})
        note = "" if used or not req.collection else f"（找不到分类「{req.collection}」，已放入文库根目录）"
        if u.status_code not in (200, 201):
            note += f"（移动到分类失败: HTTP {u.status_code}）"

        pdf_ok = self._ensure_pdf(session, item_id, req) if req.pdf_url and self.zcfg.attach_pdf else None
        return AddResult(True, f"已加入 Zotero{(' → ' + used) if used else ''}{note}", collection_used=used, pdf_attached=pdf_ok)

    def _ensure_pdf(self, session: str, item_id: str, req: AddRequest) -> bool:
        """saveItems 后检查 PDF 是否已附加；没有则自行下载并通过 saveAttachment 上传给 Zotero。"""
        from .http_api import LocalApiReader

        reader = LocalApiReader(self.cfg)
        for _ in range(5):
            time.sleep(2)
            ref = reader.find(req.title, req.arxiv_id, req.doi)
            if ref and reader.pdf_path(ref.key):
                return True
        try:
            pdf = httpx.get(req.pdf_url, timeout=120, follow_redirects=True)
            if pdf.status_code != 200 or not pdf.content.startswith(b"%PDF"):
                return False
            meta = {"sessionID": session, "parentItemID": item_id, "title": "Preprint PDF", "url": req.pdf_url}
            r = self._post(
                "saveAttachment",
                content=pdf.content,
                headers={"Content-Type": "application/pdf", "X-Metadata": json.dumps(meta)},
                timeout=180,
            )
            return r.status_code in (200, 201)
        except Exception:  # noqa: BLE001
            return False

    def find_after_save(self, req: AddRequest) -> ZoteroRef | None:
        from .http_api import LocalApiReader

        reader = LocalApiReader(self.cfg)
        for _ in range(5):
            ref = reader.find(req.title, req.arxiv_id, req.doi)
            if ref:
                return ref
            time.sleep(1.5)
        return None
