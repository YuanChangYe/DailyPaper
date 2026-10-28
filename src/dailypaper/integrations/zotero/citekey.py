"""citekey：优先向 Better BibTeX 要，没装 BBT 时按"姓 + 年份 + 标题首个实词"生成（与 BBT 默认格式相近）。"""

from __future__ import annotations

import re
import unicodedata

import httpx

_SKIP = {"a", "an", "the", "on", "of", "for", "in", "to", "and", "with", "via", "towards", "toward", "is", "are", "when", "how", "what", "why"}


def bbt_citekey(base_url: str, key: str) -> str | None:
    try:
        r = httpx.post(
            f"{base_url.rstrip('/')}/better-bibtex/json-rpc",
            json={"jsonrpc": "2.0", "method": "item.citationkey", "params": [[key]], "id": 1},
            timeout=10,
        )
        if r.status_code != 200:
            return None
        result = r.json().get("result") or {}
        return next(iter(result.values()), None) if isinstance(result, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def make_citekey(authors: list[str], year: str, title: str) -> str:
    last = _ascii(authors[0].split()[-1]).lower() if authors else "anon"
    word = next((w for w in re.findall(r"[A-Za-z]+", _ascii(title)) if w.lower() not in _SKIP), "paper")
    return re.sub(r"[^a-z0-9]", "", last) + (year or "") + word.lower()
