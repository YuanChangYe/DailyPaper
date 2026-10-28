---
title: Selective Prediction via Conformal Prediction
tags:
- dailypaper
- topic
---

# Selective Prediction via Conformal Prediction

基于 conformal prediction / conformal risk control 的选择性预测与弃权：coverage 保证、 conformal factuality、learn-then-test、risk control、对 LLM 输出的 set-valued 预测或拒答机制。

共 1 篇。

| 日期 | 价值 | 论文 | TL;DR |
|---|---|---|---|
| 2026-10-28 | ★★★☆☆ | [Calibrated for Which Policy? Conformal Uncertainty for Fixed World Models Under Intervention](../papers/2026/10/2610.35877.md) | 在预测器固定、条件机制 P(Y/X) 不变的假设下，用 domain classifier 估计 dQ_X/dP_X 并做 weighted conformal，给出 1−α−TV 与 classifier-regret 两层覆盖下界；在 192 个 Franka 转移的受控倾斜上把 55.0% 覆盖率恢复到 90.9%。 |