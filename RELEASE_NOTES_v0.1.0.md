# DailyPaper v0.1.0

第一个可用版本。每天自动抓取 arXiv 新论文，按你的研究方向用 LLM 筛选，对最相关的几篇做全文精读，生成围绕 insight 与 novelty 的阅读笔记；本地网页浏览、审核后一键入库 Zotero、笔记直接进 Obsidian。

**这是一个框架**：请自行接入模型 API key（必需），以及 Zotero、Obsidian、GitHub、个人网站（均可选）。README 中有逐步的接入与验证清单。

## 功能

- **流水线**：arXiv RSS 抓取 → 去重 → 关键词预筛 → LLM 摘要筛选（Haiku）→ 全文获取（arXiv HTML / PDF）→ LLM 精读（Sonnet）→ Markdown / JSON / RSS 输出。每步可插拔，结果落盘可续跑。
- **精读框架**：固定 10 节（TL;DR、Problem formulation、Core insight、Novelty delta、Mechanism、Guarantees、Claim ↔ Evidence、Red flags、Research hooks、是否值得读原文），每条判断标注原文出处；按研究方向、论文类型和外部阅读 skill 自动叠加分析 lens。
- **网页**（`dailypaper app`）：发现、今日、文库、论文、审核台、搜索设置六个视图；列表带论文首页缩略图；论文页可左右分栏对照 PDF；搜索设置直接编辑研究方向并保存；可触发运行并查看日志。同一份网页可发布为静态网站。
- **Zotero**：审核通过后写入条目、标签、PDF 和精读子笔记；citekey 回写笔记；文库去重索引与兴趣画像用于个性化排序；可对文库中任意论文做反向精读。
- **Obsidian**：笔记即 Markdown；在笔记中评分、勾选入库会回流。
- **运行方式**：本地任务计划（Windows 脚本）或 GitHub Actions + GitHub Pages（定时默认关闭，按需开启）。
- **模型**：默认 Claude，支持任何 OpenAI 兼容接口；离线假模型用于调试；月度花费统计与预算提醒。

## 需要注意

- 依赖 `pymupdf<2`：2.0 在部分 Windows 环境下导入即崩溃。
- 笔记由 LLM 生成，可能有误读；它用于判断"值不值得读、该读哪里"，不能替代阅读原文。

## 安装

```bash
pip install -e ".[all]"
dailypaper check
```
