"""插件注册表。

所有可替换的组件（stage、LLM provider、lens 适配器、全文加载器、Zotero 后端、站点适配器）
都按 ``kind`` 和 ``name`` 注册在这里：

    @register("filter", "keyword")
    class KeywordFilter(Filter): ...

配置里用 ``"<kind>.<name>"`` 引用，例如 ``use: filter.keyword``。

第三方插件：在自己包的 pyproject 里声明 entry point 组 ``dailypaper.plugins``，
指向一个模块；导入该模块时它自己调用 ``register`` 即可，不需要修改本仓库。
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from collections import defaultdict
from collections.abc import Callable
from importlib.metadata import entry_points
from typing import Any, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")

_REGISTRY: dict[str, dict[str, Any]] = defaultdict(dict)
_DISCOVERED = False

# 内置插件所在的包，发现时会递归导入
BUILTIN_PACKAGES = (
    "dailypaper.plugins",
    "dailypaper.llm",
    "dailypaper.prompting.lens_adapters",
    "dailypaper.integrations",
)


class PluginNotFound(KeyError):
    pass


def register(kind: str, name: str) -> Callable[[T], T]:
    def deco(obj: T) -> T:
        existing = _REGISTRY[kind].get(name)
        if existing is not None and existing is not obj:
            log.warning("插件 %s.%s 被覆盖：%r -> %r", kind, name, existing, obj)
        _REGISTRY[kind][name] = obj
        obj.plugin_kind = kind  # type: ignore[attr-defined]
        obj.plugin_name = name  # type: ignore[attr-defined]
        return obj

    return deco


def _import_tree(package: str) -> None:
    try:
        pkg = importlib.import_module(package)
    except ImportError as e:  # 可选依赖缺失时跳过整个包
        log.debug("跳过 %s: %s", package, e)
        return
    if not hasattr(pkg, "__path__"):
        return
    for info in pkgutil.walk_packages(pkg.__path__, prefix=f"{package}."):
        try:
            importlib.import_module(info.name)
        except ImportError as e:
            # 插件的可选依赖没装（例如 pymupdf），只影响这一个插件
            log.debug("插件模块 %s 未加载: %s", info.name, e)


def discover(force: bool = False) -> None:
    """导入内置插件，并加载通过 entry points 声明的第三方插件。"""
    global _DISCOVERED
    if _DISCOVERED and not force:
        return
    for package in BUILTIN_PACKAGES:
        _import_tree(package)
    try:
        eps = entry_points(group="dailypaper.plugins")
    except TypeError:  # Python < 3.10 的旧接口，保险起见
        eps = entry_points().get("dailypaper.plugins", [])  # type: ignore[attr-defined]
    for ep in eps:
        try:
            ep.load()
        except Exception as e:  # noqa: BLE001 - 第三方插件失败不能拖垮主程序
            log.warning("第三方插件 %s 加载失败: %s", ep.name, e)
    _DISCOVERED = True


def get(kind: str, name: str | None = None) -> Any:
    """``get("filter", "keyword")`` 或 ``get("filter.keyword")``。"""
    discover()
    if name is None:
        kind, _, name = kind.partition(".")
    try:
        return _REGISTRY[kind][name]
    except KeyError:
        available = ", ".join(sorted(_REGISTRY.get(kind, {}))) or "（无）"
        raise PluginNotFound(f"找不到插件 {kind}.{name}；已注册的 {kind}: {available}") from None


def available(kind: str | None = None) -> dict[str, list[str]]:
    discover()
    kinds = [kind] if kind else sorted(_REGISTRY)
    return {k: sorted(_REGISTRY.get(k, {})) for k in kinds}
