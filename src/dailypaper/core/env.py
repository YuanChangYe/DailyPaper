"""读取项目根目录的 .env（KEY=value）。

- 真实的环境变量（系统里设置的、GitHub Secrets）优先，永远不被 .env 覆盖。
- ``refresh=True``：只有文件改过才重读，并同步由 .env 提供的变量的增、删、改。
  本地助手每次处理接口请求前都会调用它，所以在 .env 里新加或修改 key 后不需要重启。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

_LOCK = threading.Lock()
_STATE: dict = {"keys": set(), "mtime": None, "path": None}


def _parse(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if key and value:
            out[key] = value
    return out


def load_dotenv(path: Path, refresh: bool = False) -> None:
    path = Path(path).resolve()  # 同一个文件不管怎么传进来都认作同一个
    with _LOCK:
        if not path.exists():
            if refresh and _STATE["path"] == str(path):  # 文件被删了：撤掉它提供过的变量
                for k in _STATE["keys"]:
                    os.environ.pop(k, None)
                _STATE.update(keys=set(), mtime=None)
            return
        mtime = path.stat().st_mtime
        if refresh and _STATE["path"] == str(path) and mtime == _STATE["mtime"]:
            return
        values = _parse(path)
        mine: set[str] = _STATE["keys"] if _STATE["path"] == str(path) else set()
        for key, value in values.items():
            if key not in os.environ or key in mine:
                os.environ[key] = value
                mine.add(key)
        for key in mine - set(values):  # .env 里删掉的 key
            os.environ.pop(key, None)
        _STATE.update(keys=mine & set(values), mtime=mtime, path=str(path))


def dotenv_keys() -> set[str]:
    """当前由 .env 提供的变量名（不含真实环境变量）。"""
    return set(_STATE["keys"])
