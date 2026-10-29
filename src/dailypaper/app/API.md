# DailyPaper 网页 ↔ 后端接口约定

网页是一个静态文件 `index.html`（加 `assets/`），同一份文件用在三个地方：

| 运行位置 | 怎么判断 | 能做什么 |
|---|---|---|
| 本地助手 `dailypaper app`（http://127.0.0.1:8765） | `GET /api/meta` 返回 200 | 全部功能：浏览、审核入库 Zotero、改搜索方向、触发运行 |
| GitHub Pages / 个人网站 | `/api/meta` 失败，`api/index.json` 成功 | 只读浏览；设置页可导出 `topics.yaml` |
| 都没有（本地直接打开、设计预览） | 两者都失败 | 内置示例数据，所有写操作只做演示 |

所有写请求都带 `Content-Type: application/json` 和 `X-DailyPaper: 1`，与 `app/server.py` 现有的安全中间件一致。

## 1. 已有接口（`app/server.py` 现状，网页直接使用）

| 方法 | 路径 | 网页用途 |
|---|---|---|
| GET | `/api/meta` | 判断本地模式；研究方向名称；各审核状态数量；`schema`（笔记结构：元信息标签、各节标题，来自 prompts/schema.yaml） |
| GET | `/api/items?status=` | 审核台列表（`{entry: QueueEntry, decision: Decision}[]`） |
| GET | `/api/note/{id}` | 论文详情的备用来源（`/site/api/papers/{id}.json` 不可用时） |
| GET | `/api/zotero/status` | Zotero 连接状态、分类列表、默认分类 |
| POST | `/api/decide` `{ids, action, collection}` | 选中论文 → 入库 / 稍后 / 不要（任何页面的多选栏和审核台） |
| POST | `/api/sync` | 从 Obsidian 收集入库请求并导入 |
| POST | `/api/profile` | 从 Zotero 导出文库索引与兴趣画像 |
| POST | `/api/git/pull`、`/api/git/push` | 同步审核结果 |
| GET | `/api/pdf/{arXiv ID}` | 同源返回论文 PDF（缓存在 `.cache/pdf/`），论文页的分栏阅读用它嵌入 iframe |
| GET | `/site/api/thumbs/{id}.jpg` | 论文首页缩略图，由 `renderer.thumbnails` 生成；不存在时网页显示占位 |
| GET | `/api/zotero/library` | Zotero 分类树（文献库视图） |
| GET | `/api/zotero/items?collection=KEY` | 某分类下的条目，附带是否已有精读笔记 |
| GET/PUT | `/api/annotations/{id}` | 某篇论文的高亮与批注；`GET /api/annotations` 返回各篇数量 |
| GET | `/api/trends?days=7` | 阅读统计（每日、方向、类型、Top）及缓存的 AI 总结 |
| POST | `/api/trends/summary` `{days, force?}` | 用筛选模型生成并缓存阅读总结 |
| POST | `/api/shutdown` | 退出本地助手（桌面图标启动时使用） |
| GET/PUT | `/api/config` | 搜索设置（见第 2.2 节，已实现） |
| POST/GET | `/api/run`、`/api/run/status` | 触发运行与轮询（见第 2.3 节，已实现） |
| GET/PUT | `/api/config/llm` | 运行与费用：provider 列表（只报告 key 是否存在）、screen / read 角色的模型、effort、max_tokens、联网搜索；预算、价格表、缓存开关。写回 `config/llm.yaml` 并保留注释 |
| GET | `/api/usage?months=6` | 本月花费、调用次数、按模型明细，近几个月汇总（云端 + 本地两份记录合并） |
| GET | `/api/storage` | 输出目录、笔记与批注占用、各类缓存（llm / fulltext / pdf / runs / prompts）的大小与文件数 |
| POST | `/api/storage/clear` `{kind}` | 清空一类缓存目录，不动 data/ 与笔记 |
| POST | `/api/open-folder` `{which}` | 在资源管理器中打开 workspace / content / data / cache / config 目录（仅本机） |
| GET | `/api/prompts` | prompts/ 下可编辑的文件：base 模板、lens、schema.yaml；附是否改过默认、历史版本数、审核标记 |
| GET/PUT/DELETE | `/api/prompts/{path}` | 读、写（`{content, reviewed, force}`：先程序校验，不通过且未 force 则 400）、删除（仅 lenses/）；每次保存把旧版放进 prompts/.history/ |
| GET/POST | `/api/prompts/{path}/history`、`.../restore {version}`、`.../reset` | 历史版本、恢复到某版、恢复仓库默认（prompts/defaults/） |
| POST | `/api/prompts/validate`、`/api/prompts/review` `{id, content}` | 程序校验（模板能渲染、精读模板仍含 JSON 元信息与全部小节、schema 字段齐全）；用筛选模型按检查清单审核，返回 `{ok, issues, suggestions}` |
| POST | `/api/prompts/preview` `{paper_id, overrides?}` | 用索引里某篇论文组装精读提示词（可用未保存的内容覆盖），返回 system / user / 选中的 lens |
| GET/PUT | `/api/lenses` | lenses.yaml：预算、catalog（优先级、作用小节、启用）、规则；本机未接入的 skill 列表 |
| POST | `/api/lenses/simulate` `{paper_id}` | 某篇论文按当前规则会用到哪些 lens、哪些超预算被丢弃 |
| POST | `/api/skills/import` `{names}`；GET `/api/skills/{id}/preview` | 从本机 ~/.claude/skills 复制 skill 文本并加入 catalog；查看 skill 原文与实际注入提示词的文本 |
| GET | `/api/export/annotations.json`、`/api/export/notes.zip` | 导出全部高亮与批注；或把所有精读笔记打包，带批注的在末尾附上高亮与批注 |

## 2. 需要主会话新增的接口

### 2.1 挂载内容目录（必须）
```python
from fastapi.staticfiles import StaticFiles
app.mount("/site", StaticFiles(directory=cfg.content_dir), name="site")
# 并让 "/" 返回新的 index.html，"/assets" 指向网页的 assets 目录
```
本地模式下，网页从 `/site/api/index.json`、`/site/api/daily/<date>.json`、`/site/api/papers/<id>.json` 读取内容，和 GitHub Pages 上读取相对路径 `api/...` 是同一份文件。

### 2.2 搜索设置
```
GET /api/config
→ {
    "editable": true,
    "topics": [ {"id","name","weight","description","keywords":[],"context":[],"exclude":[],"lens_questions":[]} ],
    "daily":  {"categories": ["cs.CL", ...], "max_candidates": 60, "top_k": 5, "min_score": 6.5}
  }

PUT /api/config   （请求体同上，不含 editable）
→ {"messages": ["已保存 topics.yaml 与 pipeline.yaml"]}
```
- `topics` 对应 `config/topics.yaml` 的 `topics`；`daily` 对应 `config/pipeline.yaml` 中 `pipelines.daily` 里 `source.*`（categories）、`filter.keyword`（max_candidates）、`ranker.llm_screen`（top_k、min_score）的参数。
- 保存前用 `AppConfig` 校验；建议用 ruamel.yaml 保留注释。校验失败返回 400 和可读的中文原因。
- 若 `config/` 不可写（例如云端），返回 `editable: false`，网页会把保存按钮换成"导出 YAML"。

### 2.3 触发运行
```
POST /api/run  {"pipeline": "daily" | "read", "ids": ["2610.35877"], "date": "2026-10-28"}
→ {"run_id": "...", "messages": ["已开始：daily"]}       （已有运行中的任务时返回 409）

GET /api/run/status
→ {"running": true, "pipeline": "daily", "started_at": "...", "finished_at": null,
   "ok": null, "log_tail": ["[fetch] 213 篇", "..."]}
```
- 后台起子进程 `dailypaper run` / `dailypaper read <ids>`，把 stdout 最后约 200 行放进 `log_tail`。
- 网页在运行期间每 2 秒轮询一次 status，结束后自动刷新数据。

## 3. 网页里的功能 → 接口对照

| 网页位置 | 动作 | 接口 |
|---|---|---|
| 任意论文卡片的勾选框 + 底部多选栏 | 加入 Zotero / 稍后 / 不要 | `POST /api/decide` |
| 首页搜索框粘贴 arXiv ID | 精读这篇 | `POST /api/run {pipeline:"read", ids}` |
| 顶栏"立即运行" | 跑今日任务 | `POST /api/run {pipeline:"daily"}` |
| 审核台 | 状态筛选、单篇/批量决定、Obsidian 同步、兴趣画像、git 同步 | 第 1 节全部 |
| 搜索设置 | 编辑方向、关键词、语境词、排除词、镜头问题、arXiv 分类、Top K、阈值 | `GET/PUT /api/config` |
| 论文详情 | 精读笔记（Markdown + KaTeX）、目录、入库按钮 | `/site/api/papers/<id>.json`、`/api/decide` |
