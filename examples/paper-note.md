---
title: Calibrated for Which Policy? Conformal Uncertainty for Fixed World Models Under Intervention
paper_id: '2610.35877'
source: arxiv
authors:
- Ayush Mittal
- Dhruv Gupta
date: '2026-10-28'
published: '2026-08-20'
topics:
- conformal_selective
tags:
- dailypaper
- conformal_selective
- theory
paper_type: theory
value_score: 3
score: 10.54
tldr: 用域分类器估计策略偏移的密度比,做加权 conformal 校准;强偏移下覆盖率由 55.0% 恢复到 90.9%,并给出 TV 与分类器 regret 覆盖下界。
lenses:
- topic:conformal_selective
- type_theory
- evidence_discipline
- idea_review
model: claude:claude-sonnet-5-5
grounding: structure-grounded
schema_version: 2
schema_ok: true
url_abs: https://arxiv.org/abs/2610.35877
url_pdf: https://arxiv.org/pdf/2610.35877v1
status: unread
rating: null
zotero_approve: true
zotero_key: L2C6MABE
citekey: mittal2026calibrated
zotero_link: zotero://select/library/items/L2C6MABE
---

# Calibrated for Which Policy? Conformal Uncertainty for Fixed World Models Under Intervention

> **作者** Ayush Mittal, Dhruv Gupta · **来源** [2610.35877v1](https://arxiv.org/abs/2610.35877) · [PDF](https://arxiv.org/pdf/2610.35877v1) · **方向** Selective Prediction via Conformal Prediction · **类型** theory · **价值** ★★★☆☆

> **一句话总结** 用域分类器估计策略偏移的密度比,做加权 conformal 校准;强偏移下覆盖率由 55.0% 恢复到 90.9%,并给出 TV 与分类器 regret 覆盖下界。

> **核心直觉** 策略改变后,物理转移机制不变,变的只是查询分布,所以只需用不依赖结果的密度比重新加权校准分数,世界模型不用重训。

> **要不要深入** 快速了解即可:前 8 节够用。理论是 weighted conformal 加 TV 与 Pinsker 的组合,实验是单个 192 点池上的受控倾斜;对 agent_rsi 的启发在第 13 节。

## 1. 摘要翻译

改变部署策略可能使世界模型的不确定性失效,而物理转移机制和预测器都没有变。对于一步预测,如果源分布与目标分布共享条件机制 $P(Y\mid X)=Q(Y\mid X)$,那么这次干预就由结果之前的测度变换 $w(X)=\mathrm{d}Q_X/\mathrm{d}P_X$ 所识别。对于由策略控制的动力学,这个比值可以分解为状态占用(state-occupancy)项与动作倾向(action-propensity)项。加权 conformal 校准把 marginal coverage 迁移到目标分布;使用估计的比值 $\widehat{w}$ 时,

$$\mathbb{P}_{Q}\{Y\in\mathcal{C}_{\widehat{w}}(X)\}\geq 1-\alpha-\operatorname{TV}(Q,\widehat{Q})\geq 1-\alpha-\mathbb{E}_{P}|w-\widehat{w}|.$$

源域与目标域分类器的总体 log-loss regret 可以界住同一个比值误差。设 $h$ 是其截断后的目标域后验,$\rho$ 是目标域先验,$\Delta_{\log}(h)$ 是总体 log-loss regret,则

$$\mathbb{E}_{P}|w-\widehat{w}_{h}|\leq\frac{1}{\rho\gamma}\sqrt{\frac{\Delta_{\log}(h)}{2}}.$$

一次精确的消去使依赖关系从通用 odds 映射界的 $\gamma^{-2}$ 量级改进为 $(\rho\gamma)^{-1}$。实验使用公开的 DINO 特征世界模型和真实 Franka 轨迹。在最强的受控状态-动作偏移下,普通 90% conformal 集合只覆盖 55.0%,而 oracle 校准与学得比值校准分别覆盖 91.0% 与 90.9%。学得的代理分布与目标分布的总变差距离在 0.9% 以内。校准是相对于部署分布而言的:模型可以保持不变,而校准跟随干预。我们用 Lean 4 机器检验了分类器 regret 论证中用到的有限支撑代数恒等式与因果恒等式,包括其中的覆盖率组合步骤。

## 2. 研究问题与价值

**Setting。** 世界模型 $m$ 在给定当前状态 $S$ 和动作 $A$ 时预测下一帧(或其潜在表示)$Y$。令 $X=(S,A)$,用残差 $r_m(X,Y)$ 作 nonconformity score,用 split conformal 得到预测集。校准数据来自采集策略 $\pi_b$,部署时由另一个策略 $\pi_t$ 选动作【§2】。约束是:预测器冻结,不重训;目标域只有无标签的协变量 $X$,结果 $Y$ 事后才出现;校准数据 i.i.d.【§3】。

**问题。** 策略一变,$(S,A)$ 的分布变了,即使 $K(Y\mid S,A)$ 不变,残差的边缘分布也变了,源域分位数不再给出目标域的覆盖率。论文要回答三件事:何时偏移是可识别的;识别出的比值估计有误差时覆盖率损失多少;能否仅用无标签协变量估计这个比值【Abstract】【§1】。

**价值。** 在机器人规划中,planner 会主动挑选模型预测好的动作,这使部署分布系统性偏离采集分布,校准因此失效。从科学上看,论文把"世界模型校准与否"改写成"相对于哪个测度校准"。〔分析〕对 LLM/agent 方向,这个视角可以平移到自我改进场景:agent 的策略或生成分布改变后,固定的置信度门控应相对于新分布重新校准。但机制不变的前提在那里要另行检验(见第 9 节)。

## 3. 已有工作与不足

以下相关工作均依据原文 related work,未独立核实。

- **Conformal 在偏移下**:split conformal 在 exchangeability 下给出有限样本 marginal coverage(36; 20)。Weighted conformal 在协变量偏移且似然比已知时保持覆盖(34)。更一般的非 exchangeable 与自适应方法见(2; 11)。倾向得分加权的 conformal 用于静态处理效应反事实(21)。不足:这些工作假设比值已知,或没有针对策略干预的动力系统给出比值的识别方式。
- **Off-policy 校正**:stationary-distribution 校正直接估计占用比(25; 22),轨迹级重要性比随时间连乘,方差随 horizon 恶化(18)。这些工作针对回报估计,没有针对预测集的覆盖率。
- **密度比估计**:可直接估计,也可通过源/目标分类器估计(31; 3);proper loss 与密度比的联系见(23)。不足:通用 odds 映射 Lipschitz 界含两个分母因子,即 $\gamma^{-2}$,而且没有接到 conformal 覆盖率上。
- **世界模型与保形安全**:世界模型工作(12; 14; 38; 1; 32; 28 等)侧重预测与规划本身;conformal 已用于控制器、轨迹预测和 latent safety filter(6; 39; 30),但不处理"策略改变使校准失效"。

〔分析〕本文的空档在于把这些已有部件串起来:识别、加权 conformal 迁移、分类器 regret 到覆盖率的端到端界。组件本身都来自已有文献,因此 novelty 主要属于"新设定下的组合加上一个常数改进",是否有更近的先例,不确定,需要查新。

## 4. 作者的思考路径与核心直觉

〔分析〕以下是我的重建,不是作者原话。

1. 已知失败模式:世界模型在采集策略下校准,换成 planner 后覆盖率下降。常见反应是重训模型或做在线自适应。
2. 注意到:世界模型学的是 $K(Y\mid X)$,换策略只改变 $X$ 的分布。若条件机制不变,模型本身没错,错的是校准所用的测度。
3. 由此,联合似然比里 $Y$ 的部分消去,只剩 $X$ 的比值(Prop. 1),它在 $Y$ 到达之前就可以计算。对策略动力学,它进一步分解为占用比乘倾向比(Prop. 2)。
4. weighted conformal 恰好需要这个比值。比值未知时,可以用无标签的源/目标协变量训练二分类器,通过 Bayes 公式把后验转成比值。
5. 剩下的问题是比值有误差时覆盖率损失多少。把加权结果看成是对归一化代理分布 $\widehat Q$ 精确成立,再用 TV 把事件概率搬回 $Q$。分类器的训练目标是 log-loss,所以再用 Pinsker 把 regret 连到 TV。

**核心思想**:校准是相对于查询分布的统计陈述。策略变了,模型和机制不用动,只需用结果之前就能得到的密度比重新加权。比值估得多准,覆盖率就最多损失多少。

## 5. 方法与完整流程

**例子**:冻结的 dino_wm_droid 预测下一帧 DINOv2 特征;源策略下的 192 个 Franka 一步转移;目标策略偏向平移幅度较大的动作【§5.1】。

1. **输入**:$n=500$ 个源校准三元组 $(x_i,y_i)$;独立的源/目标协变量用于拟合比值;水平 $\alpha$【Alg. 1】。
2. **确定 $X$**:选出能使 $P(Y\mid X)=Q(Y\mid X)$ 成立的变量。实验里 $X$ 是两帧上下文加七维位姿差动作,偏移统计量 $g_i$ 是平移幅度的经验 midrank【§5.1–5.2】。
3. **拟合域分类器**:拟合 $h(x)=\Pr(D=1\mid x)$,用逻辑回归,后验截断到 $[0.05,0.95]$。
4. **转成比值**:$\widehat w_h(x)=\frac{1-\rho}{\rho}\frac{h(x)}{1-h(x)}$【Eq. 7】。这是 Bayes 公式:后验 odds 乘先验 odds 的倒数等于密度比。
5. **算分数**:$R_i=r_m(x_i,y_i)$,即预测潜变量与实际下一帧编码的 RMSE。
6. **加权分位数**:按 $\widehat w(x_i)$ 对分数加权,再给测试点的权重放在 $+\infty$ 处,取 $(1-\alpha)$ 分位数 $q$,输出 $\mathcal C(x)=\{y:r_m(x,y)\le q\}$【Eq. 3】。测试点质量放在 $+\infty$ 是有限样本保证的来源。
7. **输出**:目标分布下的预测集,覆盖率至少为 $1-\alpha-\mathrm{TV}(Q,\widehat Q)$【Eq. 5】。

关键公式有两个。其一是 $\mathrm{TV}(Q,\widehat Q)\le \mathbb E_P|w-\widehat w|$,其中 $\widehat Q$ 是 $\widehat w$ 归一化后的分布。其二是 $\mathbb E_P|w-\widehat w_h|\le \frac{1}{\rho\gamma}\sqrt{\Delta_{\log}(h)/2}$。

组件对应不足:Prop. 1–2 解决"比值是否可识别";weighted conformal 加 TV 解决"比值有误差时的覆盖率";Lemma 4 和 Pinsker 解决"用分类器训练目标界住比值误差"。超参包括截断 $\gamma=0.05$ 和 clip(5) 这个对照【§5.2】。

## 6. 核心数学推导

**背景**:Radon–Nikodym 导数 $\mathrm{d}Q/\mathrm{d}P$ 是"每单位 $P$ 质量对应多少 $Q$ 质量"的权重函数,存在的条件是 $Q\ll P$(绝对连续)。总变差 $\mathrm{TV}(Q,\widehat Q)=\sup_E|Q(E)-\widehat Q(E)|$ 是任意事件概率的最大差。Pinsker 不等式给出 $\mathrm{KL}\ge 2(\text{TV})^2$,二元情形即 $\mathrm{KL}(\mathrm{Bern}(\eta)\|\mathrm{Bern}(h))\ge 2(\eta-h)^2$。

**保证类型**:marginal coverage,对校准样本与测试点联合取概率;有限样本;期望意义;要求校准数据 i.i.d.、$\widehat w$ 与校准样本独立、机制共享、支撑包含【Prop. 3】【§9】。不是 conditional coverage。

**推导步骤**:

1. **Prop. 1**:若 $P=P_X\otimes K$ 且 $Q=Q_X\otimes K$,对有界 $f$ 先对 $y$ 积分,再把 $Q_X$ 换成 $w_X P_X$,得 $\int fq\,\mathrm dQ=\int w_X f\,\mathrm dP$。因此联合比值就是 $w_X$。含义:共享条件核使 $Y$ 的部分抵消。这是标准的协变量偏移恒等式,很简短。
2. **Prop. 3**:以 $\widehat w$ 为条件,把 $\widehat Q$ 当作目标,加权 conformal 对 $\widehat Q$ 给出 $\ge1-\alpha$(34)。覆盖事件是固定的可测集,换成 $Q$ 至多损失 $\mathrm{TV}$。再把 $2\mathrm{TV}(Q,\widehat Q)=\mathbb E_P|w-\widehat w/Z|$,用三角不等式加 $|Z-1|\le\varepsilon_1$,得 $\mathrm{TV}\le\varepsilon_1$【Eq. 6】。
3. **Lemma 4**:$\dfrac{\mathrm dP_X}{\mathrm dM}=\dfrac{1-\eta}{1-\rho}$。$w-\widehat w_h$ 含因子 $\frac{1}{(1-\eta)(1-h)}$,换到混合分布 $M$ 时分子的 $1-\eta$ 恰好消去,得 $\mathbb E_P|w-\widehat w_h|=\frac1\rho\mathbb E_M\frac{|\eta-h|}{1-h}$。我手算验证了这一步成立。
4. **Prop. 5**:用 $1-h\ge\gamma$,再 Cauchy–Schwarz 和 Pinsker,$\mathbb E_M|\eta-h|\le\sqrt{\mathbb E_M(\eta-h)^2}\le\sqrt{\Delta_{\log}/2}$。$\rho=1/2$ 时 $2/\gamma\cdot\sqrt{\Delta/2}=\sqrt{2\Delta}/\gamma$,与 Eq. 11 一致。
5. **Appendix A**:构造 $W_1,W_2\to0$ 而 TV 与 $\chi^2$ 固定的反例,说明几何上接近的分布仍可能使加权校准失效。

**紧性**:原文没有给下界或匹配例子。TV 作为事件迁移惩罚对"任意事件"是尖锐的,但对具体的覆盖事件可能很松。〔分析〕$\widehat Q$ 与 $Q$ 的 TV 是最坏事件,而覆盖事件只是其中一个,所以实际覆盖率往往远高于下界(Table 2 的 89.2% 对 90.9%)。

Lean 形式化只覆盖有限支撑的代数部分,weighted conformal 的核心不等式作为前提直接假设【App. E】。

## 7. 实验设计与结论

**问题 1:受控偏移会不会使普通校准失效?** 在 192 个真实转移池上,用 $e^{\pm\lambda g}$ 对称倾斜构造 $P_\lambda,Q_\lambda$,$\lambda\in\{0,0.5,1,1.5\}$【Eq. 13】。名义 90% 下,普通校准覆盖 90.2%、81.6%、69.0%、55.0%【§6.1】。证据:中。方向很明显,但偏移统计量与分数的 Pearson 相关达 0.800【App. D.1】,这是挑选出来使影响明显的设置。

**问题 2:学得的比值能否追上 oracle?** oracle 为 90.5%、91.8%、91.0%,学得为 90.6%、91.8%、90.9%【§6.1】。证据:弱到中。分类器的函数形式恰好正确指定(log 比值对 $g$ 线性)【§5.2】,所以这只验证了"模型正确时有效"。没有错设定、高维 $X$、噪声特征的对照。每个 $\lambda$ 重复 50 次,有 Student-$t$ 区间,方差报告比较充分。但 192 点采样放回,独立重复更像是对同一个 192 点池的重抽样,真实的 episode 间变异只在附录用 15 个 episode 的 cluster bootstrap 提到,正文未给数字。

**问题 3:理论界是否成立、是否紧?** 观察覆盖率高于 exact-TV 下界(89.1% 至 89.3%)【Table 2】。regret 下界 66.5% 至 72.4%,明显松。证据:中。但这里的"下界成立"几乎是必然的,因为 TV 只有 0.7–0.9%,界本身约等于 $1-\alpha$。该界在 TV 较大的情形没有被检验,只有 clip(5) 的一个点。

**问题 4:截断是否有代价?** clip(5) 在 $\lambda=1.5$ 把 ESS 从 73 提到 125,TV 从 0.9% 升到 18.7%,覆盖率从 90.9% 降到 83.8%【§6.3】。证据:强(有消融,对应理论预测),但只是一个截断阈值。

**问题 5:代价是什么?** ESS 从 500 降到 73,半径从 1.183 增到 1.281【Table 1】。这是诚实的代价报告。

**公平性**:baseline 是普通 conformal、oracle、学得、clip,预测器相同。缺失的 baseline:Barber 等的非 exchangeable 加权方法、自适应 conformal(ACI)、doubly robust 校准、对目标域直接用少量标签重新校准。

**数字核对**:我用 $\rho=1/2,\gamma=0.05$ 和 Table 2 的 $\Delta_{\log}$ 代入 Eq. 10,算出 $\lambda=0$ 的 regret 下界约 66.2%、$\lambda=1.5$ 约 64.3%,而表中为 70.8% 和 66.5%。差异在 2 至 5 个百分点,且并非常数。原文说下界松来自全局 $\gamma$,但没给出表中数字的具体计算方式。这些 regret 下界的数字因此不确定,需要作者给出所用的 $\rho,\gamma$ 与公式。

## 8. Take-aways

五句版:(1) 策略改变时,保持预测器不变、改变校准测度是合理的操作,前提是条件机制共享。(2) 一步预测里联合比值只依赖结果前变量,策略动力学下分解为占用比乘倾向比。(3) 比值有误差时,加权 conformal 的覆盖损失被 $\mathrm{TV}(Q,\widehat Q)\le\mathbb E_P|w-\widehat w|$ 界住。(4) 域分类器 log-loss regret 经 $(\rho\gamma)^{-1}\sqrt{\Delta/2}$ 界住比值误差,但数值上比 exact-TV 松得多。(5) 192 点真实 Franka 池的受控倾斜实验里,覆盖率从 55.0% 恢复到 90.9%,而截断提高 ESS 却使覆盖降到 83.8%。

三句版:策略改变让校准失效而机制不变,用不依赖结果的密度比重新加权即可。覆盖损失可由比值误差与分类器 regret 显式界住。实验验证限于正确指定的单一受控设置。

一句版:在"机制不变"的前提下,世界模型的 conformal 校准应相对于部署分布,用域分类器估比值并加权,覆盖损失由比值误差控制;对 LLM/agent 研究者的意义是提供一个"策略改变后重新校准选择性预测门控"的最小理论模板。

## 9. 最脆弱的假设

**共享条件机制 $P(Y\mid X)=Q(Y\mid X)$,且 $X$ 包含足够信息使之成立。** 出现在 Prop. 1、Prop. 2、Prop. 3 与 §9。正面支撑:世界模型预测的是物理转移,理论上与策略无关;实验里该假设由构造保证,因为每个索引的目标与分数是确定的,只改变索引的抽样权重【§5.2】。

没有检验的情形:真实策略变化常伴随机制变化,例如策略把系统带到模型没见过的状态区域,此时有效 $X$(含未观测的接触状态、遮挡、隐变量)不足以使条件分布相同;$X$ 若只是两帧加动作,隐变量会使 $P(Y\mid X)$ 随策略而变。原文承认机制偏移需要额外数据或方法【§9】,但没有给出检验该假设的方法。假设不成立时,加权 conformal 对 $\widehat Q$ 的保证仍在,但 $\widehat Q$ 与 $Q$ 的差不再是 TV 所能表示的,覆盖率下界失去意义。更糟的是,域分类器看不到这种偏移,因为它只看 $X$,会报告很小的 regret。

## 10. 一周最小复现

**目标**:验证最脆弱的假设失效时,域分类器的 regret 与覆盖率会脱钩。

1. **数据**:JEPA-WMs 的 dino_wm_droid 与其 Franka 评测数据(192 个转移,若有更多 DROID 一步转移则越多越好)。
2. **实现**:复用论文的 Eq. 3 加权 conformal(约 50 行 Python)、逻辑回归域分类器。自写机制偏移:对目标域的下一帧特征加动作相关的扰动(如把动作幅度大的样本的真值加固定偏置),使 $Q(Y\mid X)\ne P(Y\mid X)$,强度设三档。
3. **对照**:(a) 论文原设置;(b) 机制偏移加协变量偏移;(c) 仅机制偏移;(d) 在 (b) 里加入 $n_{\text{lab}}=50$ 个目标标签的重校准。
4. **指标**:目标覆盖率、$\Delta_{\log}$、论文下界、ESS,各 50 次重复,按 episode 分层重抽样。
5. **预期**:(b)(c) 下覆盖率低于 $1-\alpha-\mathrm{TV}$ 下界,而 $\Delta_{\log}$ 仍很小;(d) 能部分恢复。
6. **判据**:出现下界被违反且 $\Delta_{\log}$ 仍小,说明理论界对机制偏移没有预警能力(支持审稿批评);若仍未违反,说明脆弱性在这个设置下被高估。另外可以检查原论文 Table 2 的 regret 下界复算。

## 11. 审稿人的反例设计

**反例 1(隐变量机制偏移)**:机器人推物体,摩擦系数是隐变量。策略 $\pi_t$ 倾向选需要大力的动作,而这些动作在高摩擦物体上出现更多。此时 $X=(S,A)$ 不含摩擦,$P(Y\mid X)$ 在两个策略下不同。替代解释:域分类器看到的只是 $X$ 的偏移,不会发现隐变量混杂。预测:加权校准覆盖率低于下界且分类器 regret 很小。指向:Prop. 1 的共享核假设。最小修补:加入机制检验(目标域少量标签的加权残差检验)作为适用前提。可信度:高,这是该设定下的常见情况。

**反例 2(实验只验证了理想情形)**:§5.2 的分类器函数形式恰好正确,$X$ 仅为一维标量 $g$,偏移是构造的指数倾斜。对 $X$ 为 DINO 特征(数百维)的真实策略偏移,密度比估计会出现过拟合、支撑不重叠,ESS 极低。预测:$\Delta_{\log}$ 不会是 $10^{-5}$ 量级,regret 界会接近 vacuous。可信度:高。最小修补:报告错设定与高维特征下的结果。

**反例 3(多步)**:论文只做一步 marginal。在多步 rollout 中,下一步的 $X$ 依赖上一步预测误差,exchangeability 与独立性都失效,占用比也要估计。〔分析〕作者已承认这一局限【§9】,可修复,但对"世界模型规划"这个宣传场景是重要缺口。

**批评 4(novelty)**:Prop. 3 是 weighted conformal 加 TV 的两步组合,类似 Barber 等"coverage gap 由 TV 控制"的已知思路(依据我的背景知识,未核实);$(\rho\gamma)^{-1}$ 的改进只是对分母因子的一次消去。这不是缺陷,但降低理论贡献的权重。

## 12. 后续研究调研

联网检索只找到了论文本身的 arXiv 页面(https://arxiv.org/html/2610.35877),没有找到扩展、反驳或复现的工作。检索次数随后用尽,后续检索未能完成,因此"未找到后续工作"不等于不存在。

以下是我根据训练知识回忆的相近前序工作,不是后续工作,未联网核实,仅供查新起点:

- Tibshirani et al., *Conformal Prediction Under Covariate Shift*(2019):本文依赖的 weighted conformal 基础。
- Barber et al., *Conformal Prediction Beyond Exchangeability*(2023):给出非 exchangeable 下的覆盖间隙界,与本文 TV 惩罚最接近,需要对比。
- Lei & Candès, *Conformal Inference of Counterfactuals and Individual Treatment Effects*(2021):倾斜加权的因果用法。
- Yang, Kuchibhotla, Tchetgen Tchetgen, *Doubly Robust Calibration of Prediction Sets under Covariate Shift*(2022):对比值估计误差更稳健,是本文可对比的 baseline。

## 13. Follow-up idea

**候选想法(待验证,需要查新)**:在自我改进或换策略的部署过程中,用部署后陆续到达的标签,在线检验"机制共享"假设,失败就从密度比加权切换到更稳健的校准。

**驱动需求**:自我改进的 agent、机器人规划者需要选择性预测门控(接受/弃权)。论文的方法需要 $P(Y\mid X)$ 不变,而 agent 自我修改会改变它。世界模型下一步的结果几乎立即可得,所以目标标签其实是廉价的,论文却只用无标签协变量。

**借用工具**:conformal test martingale(Vovk 等)与加权 exchangeability 检验,以及 adaptive conformal inference。

**第一个实验**:在 JEPA-WMs/DROID 上,构造序列流:先是纯策略偏移,然后在第 $T$ 步注入机制偏移(如下一帧特征加动作相关偏置)。对照:(a) 论文方法固定加权;(b) ACI;(c) 加权加 test martingale 触发后切到 ACI。指标:全流程的覆盖率误差、检测延迟、触发后的半径。成功的标准:(c) 在机制偏移后覆盖率偏差小于 (a),在纯策略偏移段误报率低于设定水平且半径小于 (b)。

**与已有工作的区别**:论文只在假设成立时保证覆盖;这里把假设本身变成可检验、可触发回退的对象,并让标签流参与校准。

**可能失败的原因**:(1) 有效样本量低(论文里 ESS 为 73),加权检验的功效不足,检测延迟过长。(2) 无法区分比值估计误差和机制偏移,两者都会触发,误报增多。(3) 一步结果延迟或有遮挡时标签并不廉价。(4) 多步依赖破坏 martingale 所需的 exchangeability。

---

<details><summary>原文摘要</summary>

Changing a deployment policy can invalidate a world model's uncertainty while leaving the physical transition mechanism and predictor unchanged. For one-step prediction, if source and target laws share the conditional mechanism $P(Y|X)=Q(Y|X)$, then the intervention is identified by the pre-outcome change of measure $w(X)=dQ_X/dP_X$. For policy-controlled dynamics this ratio factors into state-occupancy and action-propensity terms. Weighted conformal calibration transports marginal coverage to the target law; with an estimated ratio $\hat w$, $P_Q\{Y \in C_{\hat w}(X)\} \ge 1-α-TV(Q,\hat Q) \ge 1-α-E_P|w-\hat w|$. Population log-loss regret for a source-versus-target domain classifier bounds the same ratio error. If $h$ is its clipped target posterior, $ρ$ is the target-domain prior, and $Δ(h)$ is population log-loss regret, then $E_P|w-\hat w_h| \le (1/(ργ))\sqrt{Δ(h)/2}$. An exact cancellation improves the dependence from a generic odds-map bound of order $γ^{-2}$ to $(ργ)^{-1}$. Our experiments use a released DINO-feature world model and physical Franka trajectories. Under the strongest controlled state-action shift, ordinary 90% conformal sets cover 55.0%, whereas oracle and learned-ratio calibration cover 91.0% and 90.9%. The learned surrogate remains within 0.9% total variation of the target law. Calibration is relative to the deployment law: the model can remain fixed while calibration follows the intervention. We machine-check in Lean 4 the finite-support algebraic and causal identities used in the classifier-regret argument, including its coverage-composition step.

</details>

<small>精读模型 claude:claude-sonnet-5-5 · 全文来源 arxiv_html（structure-grounded） · lens: topic:conformal_selective, type_theory, evidence_discipline, idea_review · 筛选理由：核心是 conformal 覆盖保证在策略分布偏移下的迁移，属于 conformal 方向的直接相关工作；但对象是机器人世界模型的一步预测而非 LLM 输出或 agent 决策，因此与 LLM/agent 方向只有间接关联。</small>