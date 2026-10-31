"""PDF 出处定位表：把笔记里的出处（§5.2、Table 2、Figure 3、Appendix A）对应到 PDF 页码。

- 逐页扫描：``5.2 Method``（或编号和标题分两行）、``Table 2:``、``Figure 3.``、``Appendix A``；
- PDF 有书签时，只接受和书签标题对得上的小节标题，避免把图里的坐标刻度、编号列表当成小节。
结果缓存在 PDF 旁边的 ``<slug>.anchors.json``；PDF 更新（mtime 变化）时重算。
"""

from __future__ import annotations

import re
from pathlib import Path

from ..core.io import read_json, write_json

_NUM_RE = re.compile(r"^([A-H]|\d{1,2})((?:\.\d{1,2}){0,3})\.?$")
_HEAD_RE = re.compile(r"^([A-H]|\d{1,2})((?:\.\d{1,2}){0,3})\.?\s+([A-Z][^\n]{2,90})$")
_APP_HEAD_RE = re.compile(r"^appendix\s+([A-H])\b", re.I)
_CAP_RE = re.compile(r"^(table|tab\.|figure|fig\.)\s*(\d+)\s*[:.|]", re.I)


def _put(out: dict[str, int], key: str, page: int) -> None:
    out.setdefault(key, page)  # 第一次出现的位置（目录页之后的正文、图表标题本身）


def _norm(t: str) -> str:
    return re.sub(r"[^a-z]", "", t.lower())[:24]


def _valid_num(head: str, tail: str) -> bool:
    # 1.0、2.00 之类是坐标轴刻度，不是小节号；小节号的每一段都从 1 开始
    return all(int(x) > 0 for x in tail.strip(".").split(".") if x) and (not head.isdigit() or int(head) > 0)


def _is_title(line: str) -> bool:
    letters = sum(ch.isalpha() for ch in line)
    return 3 <= len(line) <= 90 and line[:1].isupper() and letters >= 0.6 * len(line) \
        and not _CAP_RE.match(line) and not line.lower().startswith(("arxiv", "proof", "where", "lemma", "theorem"))


def build_anchors(pdf_path: Path) -> dict:
    import pymupdf  # 可选依赖：pip install dailypaper[pdf]

    anchors: dict[str, int] = {}
    with pymupdf.open(pdf_path) as doc:
        pages = doc.page_count
        # 书签标题（多数不带编号）只用来确认正文里找到的标题确实是小节标题
        toc = {_norm(str(title)) for _level, title, _page in doc.get_toc(simple=True)}
        toc.discard("")

        def accept(title: str) -> bool:
            return _is_title(title) and (not toc or _norm(title) in toc or any(_norm(title).startswith(t[:12]) for t in toc if len(t) >= 12))

        for i, page in enumerate(doc, start=1):
            lines = [x.strip() for x in page.get_text("text").splitlines() if x.strip()]
            for j, line in enumerate(lines):
                if len(line) > 160:
                    continue
                m = _CAP_RE.match(line)
                if m:
                    kind = "Table" if m.group(1).lower().startswith("tab") else "Figure"
                    _put(anchors, f"{kind} {m.group(2)}", i)
                    continue
                m = _APP_HEAD_RE.match(line)
                if m:
                    _put(anchors, "§" + m.group(1).upper(), i)
                    continue
                m = _HEAD_RE.match(line)  # "5.2 Method" 同一行
                if m and _valid_num(m.group(1), m.group(2)) and accept(m.group(3)):
                    _put(anchors, "§" + m.group(1) + m.group(2), i)
                    continue
                m = _NUM_RE.match(line)  # "5.2" 单独一行，下一行是标题
                if m and j + 1 < len(lines) and _valid_num(m.group(1), m.group(2)) and accept(lines[j + 1]):
                    _put(anchors, "§" + m.group(1) + m.group(2), i)
    return {"pages": pages, "anchors": anchors}


def load_anchors(pdf_path: Path) -> dict:
    cache = pdf_path.with_suffix(".anchors.json")
    mtime = pdf_path.stat().st_mtime
    hit = read_json(cache, None)
    if hit and hit.get("mtime") == mtime:
        return {"pages": hit["pages"], "anchors": hit["anchors"]}
    data = build_anchors(pdf_path)
    write_json(cache, {**data, "mtime": mtime})
    return data


_PAGE_RE = re.compile(r"(?:^|[^A-Za-z])(?:pp?\.|page)\s*(\d+)", re.I)  # "Prop. 1" 里的 p 不算
_REF_RE = re.compile(
    r"(?P<p>(?<![A-Za-z])(?:p\.|pp\.|page)\s*(?P<pn>\d+))"
    r"|(?P<s>(?:§|sec(?:tion)?\.?\s*)(?P<sn>[A-H]?\d*(?:\.\d+)*[A-H]?))"
    r"|(?P<app>appendix\s+(?P<an>[A-H](?:\.\d+)*))"
    r"|(?P<t>(?:table|tab\.)\s*(?P<tn>\d+))"
    r"|(?P<f>(?:figure|fig\.?)\s*(?P<fn>\d+))",
    re.I,
)


def resolve(ref: str, anchors: dict[str, int]) -> int | None:
    """把一个出处文本（"§5.2"、"Table 2"、"p.7"、"附录 A"）解析成页码；章节找不到时退到上一级。"""
    ref = ref.replace("附录", "Appendix ").replace("表", "Table ").replace("图", "Figure ")
    pg = _PAGE_RE.search(ref)
    if pg:  # 明确写了页码就用页码
        return int(pg.group(1))
    m = _REF_RE.search(ref)
    if not m:
        return None
    if m.group("p"):
        return int(m.group("pn"))
    if m.group("t"):
        return anchors.get(f"Table {m.group('tn')}")
    if m.group("f"):
        return anchors.get(f"Figure {m.group('fn')}")
    num = (m.group("sn") or m.group("an") or "").upper()
    while num:
        if f"§{num}" in anchors:
            return anchors[f"§{num}"]
        num = num.rsplit(".", 1)[0] if "." in num else ""
    return None
