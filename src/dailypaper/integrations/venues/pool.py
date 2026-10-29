"""顶会论文池：SQLite（Python 自带）存标题 + 摘要，FTS5 做全文检索。文件 data/venues.sqlite，不进 git。

表：
- papers     每篇一行；key 去重（arxiv:<id> > doi:<doi> > title:<标题哈希>）；screened / score 缓存筛选结果；read_at 已精读
- papers_fts FTS5 外部内容表，bm25 排序
- sync_log   每个 venue-year 的上次同步时间与篇数
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...core.config import AppConfig, VenueSpec
from ...core.models import Paper, ScreenResult

_ARXIV_RE = re.compile(r"(\d{4}\.\d{4,5})(?:v\d+)?")
_ARXIV_DOI_RE = re.compile(r"10\.48550/arxiv\.(\d{4}\.\d{4,5})", re.I)


def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", title.lower())


def extract_arxiv_id(*texts: str) -> str:
    """从 url / doi / 备注里找 arXiv id。"""
    for t in texts:
        if not t:
            continue
        m = _ARXIV_DOI_RE.search(t)
        if m:
            return m.group(1)
        if "arxiv" in t.lower():
            m = _ARXIV_RE.search(t)
            if m:
                return m.group(1)
    return ""


def paper_key(paper: Paper) -> str:
    aid = paper.extra.get("arxiv_id") or extract_arxiv_id(paper.url_abs, paper.doi, paper.comment)
    if aid:
        return f"arxiv:{aid}"
    if paper.doi:
        return f"doi:{paper.doi.lower()}"
    return "title:" + hashlib.sha1(normalize_title(paper.title).encode("utf-8")).hexdigest()[:16]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class VenuePool:
    def __init__(self, cfg: AppConfig, path: Path | None = None) -> None:
        self.cfg = cfg
        self.path = path or cfg.data_dir / "venues.sqlite"
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS papers(
              key TEXT PRIMARY KEY, venue TEXT NOT NULL, year INTEGER NOT NULL, title TEXT NOT NULL, abstract TEXT NOT NULL DEFAULT '',
              authors TEXT NOT NULL DEFAULT '[]', url_abs TEXT DEFAULT '', url_pdf TEXT DEFAULT '', url_html TEXT DEFAULT '',
              arxiv_id TEXT DEFAULT '', doi TEXT DEFAULT '', adapter TEXT DEFAULT '', added_at TEXT NOT NULL,
              screened TEXT, score REAL, read_at TEXT, seen INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS idx_papers_venue_year ON papers(venue, year);
            CREATE INDEX IF NOT EXISTS idx_papers_score ON papers(score);
            CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(title, abstract, content='papers', content_rowid='rowid', tokenize='unicode61');
            CREATE TRIGGER IF NOT EXISTS papers_ai AFTER INSERT ON papers BEGIN
              INSERT INTO papers_fts(rowid, title, abstract) VALUES (new.rowid, new.title, new.abstract); END;
            CREATE TRIGGER IF NOT EXISTS papers_ad AFTER DELETE ON papers BEGIN
              INSERT INTO papers_fts(papers_fts, rowid, title, abstract) VALUES ('delete', old.rowid, old.title, old.abstract); END;
            CREATE TRIGGER IF NOT EXISTS papers_au AFTER UPDATE OF title, abstract ON papers BEGIN
              INSERT INTO papers_fts(papers_fts, rowid, title, abstract) VALUES ('delete', old.rowid, old.title, old.abstract);
              INSERT INTO papers_fts(rowid, title, abstract) VALUES (new.rowid, new.title, new.abstract); END;
            CREATE TABLE IF NOT EXISTS sync_log(venue TEXT NOT NULL, year INTEGER NOT NULL, synced_at TEXT NOT NULL, count INTEGER NOT NULL,
              adapter TEXT DEFAULT '', PRIMARY KEY(venue, year));
            """
        )
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------ 写入
    def upsert(self, papers: list[Paper], venue: str, year: int, adapter: str = "") -> int:
        """写入一批论文；已存在的 key 只补空字段（先到的会议为准）。返回新增篇数。"""
        new = 0
        now = _now()
        for p in papers:
            if not p.title.strip():
                continue
            key = paper_key(p)
            aid = p.extra.get("arxiv_id") or extract_arxiv_id(p.url_abs, p.doi, p.comment)
            row = self.db.execute("SELECT key, abstract, url_pdf FROM papers WHERE key = ?", (key,)).fetchone()
            if row is None:
                self.db.execute(
                    "INSERT INTO papers(key, venue, year, title, abstract, authors, url_abs, url_pdf, url_html, arxiv_id, doi, adapter, added_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (key, venue, year, p.title.strip(), p.abstract.strip(), json.dumps(p.authors, ensure_ascii=False), p.url_abs, p.url_pdf,
                     p.url_html or (f"https://arxiv.org/html/{aid}" if aid else ""), aid, p.doi, adapter, now),
                )
                new += 1
            else:
                if not row["abstract"] and p.abstract:
                    self.db.execute("UPDATE papers SET abstract = ? WHERE key = ?", (p.abstract.strip(), key))
                if not row["url_pdf"] and p.url_pdf:
                    self.db.execute("UPDATE papers SET url_pdf = ? WHERE key = ?", (p.url_pdf, key))
        self.db.commit()
        return new

    def log_sync(self, venue: str, year: int, count: int, adapter: str = "") -> None:
        self.db.execute("INSERT OR REPLACE INTO sync_log(venue, year, synced_at, count, adapter) VALUES (?,?,?,?,?)", (venue, year, _now(), count, adapter))
        self.db.commit()

    def last_sync(self, venue: str | None = None, year: int | None = None) -> str | None:
        if venue is None:
            row = self.db.execute("SELECT MAX(synced_at) AS t FROM sync_log").fetchone()
        else:
            row = self.db.execute("SELECT synced_at AS t FROM sync_log WHERE venue = ? AND year = ?", (venue, year)).fetchone()
        return row["t"] if row and row["t"] else None

    def set_screened(self, key: str, screen: ScreenResult, score: float) -> None:
        self.db.execute("UPDATE papers SET screened = ?, score = ? WHERE key = ?", (screen.model_dump_json(), float(score), key))
        self.db.commit()

    def set_arxiv(self, key: str, arxiv_id: str, url_html: str = "") -> None:
        self.db.execute("UPDATE papers SET arxiv_id = ?, url_html = COALESCE(NULLIF(?, ''), url_html) WHERE key = ?", (arxiv_id, url_html, key))
        self.db.commit()

    def mark_read(self, key: str) -> None:
        self.db.execute("UPDATE papers SET read_at = ? WHERE key = ?", (_now(), key))
        self.db.commit()

    def mark_seen(self, keys: list[str]) -> None:
        self.db.executemany("UPDATE papers SET seen = 1 WHERE key = ?", [(k,) for k in keys])
        self.db.commit()

    # ------------------------------------------------------------ 读取
    def _spec(self, venue_id: str) -> VenueSpec | None:
        return self.cfg.venues.get(venue_id)

    def to_paper(self, row: sqlite3.Row) -> Paper:
        spec = self._spec(row["venue"])
        return Paper(
            id=row["key"], source="venue", title=row["title"], abstract=row["abstract"], authors=json.loads(row["authors"] or "[]"),
            url_abs=row["url_abs"] or "", url_pdf=row["url_pdf"] or "", url_html=row["url_html"] or "", doi=row["doi"] or "",
            extra={"venue": row["venue"], "venue_name": spec.name if spec else row["venue"].upper(), "year": row["year"],
                   "ccf": spec.ccf if spec else "", "core": spec.core if spec else "", "adapter": row["adapter"], "key": row["key"],
                   "arxiv_id": row["arxiv_id"] or ""},
        )

    def get(self, key: str) -> Paper | None:
        row = self.db.execute("SELECT * FROM papers WHERE key = ?", (key,)).fetchone()
        return self.to_paper(row) if row else None

    def screened_of(self, key: str) -> tuple[ScreenResult | None, float | None]:
        row = self.db.execute("SELECT screened, score FROM papers WHERE key = ?", (key,)).fetchone()
        if not row or not row["screened"]:
            return None, None
        return ScreenResult.model_validate_json(row["screened"]), row["score"]

    def candidates(self, venues: list[str] | None = None, years: list[int] | None = None, unread_only: bool = True, limit: int = 0) -> list[sqlite3.Row]:
        """补足用的候选行（未读、未 seen）：已筛选的在前、分数高的在前；未筛选的随机顺序，让每次筛选的样本跨会议、跨年份。"""
        where, args = self._where(venues, years)
        if unread_only:
            where.append("read_at IS NULL AND seen = 0")
        sql = "SELECT * FROM papers" + (" WHERE " + " AND ".join(where) if where else "") + \
            " ORDER BY (screened IS NULL), score DESC, random()" + (f" LIMIT {int(limit)}" if limit else "")
        return list(self.db.execute(sql, args).fetchall())

    def _where(self, venues: list[str] | None, years: list[int] | None) -> tuple[list[str], list[Any]]:
        where: list[str] = []
        args: list[Any] = []
        if venues:
            where.append("venue IN ({})".format(",".join("?" * len(venues))))
            args += list(venues)
        if years:
            where.append("year IN ({})".format(",".join("?" * len(years))))
            args += [int(y) for y in years]
        return where, args

    def search_fts(self, query: str, venues: list[str] | None = None, years: list[int] | None = None, limit: int = 100) -> list[dict]:
        """bm25 全文检索；query 用 FTS5 语法（已由调用方清理）。返回 [{paper, rank}]。"""
        where, args = self._where(venues, years)
        sql = ("SELECT p.*, bm25(papers_fts, 3.0, 1.0) AS rank FROM papers_fts JOIN papers p ON p.rowid = papers_fts.rowid "
               "WHERE papers_fts MATCH ?" + ("".join(" AND p." + w for w in where)) + " ORDER BY rank LIMIT ?")
        try:
            rows = self.db.execute(sql, [query, *args, int(limit)]).fetchall()
        except sqlite3.OperationalError:
            # 查询语法不合法时退化成按空格拆词的 OR 查询
            terms = [f'"{t}"' for t in re.findall(r"[\w\-]+", query) if len(t) > 1]
            if not terms:
                return []
            rows = self.db.execute(sql, [" OR ".join(terms), *args, int(limit)]).fetchall()
        return [{"paper": self.to_paper(r), "rank": float(r["rank"])} for r in rows]

    def stats(self) -> dict:
        per = {}
        for r in self.db.execute("SELECT venue, year, COUNT(*) AS n, SUM(screened IS NOT NULL) AS screened, SUM(read_at IS NOT NULL) AS read FROM papers GROUP BY venue, year ORDER BY venue, year DESC"):
            per.setdefault(r["venue"], []).append({"year": r["year"], "count": r["n"], "screened": r["screened"], "read": r["read"]})
        logs = {(r["venue"], r["year"]): r["synced_at"] for r in self.db.execute("SELECT venue, year, synced_at FROM sync_log")}
        for venue, rows in per.items():
            for x in rows:
                x["synced_at"] = logs.get((venue, x["year"]))
        total = self.db.execute("SELECT COUNT(*) AS n, SUM(screened IS NOT NULL) AS s, SUM(read_at IS NOT NULL) AS r FROM papers").fetchone()
        return {"path": str(self.path), "total": total["n"], "screened": total["s"] or 0, "read": total["r"] or 0,
                "last_sync": self.last_sync(), "venues": per,
                "sync_log": [{"venue": v, "year": y, "synced_at": t} for (v, y), t in sorted(logs.items())]}
