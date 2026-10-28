# 工作流程图 · 图片生成提示词

生成后保存为 `assets/workflow.webp`（README 引用这个路径）。建议 16:9 或 3:2 横图，宽度 1600 px 以上。

## 中文提示词

```
一张横版信息图，扁平动画风格（类似 Kurzgesagt / 现代科普动画），干净的几何形状，柔和的阴影，统一的圆角，无文字乱码。
背景：深蓝夜空色（#0f1b2d），带细微的网格或星点纹理。主色：青色（#3ddc97）、琥珀色（#ffb347）、淡紫（#b48cff），文字白色。

画面从左到右是一条流水线：
1. 最左侧：一个橙色的 arXiv 纸飞机 / 纸卷图标，旁边一摞"新论文"卡片，标签"arXiv 每日 RSS"。
2. 流水线用一条发光的传送带表示，上方悬浮一个时钟图标，标签"每个工作日定时触发（GitHub Actions / 本地任务）"。
3. 传送带上依次有 6 个工作站，每个是一个带图标的小圆形机器人或机械臂，下方是标签：
   - "去重"：一个漏斗，旁边一个小小的 Zotero 书架图标，从下方伸出一条细线连进漏斗
   - "关键词预筛"：一个放大镜，旁边数字 300→60
   - "LLM 摘要筛选"：一个小号的可爱机器人头，手里拿评分卡，标签"小模型打分 · 取 top-5"
   - "全文获取"：一页展开的论文，带 HTML 和 PDF 两个小角标
   - "LLM 精读"：一个大号的机器人，戴眼镜，专注读纸，旁边漂浮几个"lens"透镜图标，标签"大模型 + lens"
   - "输出"：从机器人手中飞出三张卡片，分别写 Markdown、JSON、RSS
4. 右侧流水线分成三条发光支线，各自通往一个目的地（三个小岛或小平台）：
   - "GitHub Pages 网站"：一台显示网页的显示器
   - "Obsidian"：一颗紫色的宝石 / 黑曜石图标，旁边有星级评分
   - "本地审核页"：一个带绿色 ✓ 按钮的卡片界面，标签"点击确认"
5. 从 Obsidian 和审核页各引出一条线，汇合后流向画面右下角的"Zotero 文库"（一个红色书架图标），再从 Zotero 画一条弧形虚线绕回画面左侧的"去重"工作站，标注"回流：去重 + 兴趣画像"。

整体构图清晰、可读，像一张产品官网的 hero 插图。所有标签用简体中文，字体无衬线、清晰可读。4K，锐利，无水印。
```

## English prompt

```
A wide horizontal infographic in flat 2D animation style (Kurzgesagt-like), clean geometric shapes, soft shadows, consistent rounded corners, crisp legible labels, no garbled text.
Background: deep navy (#0f1b2d) with a subtle grid / star texture. Accent palette: mint green (#3ddc97), amber (#ffb347), soft purple (#b48cff); text in white.

Left to right, a single glowing conveyor-belt pipeline:
1. Far left: an orange arXiv paper-plane / scroll icon next to a stack of "new paper" cards, labeled "arXiv daily RSS".
2. Above the belt, a clock icon labeled "runs every weekday (GitHub Actions / local scheduler)".
3. Six workstations on the belt, each a small round robot or robotic arm with an icon and a label below:
   - "Dedupe": a funnel, with a tiny Zotero bookshelf icon connected to it by a thin line from below
   - "Keyword prefilter": a magnifying glass, with the number 300→60
   - "LLM screening": a small cute robot head holding a score card, label "small model · top-5"
   - "Fetch full text": an open paper page with small HTML and PDF badges
   - "LLM deep read": a larger robot wearing glasses, intently reading, with several floating lens icons around it, label "large model + lenses"
   - "Output": three cards flying out of the robot's hands, labeled Markdown, JSON, RSS
4. On the right the belt splits into three glowing branches, each ending on a small floating platform:
   - "GitHub Pages site": a monitor showing a web page
   - "Obsidian": a purple gem / obsidian crystal with a star rating next to it
   - "Local review page": a card UI with a green ✓ button, label "click to approve"
5. Lines from Obsidian and the review page merge and flow to a red bookshelf icon at the bottom right labeled "Zotero library"; from Zotero a dashed arc loops back to the "Dedupe" station on the left, labeled "feedback: dedupe + interest profile".

Composition should read like a product-website hero illustration: clear hierarchy, generous spacing, sans-serif labels. 4K, sharp, no watermark.
```

## 使用建议

- 想让模型少出错别字：把标签改成英文生成，再用矢量工具（Figma / Inkscape）手动替换为中文。
- 对标签位置不满意时，可以在提示词末尾加一句"所有文字标签放在元素正下方，水平居中"。
- 如果要做成动图：先生成静态图，再用 Canva / After Effects 做传送带流动和卡片飞出的小动画。
