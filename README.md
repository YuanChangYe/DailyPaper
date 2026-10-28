# DailyPaper

每天自动从 arXiv 抓取新论文，先用 LLM 按你的研究方向筛选，再对最相关的几篇做全文精读，生成围绕 **insight 与 novelty** 的阅读笔记。结果同时输出到三处：

- **网站**：GitHub Pages，不需要服务器
- **Obsidian**：与网站共用同一份 Markdown，用 Git 同步
- **Zotero**：只有你在审核页点按钮确认后才入库

默认关注的方向有五个，都在 `config/topics.yaml` 中配置，可以随时修改：
- LLM / RAG 的 uncertainty quantification
- Agent 的 uncertainty quantification
- 基于 conformal prediction 的 selective prediction
- 结合 selective prediction 的 Agent RSI
- Multi-agent trustworthiness

---

## 目录

1. [功能一览](#1-功能一览)
2. [本地快速开始](#2-本地快速开始)
3. [代码框架](#3-代码框架)
4. [功能详解](#4-功能详解)
5. [部署到 GitHub](#5-部署到-github不需要服务器)
6. [扩展指南](#6-扩展指南)
7. [命令速查](#7-命令速查)
8. [开发与测试](#8-开发与测试)
9. [与最初设计计划的差异](#9-与最初设计计划的差异)

---

## 1. 功能一览

| 功能 | 说明 | 入口 |
|---|---|---|
| 每日抓取 | arXiv 每日公告 RSS，一次请求拿到当天全部新论文（含 cross-list） | `source.arxiv_rss` |
| 去重 | 跳过处理过的论文和已在 Zotero 中的论文 | `filter.seen`、`filter.zotero_index` |
| 关键词预筛 | 本地正则，不花钱；把几百篇缩到几十篇 | `filter.keyword` + `topics.yaml` |
| LLM 筛选 | Haiku 读标题和摘要，按方向打分、判断论文类型和 novelty、建议 lens | `ranker.llm_screen` |
| 个性化排序 | 综合你的审核决定、Obsidian 评分和 Zotero 文库兴趣画像 | `integrations/obsidian/feedback.py`、`integrations/zotero/profile.py` |
| 全文获取 | arXiv HTML（保留章节和 LaTeX）→ PDF → 摘要，依次尝试 | `enricher.fulltext` |
| 精读 | Sonnet 按 base 框架输出 10 节笔记，并叠加自适应 lens（方向、论文类型、外部 skill） | `reader.deep_read` + `prompts/` + `lenses.yaml` |
| 输出 | 单篇笔记、每日日报、方向索引、首页、JSON API、RSS | `renderer.markdown`、`renderer.site_index` |
| 审核 | 本地网页，三个按钮：加入 Zotero / 稍后 / 不要；支持批量 | `dailypaper app` |
| Zotero 联动 | 入库（含 PDF、标签、精读子笔记）、citekey 回写、去重索引、兴趣画像、反向精读 | `integrations/zotero/` |
| 网站 | MkDocs 独立站；预留站点接口，可接入你自己的 Hugo / Jekyll / 任意网站 | `mkdocs.yml`、`integrations/site/` |
| 定时运行 | GitHub Actions 每个工作日运行，自动提交并发布网站 | `.github/workflows/daily.yml` |
| 花费统计 | 按月累计 API 花费，超过预算的 80% 时警告 | `llm/usage.py` |

---

## 2. 本地快速开始

需要 Python 3.10+。

```bash
cd D:\Python\DailyPaper
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[all]"
```

把 `.env.example` 复制为 `.env`，填入 `ANTHROPIC_API_KEY=...`。`.env` 已加入 `.gitignore`，不会被提交。

**用 Claude Max 附带的 API 额度**：Max 套餐每月附带 API 额度（Max 5x $100，Max 20x $200），当月不用会过期，不能累积。领取步骤：
1. 在 [Claude Console](https://platform.claude.com) 新建一个 organization。
2. 在 Claude 账号的计费设置里把它关联到 Max 套餐。额度会显示在 Console → Billing → Promotional credits，新订阅需满 7 天才能领取。
3. 在 Console → API Keys 创建 key，填进 `.env`。

交互式使用 Claude Code 不扣这部分额度。详见[官方说明](https://platform.claude.com/docs/en/about-claude/api-credits-for-subscribers)。

```bash
dailypaper read 2610.35877            # 精读一篇指定论文（arXiv ID 或 Zotero 条目 key）
dailypaper run                        # 跑一次每日流水线
dailypaper run --provider fake        # 不调用真实模型，离线跑通全流程
mkdocs serve                          # 本地预览网站 http://127.0.0.1:8000
dailypaper app                        # 打开审核页（Zotero 联动）
```

**成本参考**（2026-10 价格）：
- 精读一篇约输入 2 万、输出 1 万 token，用 Sonnet 5.5 约 $0.14。
- 摘要筛选用 Haiku 5.5，花费可以忽略。
- 每天精读 5 篇约 $0.7，一个月的工作日约 $15，在 Max 额度之内。
- 相同的 prompt 会缓存在 `.cache/llm/`，重跑不会重复花钱。

---

## 3. 代码框架

### 3.1 分层设计

```
┌───────────────────────────────────────────────────────────────────────────┐
│  入口     cli.py（命令行）        app/（本地审核助手，FastAPI）             │
├───────────────────────────────────────────────────────────────────────────┤
│  联动     integrations/  zotero · site · obsidian · review（审核队列）      │
├───────────────────────────────────────────────────────────────────────────┤
│  插件     plugins/  sources · filters · rankers · fulltext · readers ·     │
│                     renderers · publishers                                 │
│           llm/      anthropic · openai_compat · fake（+ 缓存/重试/用量）     │
│           prompting/ lens 适配器 · lens 选择器 · prompt 组合器              │
├───────────────────────────────────────────────────────────────────────────┤
│  核心     core/  models · interfaces · registry · pipeline · config ·      │
│                  context · io · state                                      │
└───────────────────────────────────────────────────────────────────────────┘
       ▲ 依赖方向：上层依赖下层；core 不认识任何具体插件、模型厂商或外部系统
```

设计原则：

1. **核心层只定义接口**。数据源、过滤、排序、全文、精读、渲染、发布都是插件；LLM 厂商、lens 来源、Zotero 后端、网站框架也是插件。
2. **配置驱动**。流水线由 `config/pipeline.yaml` 组装。增删、重排模块只改配置，不改代码。
3. **统一契约**。所有流水线 stage 都是 `run(items: list[Candidate]) -> list[Candidate]`，因此可以任意插入、删除和重排。
4. **每一步都落盘**。每个 stage 的输出写到 `.cache/runs/<日期>_<流水线>/<序号>_<stage>.jsonl`，可以 `--from <stage>` 从中间重跑。
5. **故障隔离**。单篇论文精读失败不影响其他论文；stage 可配置 `on_error: skip`；插件缺少可选依赖时只影响它自己。
6. **兼容性**。Python 3.10–3.13，Windows 和 Linux 都能运行；输出是纯 Markdown 加 YAML frontmatter，MkDocs、Obsidian、Hugo 都能直接读取。

### 3.2 一篇论文如何流过流水线

流水线里流转的单位是 `Candidate`（定义在 `core/models.py`），每个 stage 往里填写自己负责的字段：

| stage（daily 流水线） | 插件 | 读取 | 写入 `Candidate` 的字段 |
|---|---|---|---|
| fetch | `source.arxiv_rss` | arXiv RSS | `paper`（标题、摘要、作者、分类、链接） |
| dedupe | `filter.seen` | `data/seen.json` | （过滤掉见过的） |
| in_zotero | `filter.zotero_index` | `data/zotero_index.json` | （过滤掉文库里已有的） |
| prefilter | `filter.keyword` | `topics.yaml` | `keyword_hits`、`score_parts.keyword` |
| screen | `ranker.llm_screen` | 标题+摘要、反馈、兴趣画像 | `screen`（各方向分数、类型、novelty、理由、建议 lens）、`score`、`selected` |
| fulltext | `enricher.fulltext` | arXiv HTML / PDF | `fulltext`（缓存路径、来源、溯源级别） |
| read | `reader.deep_read` | 全文 + 选中的 lens | `note`（10 节正文、TL;DR、核心 insight、价值分、所用 lens） |
| render | `renderer.markdown` | | `note_path`；写出单篇笔记和日报 |
| site_index | `renderer.site_index` | | 写出 `api/*.json`、`feed.xml`、首页、方向页 |
| review_queue | `publisher.review_queue` | | 追加到 `data/review_queue.json` |
| mark_seen | `publisher.mark_seen` | | 写入 `data/seen.json` |
| site / obsidian / git | `publisher.site` / `local_copy` / `git_commit` | | 同步到网站 / vault / 提交推送 |

`publisher.*` 类的 stage 有外部副作用，`--dry-run` 时全部跳过。

### 3.3 目录与文件说明

```
D:\Python\DailyPaper
├─ pyproject.toml                 包定义（src 布局）、依赖、可选依赖组、命令行入口、ruff/pytest 配置
├─ mkdocs.yml                     网站配置（Material 主题、搜索、标签、MathJax 公式）
├─ .env.example                   环境变量模板（复制为 .env 填 key；.env 不进 git）
├─ .github/workflows/
│  ├─ daily.yml                   每个工作日：运行流水线 → 提交 docs/ data/ → 构建并发布 Pages
│  └─ ci.yml                      代码检查 + 测试（Python 3.10 / 3.12 / 3.13，不调用付费 API）
│
├─ config/                        ★ 所有可调参数都在这里
│  ├─ pipeline.yaml               流水线：daily（每日）/ read（指定论文）/ backfill（补抓历史）
│  ├─ topics.yaml                 研究方向：描述、强关键词、语境词、排除词、权重、精读镜头问题
│  ├─ llm.yaml                    模型服务与角色（screen=Haiku，read=Sonnet）、价格、月度预算
│  ├─ lenses.yaml                 精读 lens 目录与选择规则、token 预算
│  └─ settings.yaml               路径、语言、网站、Zotero、Obsidian、本地助手
│
├─ prompts/                       ★ prompt 模板（Jinja2），改 prompt 不用改代码
│  ├─ base/
│  │  ├─ deep_read.system.md.j2   精读 base 框架：任务、证据纪律、10 节输出结构、lens 插槽
│  │  ├─ deep_read.user.md.j2     精读输入：元数据、筛选结论、全文
│  │  ├─ screen.system.md.j2      筛选：方向说明、打分标准、lens 目录、JSON 输出格式
│  │  └─ screen.user.md.j2        筛选输入：一批论文的 JSON
│  └─ lenses/type_*.md            论文类型 lens：method / theory / benchmark / empirical / survey / position
│
├─ skills/                        外部阅读 skill 的文本副本（运行 dailypaper sync-skills 后生成）
│
├─ src/dailypaper/
│  ├─ cli.py                      命令行：run / read / app / review / zotero-* / sync-skills / list-*
│  │
│  ├─ core/                       ── 核心层：只有抽象，没有具体实现 ──
│  │  ├─ models.py                数据模型：Paper、ScreenResult、FulltextInfo、ReadingNote、Candidate、RunInfo
│  │  ├─ interfaces.py            Stage 基类，及 Source / Filter / Ranker / Enricher / Reader / Renderer / Publisher
│  │  ├─ registry.py              插件注册表：@register(kind, name)、自动发现内置插件、entry points
│  │  ├─ pipeline.py              按配置组装 stage；检查点落盘、--from 断点续跑、错误隔离、dry-run
│  │  ├─ config.py                读取 config/*.yaml（支持 ${ENV} 引用）为带类型的配置对象
│  │  ├─ context.py               RunContext：一次运行的日期、配置、命令行输入、共享服务
│  │  ├─ io.py                    原子写文件、JSON/JSONL、Markdown frontmatter 读写
│  │  └─ state.py                 SeenStore：已处理论文记录（自动清理 90 天前的）
│  │
│  ├─ llm/                        ── 模型层 ──
│  │  ├─ base.py                  LLMProvider 接口、LLMResponse、LLMError
│  │  ├─ client.py                LLMClient：磁盘缓存、重试、JSON 解析与修复、用量日志；get_llm(ctx, 角色)
│  │  ├─ anthropic_provider.py    Claude（官方 SDK、流式、effort、prompt 缓存、refusal fallback）
│  │  ├─ openai_compat.py         任意 OpenAI 兼容接口：DeepSeek、Gemini、OpenRouter……
│  │  ├─ fake.py                  离线假模型（输出格式与真实模型一致），用于调试和测试
│  │  └─ usage.py                 按月累计花费、预算提醒
│  │
│  ├─ prompting/                  ── prompt 组合层 ──
│  │  ├─ lens.py                  Lens、LensContext、LensAdapter 基类（路径回退、去代码块、标题降级、截断）
│  │  ├─ lens_adapters/
│  │  │  ├─ basic.py              markdown（prompts/lenses/*.md）与 topic（topics.yaml 的镜头问题）
│  │  │  └─ skills.py             skill_manifest（带 manifest.yaml 的 skill）与 skill_markdown（只有 SKILL.md 的）
│  │  ├─ selector.py              LensCatalog（lens 目录）+ select_lenses（规则 + LLM 建议 + 数量上限）
│  │  ├─ composer.py              PromptComposer：渲染模板，按优先级和字符预算装入 lens
│  │  └─ skills_sync.py           把 ~/.claude/skills 中的文本复制到仓库 skills/
│  │
│  ├─ plugins/                    ── 流水线插件 ──
│  │  ├─ sources/
│  │  │  ├─ arxiv_rss.py          source.arxiv_rss：每日公告 RSS（默认）
│  │  │  ├─ arxiv.py              source.arxiv：arXiv API，按提交日期查询（补抓历史）；按 ID 获取元数据
│  │  │  ├─ local.py              source.file：从本地 JSON / Atom 文件读取（测试、离线）
│  │  │  └─ single.py             source.ids：read 命令的入口（arXiv ID 或 Zotero key）
│  │  ├─ filters/
│  │  │  ├─ seen.py               filter.seen 去重；publisher.mark_seen 运行成功后写回
│  │  │  ├─ keyword.py            filter.keyword：按方向的强关键词 / 语境词 / 排除词正则预筛
│  │  │  └─ zotero_index.py       filter.zotero_index：跳过 Zotero 中已有的论文
│  │  ├─ rankers/llm_screen.py    ranker.llm_screen：批量摘要筛选 + 反馈乘子 + 兴趣画像 → 排序与选择
│  │  ├─ fulltext/loaders.py      enricher.fulltext 及加载器 arxiv_html / pdf / local_pdf / abstract
│  │  ├─ readers/deep_read.py     reader.deep_read：选 lens → 组 prompt → 调模型 → 解析笔记（可多线程）
│  │  ├─ renderers/
│  │  │  ├─ markdown.py           renderer.markdown：单篇笔记（保留你改过的 frontmatter 字段）+ 每日日报
│  │  │  └─ site_index.py         renderer.site_index：站点契约（JSON/RSS）+ 首页 + 方向页
│  │  └─ publishers/outputs.py    publisher.review_queue / git_commit / local_copy / site
│  │
│  ├─ integrations/               ── 与外部系统联动 ──
│  │  ├─ review.py                审核队列：review_queue.json（云端写）+ review_decisions.json（本地写）
│  │  ├─ zotero/
│  │  │  ├─ base.py               ZoteroReader / ZoteroWriter 接口，AddRequest / AddResult / ZoteroRef
│  │  │  ├─ http_api.py           读取：local_api（本机 Zotero 7）与 web_api（zotero.org），格式相同、共用实现
│  │  │  ├─ connector.py          写入：本机 connector（saveItems → updateSession 移到分类 → 必要时 saveAttachment 补 PDF）
│  │  │  ├─ web_api.py            写入：zotero.org Web API（可在云端用；自动创建分类；PDF 存为链接）
│  │  │  ├─ citekey.py            Better BibTeX 取 citekey；没装时按 姓+年份+标题词 生成
│  │  │  ├─ profile.py            文库去重索引（arXiv ID / DOI / 标题）与兴趣画像（加权词表，纯 Python）
│  │  │  └─ service.py            ZoteroService：组合读写后端；入库并回写笔记、导出画像、反向精读、诊断
│  │  ├─ site/
│  │  │  ├─ contract.py           站点数据契约：IndexEntry、api/index.json、api/daily、api/papers、feed.xml
│  │  │  └─ adapters.py           站点适配器：mkdocs / hugo / jekyll / generic_repo
│  │  └─ obsidian/feedback.py     扫描笔记 frontmatter（rating、zotero_approve）+ 审核决定 → 方向权重乘子
│  │
│  └─ app/                        ── 本地审核助手 ──
│     ├─ server.py                FastAPI 接口：队列、笔记、决定、同步、画像、git 拉取/推送；只允许本机访问
│     └─ static/index.html        单页界面：筛选标签、按钮、批量操作、分类选择、笔记渲染（Markdown + KaTeX）
│
├─ docs/                          生成的内容（MkDocs 网站源 = Obsidian 笔记）
│  ├─ index.md                    首页：最近日报、方向入口、高价值论文
│  ├─ daily/YYYY-MM-DD.md         每日日报：精读列表 + 也值得关注的论文表
│  ├─ papers/YYYY/MM/<id>.md      单篇精读笔记
│  ├─ topics/<方向>.md            方向索引页
│  ├─ api/                        JSON 数据（站点契约）
│  ├─ feed.xml                    RSS
│  └─ javascripts/mathjax.js      网站公式渲染配置
│
├─ data/                          跨运行的状态（提交到 git）
│  ├─ seen.json                   已处理论文（云端写）
│  ├─ review_queue.json           待审核队列（云端写）
│  ├─ review_decisions.json       你的审核决定与入库结果（本地写）
│  ├─ zotero_index.json           Zotero 去重索引（本地写）
│  ├─ interest_profile.json       兴趣画像（本地写）
│  └─ usage.json / usage.local.json   月度花费（云端 / 本地分别写）
│
├─ .cache/                        不进 git：运行检查点、LLM 缓存、全文缓存、PDF、调试用 prompt
└─ tests/                         测试：fake 模型 + 本地样例数据，不联网、不花钱
   ├─ conftest.py                 在临时目录搭一个完整项目
   ├─ test_pipeline.py            插件注册、端到端、断点续跑、禁用 stage、dry-run、自定义插件、参数校验
   ├─ test_components.py          RSS 解析、关键词、JSON 解析、lens / skill 解析、预算裁剪、prompt、花费统计
   ├─ test_review_zotero_app.py   入库与 citekey 回写、Obsidian 勾选、反馈、用户字段保留、索引与画像、审核页安全
   └─ fixtures/                   样例论文、RSS、模拟 skill
```

`data/` 中的文件按写入方拆开，避免 git 冲突：云端只写 `seen.json`、`review_queue.json`、`usage.json` 和新笔记；本地只写其余文件，以及笔记 frontmatter 中的用户字段。部分文件要在对应功能第一次运行后才会出现。

### 3.4 插件一览

`dailypaper list-plugins` 可以列出当前全部插件。

| 类别（kind） | 名称 | 文件 | 作用 |
|---|---|---|---|
| source | `arxiv_rss`、`arxiv`、`file`、`ids` | `plugins/sources/` | 产生候选论文 |
| filter | `seen`、`keyword`、`zotero_index` | `plugins/filters/` | 删掉不需要的候选 |
| ranker | `llm_screen` | `plugins/rankers/` | 打分、排序、选出精读对象 |
| enricher | `fulltext` | `plugins/fulltext/` | 补充全文 |
| reader | `deep_read` | `plugins/readers/` | 生成精读笔记 |
| renderer | `markdown`、`site_index` | `plugins/renderers/` | 写文件 |
| publisher | `review_queue`、`mark_seen`、`git_commit`、`local_copy`、`site` | `plugins/publishers/`、`plugins/filters/seen.py` | 有外部副作用的输出 |
| fulltext_loader | `arxiv_html`、`pdf`、`local_pdf`、`abstract` | `plugins/fulltext/loaders.py` | 全文来源 |
| llm | `anthropic`、`openai_compat`、`fake` | `llm/` | 模型服务 |
| lens_adapter | `markdown`、`topic`、`skill_manifest`、`skill_markdown` | `prompting/lens_adapters/` | lens 内容来源 |
| zotero_reader | `local_api`、`web_api` | `integrations/zotero/http_api.py` | 读取文库 |
| zotero_writer | `connector`、`web_api` | `integrations/zotero/` | 写入文库 |
| site_adapter | `mkdocs`、`hugo`、`jekyll`、`generic_repo` | `integrations/site/adapters.py` | 接入网站框架 |

### 3.5 配置文件

| 文件 | 常改的内容 |
|---|---|
| `pipeline.yaml` | 每天精读几篇（`screen.top_k`）、精读阈值（`min_score`）、arXiv 分类、启用或禁用 stage |
| `topics.yaml` | 研究方向的描述、关键词、权重、精读时追问的镜头问题 |
| `llm.yaml` | 各角色用哪个模型、`effort`、`max_tokens`；价格表；月度预算 |
| `lenses.yaml` | 有哪些 lens、什么条件下使用、优先级、字符预算 |
| `settings.yaml` | 输出语言；网站标题、地址和适配器；Zotero 读写方式和默认分类；Obsidian vault 路径；本地助手端口 |

YAML 中可以用 `${ENV}` 或 `${ENV:-默认值}` 引用环境变量。API key 只从环境变量读取（本地读 `.env`，云端读 GitHub Secrets），不写进配置文件。

---

## 4. 功能详解

### 4.1 每日流水线

1. **抓取**：`rss.arxiv.org` 一次返回当天公布的所有论文。默认分类为 cs.CL / cs.LG / cs.AI / cs.MA / stat.ML，类型为 new + cross（不含旧论文的新版本）。周末 arXiv 没有公告。
2. **预筛**：每个方向独立判断。需要命中一个强关键词；如果配置了语境词（例如 LLM、agent），还要同时命中一个语境词，用来排除 CV 里的 calibration 这类同名概念。最多保留 60 篇。
3. **筛选**：Haiku 每批读 10 篇的标题和摘要，给出各方向 0–10 分、论文类型、novelty（0–3）、理由和建议 lens。最终排序分的计算方式：

   `max(方向分 × 方向权重 × 反馈乘子) + novelty × 0.5 + 兴趣画像相似度 × 1.5`

   取前 `top_k` 篇中分数不低于 `min_score` 的进入精读。
4. **全文**：优先用 arXiv HTML，章节层级和 LaTeX 公式都能保留；没有 HTML 时下载 PDF，正文带 `[p.N]` 页码标记；都失败时只用摘要，笔记中会注明"仅有摘要"。超过 9 万字符的部分（通常是附录）会截断。
5. **精读**：见 4.2。
6. **输出**：笔记、日报、方向页、首页、JSON、RSS；同时加入审核队列；在云端还会提交并发布网站。

### 4.2 精读框架与 lens

**base 框架**（`prompts/base/deep_read.system.md.j2`）固定输出 10 节：

| 节 | 内容 |
|---|---|
| 0. TL;DR | 做了什么、最重要的结果（带数字）、对哪个方向有用 |
| 1. Problem formulation | 形式化设定、访问假设（black/grey/white-box）、数据假设、不确定性粒度 |
| 2. Core insight | 用"现有方法默认 X → 实际上 Y → 所以作者 Z"概括 |
| 3. Novelty delta | 与最相近前作的对比表，判断是实质创新还是增量改进 |
| 4. Mechanism | 步骤拆解、关键公式、每个组件为什么 work |
| 5. Guarantees & assumptions | 保证类型（coverage / risk control / PAC…）、前提、在 agent 场景是否失效 |
| 6. Claim ↔ Evidence | 主要 claim 与实验证据的对照表及证据强度 |
| 7. Red flags | 作者没说的问题，区分"作者已承认"和"〔分析〕" |
| 8. Research hooks | 2–3 条面向你研究方向的可执行 idea：改变的假设、最小验证实验、风险 |
| 9. 是否值得读原文 | 必读 / 选读 / 跳过，以及最该看的章节 |

框架要求每个判断标注原文出处（如【§3.2】【Table 2】），找不到依据时写"原文未说明"。模型还会先输出一段 JSON 元信息（TL;DR、核心 insight、价值分 1–5、读不读），供日报、审核页和网站使用。

**lens** 只补充分析要点和检查清单，**不改变输出结构**。有三种来源：

| 来源 | 适配器 | 例子 |
|---|---|---|
| 研究方向 | `topic` | `topics.yaml` 中的 `lens_questions`，自动生成 `topic:<id>`，例如 CP 方向会追问 score function、calibration set、exchangeability 是否成立 |
| 论文类型 | `markdown` | `prompts/lenses/type_theory.md` 等，按筛选判断的论文类型自动选择 |
| 外部 skill | `skill_manifest` / `skill_markdown` | `nature-paper-card`：证据纪律、按类型的拆解、研究 idea 门槛；`ccf-idea-reviewer`：novelty / insight 评审标准 |

lens 的选择过程：
1. 按 `lenses.yaml` 的 `rules` 匹配：`always`、`topic`、`paper_type`、`min_score`、`min_novelty`、`source` 等条件。
2. 加上筛选阶段 LLM 的建议（最多 `max_extra` 个）。
3. 按优先级排序、截断数量（`max_lenses`）。
4. 由组合器按字符预算（`budget_chars`）装入，超出时截断或丢弃低优先级的 lens。

每篇笔记的 frontmatter 记录了实际用到的 lens，方便对比效果。

接入外部 skill 时只抽取其中"怎么分析"的文本：
- 去掉脚本、代码块和工具调用指令；
- 把外部标题降到四级，避免打乱 base 框架的层级；
- 带 manifest 的 skill 会按论文类型选对应的片段。

### 4.3 审核与 Zotero

默认通过**本机 Zotero 桌面端**联动，与你现在"附件用坚果云 WebDAV 同步"的配置兼容，不需要 zotero.org API key。

```bash
dailypaper zotero-check      # 检查本地 API / connector / Better BibTeX
```

1. Zotero 设置 → 高级 中勾选"允许此计算机上的其他应用程序与 Zotero 通信"（`zotero-check` 显示 ✓ 即已开启）。
2. 在 Zotero 里新建一个 **DailyPaper** 分类。默认放入 `DailyPaper/<方向名>`；子分类不存在时退回 `DailyPaper`，再不存在则放文库根目录。connector 不能新建分类。
3. `dailypaper app` 打开审核页 `http://127.0.0.1:8765`，Zotero 需保持打开。每篇论文显示价值分、方向、TL;DR、核心 insight，有三个按钮：**✅ 加入 Zotero**（可选目标分类）、**⏸ 稍后**、**❌ 不要**；支持勾选后批量操作，也能展开完整笔记（公式已渲染）。
4. 入库内容：条目元数据（arXiv ID、DOI）、标签（`dailypaper`、方向、`value-N`）、精读笔记（作为子笔记）、PDF（由 Zotero 下载后按你的设置同步到坚果云）。入库后 citekey、`zotero_key` 和 `zotero://` 链接会回写到 Obsidian 笔记。

| 其他联动 | 入口 | 作用 |
|---|---|---|
| 兴趣画像 | `dailypaper zotero-profile` / 审核页"更新兴趣画像" | 从文库导出去重索引和兴趣画像，之后筛选会跳过已有论文，并按文库相似度加分 |
| 反向精读 | `dailypaper read <Zotero 条目 key>` | 对文库中任意论文做同样框架的精读，优先用本地 PDF |
| 补同步 | `dailypaper zotero-sync` / 审核页"同步到 Zotero" | 处理所有已批准但还没入库的论文（含 Obsidian 中勾选的） |
| 与云端同步 | 审核页"拉取最新 / 推送审核结果" | 拉取云端的新日报；推送你的审核决定，用于排序反馈 |
| 命令行审核 | `dailypaper review --approve <id>` | 不打开网页也能审核 |

审核页只允许本机访问，所有写操作都要求自定义请求头，其他网站无法借你的浏览器调用它。

想在云端直接入库，可以改用 Web API：在 `settings.yaml` 设置 `zotero.writer: web_api`，`web_api.library_id` 填 userID，并设置环境变量 `ZOTERO_API_KEY`（userID 和 key 在 zotero.org/settings/keys 获取，key 需要写权限）。

### 4.4 Obsidian

推荐用 Git 同步：
1. 把仓库 clone 到 vault 的某个子目录，例如 `<vault>/DailyPaper`。
2. 安装社区插件 **Obsidian Git**，把 "Auto pull interval" 设为 30–60 分钟。
3. 笔记在 `DailyPaper/docs/` 下。

笔记 frontmatter 中你可以编辑的字段（重新生成笔记时会保留）：

| 字段 | 作用 |
|---|---|
| `rating: 1-5` | 你的评分，会回流到排序（每个方向的乘子限制在 0.8–1.2） |
| `status` | 阅读状态，可配合 Dataview 做阅读清单 |
| `zotero_approve: true` | 在 Obsidian 里勾选入库 |
| `citekey` / `zotero_key` / `zotero_link` | 入库后自动回写 |

不用 Git 的话，可以在 `settings.yaml` 设置 `obsidian.vault_path`，并在 `pipeline.yaml` 启用 `obsidian` stage，本地运行时会直接复制到 vault。

### 4.5 网站与个人网站接口

默认用 MkDocs Material 生成独立站：深浅色、中文搜索、标签、公式。GitHub Actions 每天构建并发布。

**站点数据契约**（`integrations/site/contract.py`）是对外的稳定接口，字段只增不删：

| 文件 | 内容 |
|---|---|
| `docs/papers/**.md`、`docs/daily/*.md` | 带统一 frontmatter 的 Markdown |
| `docs/api/index.json` | 所有已精读论文的索引 |
| `docs/api/daily/<日期>.json` | 某天的日报数据 |
| `docs/api/papers/<id>.json` | 单篇完整数据（含精读正文） |
| `docs/feed.xml` | RSS |

有了自己的网站后，在 `settings.yaml` 中设置：

```yaml
site:
  adapter: generic_repo        # 或 hugo / jekyll（本机的站点目录）
  adapter_options:
    repo_url: https://github.com/<你>/<你的网站仓库>.git
    target_dir: content/dailypaper
    format: hugo               # hugo / jekyll / plain
```

任何网站也可以直接用前端读取 `api/index.json` 或 RSS 来展示，不需要服务器。

### 4.6 花费统计

每次调用按 `llm.yaml` 中的 `pricing` 计算花费，按月累计：云端写 `data/usage.json`，本地写 `data/usage.local.json`，统计时合并。命中缓存的调用不计入。每次运行结束会打印本月花费，超过 `monthly_budget_usd` 的 80% 时给出警告。请按你的 Max 档位修改预算值。

---

## 5. 部署到 GitHub（不需要服务器）

GitHub Actions 每天替你运行，GitHub Pages 免费托管网站。你只需要一个 GitHub 账号。

1. **建仓库并推送**：在 GitHub 新建一个空仓库，例如 `DailyPaper`。在你自己的终端里登录（`gh auth login` 或 GitHub Desktop），**不要把密码或 token 发给任何人**。然后执行：
   ```bash
   git remote add origin https://github.com/<你的用户名>/DailyPaper.git
   git push -u origin main
   ```
2. **添加 Secret**：在仓库 Settings → Secrets and variables → Actions → New repository secret 中添加 `ANTHROPIC_API_KEY`（与 `.env` 中的 key 相同）。
3. **开启 Pages**：Settings → Pages → Build and deployment → Source 选 **GitHub Actions**。
4. **同步 skill**（可选）：运行 `dailypaper sync-skills`，让云端精读也能用上 `nature-paper-card` 等 skill，然后提交 `skills/`。
5. **第一次运行**：在 Actions → daily → Run workflow 手动触发。之后每个工作日 UTC 06:00（北京时间 14:00）自动运行。
6. 网站地址为 `https://<你的用户名>.github.io/DailyPaper/`。把它填进 `settings.yaml` 的 `site.base_url`，RSS 链接就会指向网站。

**public 还是 private 仓库？** 仓库里会有：
- 精读笔记
- `skills/` 中的 skill 文本
- `data/zotero_index.json` 和 `data/interest_profile.json`（你的 Zotero 文库标题和关键词）

public 仓库中这些内容都是公开的。如果介意，可以：
- 用 private 仓库。GitHub Pages 对 private 仓库需要 Pro；也可以改用 Cloudflare Pages，免费并支持私有仓库，构建命令填 `pip install mkdocs-material && mkdocs build`，输出目录填 `site`。
- 或者把上述文件加入 `.gitignore`。代价是云端无法按文库去重和做个性化排序，也用不上外部 skill；本地运行不受影响。

---

## 6. 扩展指南

| 想做的事 | 改哪里 |
|---|---|
| 调整研究方向、关键词 | `config/topics.yaml` |
| 换模型（DeepSeek / Gemini / 其他 OpenAI 兼容接口） | `config/llm.yaml` 的 `roles`，并在 `.env` 加对应 key |
| 每天精读几篇、阈值 | `config/pipeline.yaml` → `screen` 的 `top_k` / `min_score` |
| 增删流水线模块 | `config/pipeline.yaml` 增删条目；或临时用 `--enable` / `--disable <stage>` |
| 修改精读框架 | `prompts/base/deep_read.system.md.j2` |
| 加一个论文类型 lens | 在 `prompts/lenses/` 新建 md，并在 `lenses.yaml` 的 `catalog` 中声明 |
| 接入一个阅读 skill | 见下文 |
| 写一个新插件 | 见下文 |

**接入一个阅读 skill**：
1. `dailypaper sync-skills --only <skill名>`，把 skill 文本复制到 `skills/`（云端需要；本地会自动回退读取 `~/.claude/skills/`）。
2. 在 `lenses.yaml` 的 `catalog` 加一条。带 `manifest.yaml` 的 skill 用 `adapter: skill_manifest`，可配置 `exclude`、`axis_map`、`references`；只有 `SKILL.md` 的用 `adapter: skill_markdown`，并用 `options.sections` 选出需要的小节。
3. 在 `rules` 中写明何时使用。
4. 用 `dailypaper list-lenses` 检查可用状态。`dailypaper read <id> --prompt-only` 只生成 prompt（写到 `.cache/prompts/`），不调用模型，可以先看效果。

**写一个新插件**：在 `src/dailypaper/plugins/<类别>/` 下新建模块，加上 `@register`，然后在 `pipeline.yaml` 中引用。

```python
from pydantic import BaseModel
from dailypaper.core.interfaces import Filter
from dailypaper.core.registry import register

@register("filter", "min_authors")
class MinAuthors(Filter):
    class Params(BaseModel):          # pipeline.yaml 中 with: 的参数，拼错参数名会直接报错
        n: int = 2

    def run(self, items):
        return [c for c in items if len(c.paper.authors) >= self.p.n]
```

```yaml
- {id: authors, use: filter.min_authors, with: {n: 3}}
```

LLM 厂商、全文加载器、lens 适配器、Zotero 后端、网站适配器的扩展方式相同：继承对应的基类，加上 `@register("<kind>", "<name>")`。第三方包也可以通过 entry point `dailypaper.plugins` 注册插件，不需要修改本仓库。

---

## 7. 命令速查

| 命令 | 作用 |
|---|---|
| `dailypaper run` | 运行每日流水线 |
| `dailypaper run --pipeline backfill --date 2026-10-20` | 补抓某天的论文（arXiv API） |
| `dailypaper run --from read` / `--until screen` | 从某一步开始 / 运行到某一步为止 |
| `dailypaper run --provider fake --dry-run` | 不调用真实模型、不写队列、不提交 |
| `dailypaper run --enable git` / `--disable read` | 临时启用 / 禁用某个 stage |
| `dailypaper read <id> [<id> ...]` | 精读指定论文（arXiv ID 或 Zotero key） |
| `dailypaper read <id> --prompt-only` | 只生成 prompt，不调用模型 |
| `dailypaper app` | 打开本地审核助手 |
| `dailypaper review [--approve ID] [--reject ID] [--status pending]` | 命令行审核 |
| `dailypaper zotero-check` / `zotero-sync` / `zotero-profile` | Zotero 诊断 / 补同步 / 导出画像 |
| `dailypaper sync-skills [--only NAME ...]` | 同步外部 skill |
| `dailypaper list-plugins` / `list-lenses` | 查看插件 / lens |
| `mkdocs serve` / `mkdocs build` | 预览 / 构建网站 |

---

## 8. 开发与测试

```bash
pytest                 # 24 个测试：fake 模型 + 本地样例，不联网、不花钱
ruff check src tests
```

CI（`.github/workflows/ci.yml`）在 Python 3.10、3.12、3.13 上运行同样的检查。

---

## 9. 与最初设计计划的差异

最初计划里的功能都已实现，只有"批注同步"在计划中就标为"后续"，目前尚未实现。实现时为了让结构更简洁，调整了文件组织：

| 计划中的文件 | 实际情况 | 原因 |
|---|---|---|
| `prompts/base/deep_read.md.j2`、`screen.md.j2` | 各拆成 `.system.md.j2` 和 `.user.md.j2` | 系统提示和论文内容分开，方便编辑，也能利用 prompt 缓存 |
| `prompts/lenses/topic_cp.md` 等 | 方向 lens 由 `topics.yaml` 的 `lens_questions` 自动生成；只保留 `type_*.md` | 方向信息只维护一处 |
| `config/` 四个文件 | 另加 `settings.yaml` | 路径、语言、Zotero、Obsidian、网站、本地助手的设置需要集中放置 |
| `core/config.py`（pydantic-settings） | 纯 pydantic + 自写的 `${ENV}` 展开 | 少一个依赖 |
| — | 新增 `core/context.py`、`core/io.py` | 运行上下文；原子写文件和 frontmatter 读写 |
| `interfaces.py` 中的 FulltextLoader | 移到 `plugins/fulltext/loaders.py`，核心层新增 `Enricher` 类别 | 加载器不是流水线 stage，不放在核心层 |
| `llm/anthropic.py` | `llm/anthropic_provider.py`；新增 `client.py`、`usage.py` | 避免与 `anthropic` 包同名；缓存重试和花费统计单独成文件 |
| `lens_adapters/markdown_lens.py`、`skill_manifest.py`、`skill_markdown.py` | `basic.py`（markdown + topic）、`skills.py`（两种 skill）；新增 `lens.py`、`skills_sync.py` | 相近的适配器合并 |
| `sources/arxiv.py` | `arxiv_rss.py`（新默认）、`arxiv.py`、`local.py`、`single.py` | 实测 arXiv API 容易限流（429），RSS 一次请求即可；另需测试用和 read 命令用的入口 |
| `filters/keyword.py` | 另加 `seen.py`、`zotero_index.py` | 去重做成可插拔 stage |
| `fulltext/arxiv_html.py`、`pdf_pymupdf.py` | 合并为 `fulltext/loaders.py`，另加 `local_pdf`、`abstract` | 加载器共用代码；需要兜底 |
| `renderers/markdown.py` | 另加 `site_index.py` | 站点契约、首页、方向页与单篇渲染分开 |
| `publishers/git_commit.py`、`local_copy.py` | 合并为 `publishers/outputs.py`，另含 `review_queue`、`site` | 都是短小的输出插件 |
| `zotero/local_api.py` | `http_api.py`（local_api 与 web_api 共用） | 两者接口格式相同 |
| `zotero/bbt.py` | `citekey.py` | 你没装 Better BibTeX，需要备用生成方式 |
| `zotero/web_api.py`（pyzotero） | 用 httpx 直接实现 | 少一个依赖 |
| — | 新增 `zotero/service.py`、`profile.py`、`integrations/review.py` | 入库编排、去重索引与兴趣画像、审核队列 |
| `site/adapters/` 目录 | `site/adapters.py` 单文件 | 适配器都很短 |
| `data/seen.json` | 第一次每日运行后出现；`data/` 中还有审核、Zotero、花费等文件 | 见 3.3 |
| `skills/` | 运行 `dailypaper sync-skills` 后才出现 | 是否公开 skill 文本由你决定 |
| `tests/` 中录制的 arXiv 响应 | 改用手写的样例数据 | 不把真实论文摘要放进仓库 |

**尚未完成或验证的事项**：
- 推送到 GitHub 和第一次云端运行；
- 用真实条目验证 Zotero 写入（审核页点"加入 Zotero"）；
- `skills/` 同步；
- Zotero 批注同步到 Obsidian（计划中标为后续）。
