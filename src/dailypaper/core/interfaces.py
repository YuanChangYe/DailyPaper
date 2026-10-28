"""插件接口。

流水线 stage 的统一契约：``run(items: list[Candidate]) -> list[Candidate]``。
按语义分成几类，类别只决定默认行为（例如 dry-run 时跳过 Publisher），不改变签名：

- Source     产生候选（把新论文追加到 items）
- Filter     删掉不需要的候选
- Ranker     打分、排序、标记 selected
- Enricher   为 selected 候选补充信息（全文等）
- Reader     为 selected 候选生成精读笔记
- Renderer   把结果写成 Markdown / JSON 等文件
- Publisher  有外部副作用的输出（git push、复制到 vault、审核队列、站点）

每个插件可以声明一个嵌套的 ``Params`` (pydantic) 来校验 pipeline.yaml 中的 ``with:`` 参数。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from .context import RunContext
from .models import Candidate


class NoParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Stage(ABC):
    plugin_kind: ClassVar[str] = ""
    plugin_name: ClassVar[str] = ""
    Params: ClassVar[type[BaseModel]] = NoParams
    side_effects: ClassVar[bool] = False  # True 的 stage 在 dry-run 时被跳过

    def __init__(self, ctx: RunContext, **params) -> None:
        self.ctx = ctx
        self.config = ctx.config
        unknown = set(params) - set(self.Params.model_fields)
        if unknown:  # 配置里拼错的参数名直接报错，而不是被静默忽略
            raise ValueError(f"未知参数 {sorted(unknown)}；可用参数: {sorted(self.Params.model_fields)}")
        self.p = self.Params.model_validate(params)
        self.log = logging.getLogger(f"dailypaper.{self.plugin_kind}.{self.plugin_name}")

    @abstractmethod
    def run(self, items: list[Candidate]) -> list[Candidate]:
        ...

    @staticmethod
    def selected(items: list[Candidate]) -> list[Candidate]:
        return [c for c in items if c.selected]


class Source(Stage):
    """把新候选追加到 items 后返回。"""


class Filter(Stage):
    """返回保留下来的候选。"""


class Ranker(Stage):
    """为候选打分、排序，并设置 ``selected``。"""


class Enricher(Stage):
    """为候选补充信息（例如全文）。"""


class Reader(Stage):
    """为 selected 候选生成 ``note``。"""


class Renderer(Stage):
    """把结果写成文件；不应有网络等外部副作用。"""


class Publisher(Stage):
    """有外部副作用的输出，dry-run 时跳过。"""

    side_effects = True
