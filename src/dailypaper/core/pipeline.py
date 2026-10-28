"""按配置组装并执行 stage。

- 每个 stage 结束后把 items 写到 ``<run_dir>/<序号>_<stage id>.jsonl``，
  所以可以 ``--from <stage id>`` 从中间某一步重跑（读取上一步的检查点）。
- ``on_error: skip`` 的 stage 失败时记录错误并原样传递 items，不中断整条流水线。
- dry-run 时跳过有外部副作用的 stage（Publisher）。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from . import registry
from .config import StageSpec
from .context import RunContext
from .interfaces import Stage
from .io import read_jsonl, write_json, write_jsonl
from .models import Candidate, RunInfo, now_utc

log = logging.getLogger("dailypaper.pipeline")


class PipelineError(RuntimeError):
    pass


@dataclass
class BoundStage:
    index: int
    spec: StageSpec
    stage: Stage

    @property
    def checkpoint_name(self) -> str:
        return f"{self.index:02d}_{self.spec.id}.jsonl"


class Pipeline:
    def __init__(self, ctx: RunContext, specs: list[StageSpec]) -> None:
        self.ctx = ctx
        self.stages: list[BoundStage] = []
        ids = [s.id for s in specs]
        if len(ids) != len(set(ids)):
            raise PipelineError(f"stage id 重复: {ids}")
        for i, spec in enumerate(specs):
            if not spec.enabled:
                continue
            cls = registry.get(spec.use)
            if not (isinstance(cls, type) and issubclass(cls, Stage)):
                raise PipelineError(f"{spec.use} 不是流水线 stage")
            try:
                stage = cls(ctx, **spec.params)
            except Exception as e:
                raise PipelineError(f"stage {spec.id} ({spec.use}) 参数错误: {e}") from e
            self.stages.append(BoundStage(i, spec, stage))

    @classmethod
    def from_config(cls, ctx: RunContext, name: str | None = None) -> Pipeline:
        name = name or ctx.pipeline
        specs = ctx.config.pipeline.pipelines.get(name)
        if specs is None:
            known = ", ".join(ctx.config.pipeline.pipelines) or "（无）"
            raise PipelineError(f"pipeline.yaml 中没有名为 {name} 的流水线；已有: {known}")
        return cls(ctx, specs)

    def _position(self, stage_id: str) -> int:
        for pos, b in enumerate(self.stages):
            if b.spec.id == stage_id:
                return pos
        raise PipelineError(f"找不到 stage {stage_id}；可用: {[b.spec.id for b in self.stages]}")

    def run(
        self,
        items: list[Candidate] | None = None,
        start_from: str | None = None,
        stop_after: str | None = None,
    ) -> list[Candidate]:
        run_dir = self.ctx.run_dir
        info = RunInfo(pipeline=self.ctx.pipeline, run_date=self.ctx.run_date)
        start = 0
        if start_from:
            start = self._position(start_from)
            if start > 0:
                prev = self.stages[start - 1]
                ckpt = run_dir / prev.checkpoint_name
                if not ckpt.exists():
                    raise PipelineError(f"缺少检查点 {ckpt}，无法从 {start_from} 开始")
                items = read_jsonl(ckpt, Candidate)
                log.info("从检查点 %s 载入 %d 条", ckpt.name, len(items))
        end = self._position(stop_after) + 1 if stop_after else len(self.stages)
        items = list(items or [])

        for b in self.stages[start:end]:
            name = f"{b.spec.id} ({b.spec.use})"
            if self.ctx.dry_run and b.stage.side_effects:
                log.info("[dry-run] 跳过 %s", name)
                info.stats[b.spec.id] = {"skipped": "dry-run"}
                write_jsonl(run_dir / b.checkpoint_name, items)
                continue
            t0 = time.perf_counter()
            n_in = len(items)
            try:
                items = b.stage.run(items)
            except Exception as e:
                if b.spec.on_error == "skip":
                    log.exception("stage %s 失败，按 on_error=skip 继续", name)
                    info.stats[b.spec.id] = {"error": repr(e)}
                    write_jsonl(run_dir / b.checkpoint_name, items)
                    continue
                raise PipelineError(f"stage {name} 失败: {e}") from e
            dt = time.perf_counter() - t0
            n_sel = sum(c.selected for c in items)
            info.stats[b.spec.id] = {"in": n_in, "out": len(items), "selected": n_sel, "seconds": round(dt, 2)}
            log.info("%-14s %4d → %4d 条（已选 %d），%.1fs", b.spec.id, n_in, len(items), n_sel, dt)
            write_jsonl(run_dir / b.checkpoint_name, items)

        info.finished_at = now_utc()
        write_json(run_dir / "run.json", info.model_dump(mode="json"))
        return items
