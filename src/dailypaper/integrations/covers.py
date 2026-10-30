"""首页封面素材库：把一个文件夹里的照片生成三档尺寸和一份清单，网页按天轮换。

- 素材目录：输出目录旁的 ``covers/``（例如 D:/Python/DailyPaperData/covers/），文件名随意，支持 jpg / png / webp / bmp / tif。
- 生成位置：``<content_dir>/assets/covers/``：``cover-<slug>-640.jpg``、``-1200.jpg``、``-2000.jpg`` 与 ``covers.json``。
- 清单里每张图有 ``credit``（页脚署名，默认取文件名，可改）、``position``（裁切焦点，CSS object-position 写法）
  和 ``weather``（天气标签，网页按当地天气优先挑匹配的封面），重新扫描时手动改过的会保留。原图不进内容目录，不会被发布。
- 天气标签：文件名开头写 ``[雨]``、``[雪,夜]`` 这样的方括号最准；没有时按文件名里的词推断（rain / 雨、fog / 雾、
  snow / 雪、storm / lightning / 雷、cloud / overcast / 阴、sunny / 晴、night / star / milky way / 银河 / 星 / 夜）。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from ..core.config import AppConfig
from ..core.io import read_json, write_json

SOURCE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
SIZES = (640, 1200, 2000)
JPEG_QUALITY = 82


def source_dir(cfg: AppConfig) -> Path:
    return cfg.content_dir.parent / "covers"


def output_dir(cfg: AppConfig) -> Path:
    return cfg.content_dir / "assets" / "covers"


def manifest_path(cfg: AppConfig) -> Path:
    return output_dir(cfg) / "covers.json"


def slugify(name: str) -> str:
    """文件名 → 安全的 id：只留字母数字和连字符；中文等字符去掉后为空时用短哈希。"""
    stem = Path(name).stem
    s = re.sub(r"[^A-Za-z0-9]+", "-", stem).strip("-").lower()[:40]
    h = hashlib.md5(stem.encode("utf-8")).hexdigest()[:6]
    return f"{s}-{h}" if s else h


_PEXELS_RE = re.compile(r"^pexels-(.+?)-\d+(?:-\d+)?$", re.I)


def default_credit(name: str) -> str:
    """页脚署名的默认值：Pexels 下载的文件名（pexels-作者-编号.jpg）转成「作者 · Pexels」，其余用文件名（去掉开头的 [天气] 标签）。"""
    stem = _BRACKET_RE.sub("", Path(name).stem).strip() or Path(name).stem
    m = _PEXELS_RE.match(stem)
    if m:
        author = " ".join(w.capitalize() for w in re.split(r"[-_]+", m.group(1)) if w and not w.isdigit())
        return f"{author} · Pexels" if author and author.lower() != "pexels user" else "Pexels"
    return stem


WEATHER_TAGS = ("clear", "cloudy", "fog", "rain", "snow", "storm", "night")
_ZH_TAGS = {"晴": "clear", "多云": "cloudy", "云": "cloudy", "阴": "cloudy", "雾": "fog", "霧": "fog", "雨": "rain",
            "雪": "snow", "雷": "storm", "雷暴": "storm", "夜": "night", "星": "night", "夜晚": "night"}
_BRACKET_RE = re.compile(r"^\s*\[([^\]]+)\]\s*")
_KEYWORDS = {
    "rain": (r"\brain(y|fall)?\b", r"\bdrizzle\b", "雨"),
    "snow": (r"\bsnow(y|fall|ing)?\b", r"\bwinter\b", "雪", "冬"),
    "fog": (r"\bfog(gy)?\b", r"\bmist(y)?\b", r"\bhaze\b", "雾", "霧"),
    "storm": (r"\b(thunder)?storm(y)?\b", r"\blightning\b", r"\bthunder", "雷", "闪电"),
    "cloudy": (r"\bcloud(s|y)?\b", r"\bovercast\b", "阴", "多云"),
    "clear": (r"\bsunny\b", r"\bclear sky\b", r"\bblue sky\b", "晴"),
    "night": (r"\bnight\b", r"\bstar(s|ry)?\b", r"\bmilky way\b", "银河", "星空", "夜"),
}


def tags_from_text(text: str) -> list[str]:
    """把「雨 夜」「rain,night」这样的标签文字转成标准标签。"""
    out = []
    for t in re.split(r"[\s,，、/|]+", text.strip()):
        tag = _ZH_TAGS.get(t) or (t.lower() if t.lower() in WEATHER_TAGS else None)
        if tag and tag not in out:
            out.append(tag)
    return out


def infer_weather(name: str) -> list[str]:
    stem = Path(name).stem
    m = _BRACKET_RE.match(stem)
    if m:
        return tags_from_text(m.group(1))
    low = stem.lower()
    return [tag for tag, pats in _KEYWORDS.items() if any(re.search(p, low) for p in pats)]  # 中文词本身也是合法的正则


def list_sources(cfg: AppConfig) -> list[Path]:
    d = source_dir(cfg)
    if not d.exists():
        return []
    return sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in SOURCE_SUFFIXES)


def read_manifest(cfg: AppConfig) -> dict:
    return read_json(manifest_path(cfg), {}) or {}


def _resize(src: Path, out: Path, width: int) -> tuple[int, int]:
    import pymupdf

    pix = pymupdf.Pixmap(str(src))
    if pix.alpha or pix.n != 3:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    if pix.width > width:
        pix = pymupdf.Pixmap(pix, width, max(1, round(pix.height * width / pix.width)))
    out.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(out), output="jpeg", jpg_quality=JPEG_QUALITY)
    return pix.width, pix.height


def build_covers(cfg: AppConfig, force: bool = False) -> dict:
    """扫描素材目录，补生成缺的尺寸，删掉原图已不存在的输出，重写清单。"""
    out = output_dir(cfg)
    out.mkdir(parents=True, exist_ok=True)
    old = {c["source"]: c for c in read_manifest(cfg).get("covers", []) if isinstance(c, dict) and c.get("source")}
    covers: list[dict] = []
    built = skipped = 0
    keep: set[str] = set()
    for src in list_sources(cfg):
        slug = slugify(src.name)
        files = {str(w): f"cover-{slug}-{w}.jpg" for w in SIZES}
        keep.update(files.values())
        fresh = all((out / f).exists() and (out / f).stat().st_mtime >= src.stat().st_mtime for f in files.values())
        prev = old.get(src.name, {})
        width = height = 0
        if fresh and not force:
            skipped += 1
            width, height = prev.get("width", 0), prev.get("height", 0)
        else:
            try:
                for w, f in files.items():
                    width, height = _resize(src, out / f, int(w))
                built += 1
            except Exception as e:  # noqa: BLE001 - 单张坏图不影响其他
                covers.append({"id": slug, "source": src.name, "error": str(e)})
                continue
        covers.append({
            "id": slug, "source": src.name,
            # 用户没改过署名（还是文件名）时用默认规则，改过的保留
            "credit": prev["credit"] if prev.get("credit") and prev["credit"] != Path(prev.get("source", "")).stem else default_credit(src.name),
            "position": prev.get("position") or "center 50%",
            "weather": prev.get("weather", []) if prev.get("weather_manual") else infer_weather(src.name),
            "weather_manual": bool(prev.get("weather_manual")),
            "width": width, "height": height,
            "files": files,
        })
    removed = 0
    for f in out.glob("cover-*.jpg"):
        if f.name not in keep:
            f.unlink(missing_ok=True)
            removed += 1
    manifest = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "sizes": list(SIZES), "covers": covers}
    write_json(manifest_path(cfg), manifest)
    return {"count": len([c for c in covers if "error" not in c]), "built": built, "skipped": skipped, "removed": removed,
            "errors": [c for c in covers if "error" in c], "source_dir": str(source_dir(cfg)), "output_dir": str(out)}


def covers_view(cfg: AppConfig) -> dict:
    m = read_manifest(cfg)
    return {"source_dir": str(source_dir(cfg)), "output_dir": str(output_dir(cfg)), "sources": len(list_sources(cfg)),
            "generated_at": m.get("generated_at"), "covers": [c for c in m.get("covers", []) if "error" not in c]}


def set_cover_meta(cfg: AppConfig, cover_id: str, credit: str | None = None, position: str | None = None,
                   weather: list[str] | str | None = None) -> dict:
    m = read_manifest(cfg)
    for c in m.get("covers", []):
        if c.get("id") == cover_id:
            if credit is not None:
                c["credit"] = credit.strip()[:120]
            if weather is not None:
                c["weather"] = tags_from_text(weather if isinstance(weather, str) else " ".join(weather))
                c["weather_manual"] = True
            if position is not None and re.match(r"^(left|center|right|\d{1,3}%)\s+(top|center|bottom|\d{1,3}%)$", position.strip()):
                c["position"] = position.strip()
            write_json(manifest_path(cfg), json.loads(json.dumps(m)))
            return c
    raise ValueError(f"清单里没有 {cover_id}")
