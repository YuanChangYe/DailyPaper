"""生成桌面快捷方式用的图标 src/dailypaper/app/static/assets/dailypaper.ico（波浪 logo，深青底）。

只依赖 pymupdf（渲染 SVG）；ICO 容器里直接放 PNG，Windows Vista 及以上支持。
"""

from __future__ import annotations

import struct
from pathlib import Path

SVG = """<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 256 256'>
<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'><stop offset='0' stop-color='#17414F'/><stop offset='1' stop-color='#0C232B'/></linearGradient></defs>
<rect width='256' height='256' rx='58' fill='url(#g)'/>
<path d='M44 150c28-34 56-34 84 0s56 34 84 0' fill='none' stroke='#F3B7A6' stroke-width='16' stroke-linecap='round'/>
<path d='M44 104c28-34 56-34 84 0s56 34 84 0' fill='none' stroke='#EEF5F6' stroke-width='16' stroke-linecap='round'/>
</svg>"""


def main() -> None:
    import pymupdf

    out = Path(__file__).resolve().parents[1] / "src" / "dailypaper" / "app" / "static" / "assets" / "dailypaper.ico"
    pngs: list[tuple[int, bytes]] = []
    with pymupdf.open(stream=SVG.encode("utf-8"), filetype="svg") as doc:
        page = doc[0]
        for size in (256, 64, 48, 32, 16):
            zoom = size / page.rect.width
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=True)
            pngs.append((size, pix.tobytes("png")))
    header = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries, blobs = b"", b""
    for size, data in pngs:
        s = 0 if size == 256 else size
        entries += struct.pack("<BBBBHHII", s, s, 0, 0, 1, 32, len(data), offset + len(blobs))
        blobs += data
    out.write_bytes(header + entries + blobs)
    print(f"written {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
