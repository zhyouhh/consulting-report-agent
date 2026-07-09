# 报告图表生成（数据图 + 结构图）—— v2 设计 spec

- 状态：**✅ 已实施（2026-07-10，一次性合并 v2.0+v2.1）**。与本 spec 的三处实施期偏差：① graphviz 整支砍掉——flowchart/org_chart/tree 用纯 Python 分层布局 + matplotlib 图元实现（零新二进制依赖，v2.1 的分期理由消失，故两期合一）；② 预览 URL 无 cache-bust——chart_id 每次铸新、PNG 内容不可变，`/assets` 路由直接发 immutable 长缓存；③ 图宽不用 `{width=N%}` 属性——渲染定 6.4in 物理宽（≤A4 文字区），pandoc 按 PNG DPI 元数据取尺寸。落地细节与硬约束见项目 CLAUDE.md「## 报告图表生成」段。
- 日期：2026-07-08
- 范围：给咨询报告写作引擎补「模型驱动的图表生成」能力——数据图（柱/线/饼/瀑布…）+ 结构图（2×2 矩阵/流程图/价值链…），全流程打通「生成 → 落盘 → 正文引用 → 预览所见即所得 → docx 导出嵌图」。
- 关联：R5 方法论路由（同类「canonical skill 能力嵌入后失效 → 后端重实现」）、R3 文件树/预览、R4 来源可信度、N6 附件管线（**方向相反**：N6 是图片输入，本 spec 是图表输出）、W2-C 去 Windows 化导出、S5 独立审查。
- 修订：v2（2026-07-08，纳入 Codex spec 审第 1 轮的 6 BLOCKER + 2 NIT——生成/插入分离与 preflight 门禁、文件树复杂度降级、引用扫描契约、导出前硬校验、S5 接触点、计费口径、渲染器并发/资源限制、打包字体+mpl-data）。

---

## 1. 背景与动机

### 1.1 这是一个「设计过、但嵌入后死掉」的能力

canonical skill 里躺着三个模块——`consulting-report-skill/modules/business-charts.md`、`framework-diagrams.md`、`data-analysis.md`——原设计是「模型写 Python + Plotly 代码 → 调 `lib/chart_utils.py` → 输出交互式 `.html`」。

这套在嵌入版（桌面/web app）里**三重失效**：

1. app 内的主模型（`deepseek-v4-pro`）**没有跑代码的工具**——工具集只有 `append_report_draft` / `edit_file` / `web_search` / `fetch_url` / `read_material_file` / `advance_stage` 等写作工具（见 `chat.py:_build_tools` 4405），无 `run_python`。
2. `lib/chart_utils.py` 在沙箱里够不到（和 R5 方法论路由「沙箱够不到 skill 目录」同因）。
3. 就算画出来，Plotly 的 `.html` **根本嵌不进 docx**——而 docx 是本项目唯一承诺的交付形态。

结论：**这不是从零发明新功能，是把一个既定但死掉的能力，按嵌入版架构重新接活**——和 R5 把「模型 read_file 自取方法论」改成「后端代码注入」是同一种手术。

### 1.2 为什么现在只出 spec、不实施

当前优先级是让已上线的 web 产品（`consulting.z0y0h.work`）在试用期稳定、收集真实用户反馈。图表是明确的 v2 升级项：价值高但非阻塞。先把设计和坑落在纸上，实施排到试用稳定之后。

### 1.3 第一性原则：docx 交付「强制」图必须落成图片文件

一个绕不开的事实：报告的交付物是 docx（经 `pandoc report.md -o out.docx`，见 `report_tools.py:export_reviewable_draft`）。plain pandoc 只对**引用的真实图片文件** `![](x.png)` 有稳的 docx 嵌入；内联 SVG（raw HTML）在 docx 输出里被丢弃，mermaid code fence 变成代码块，Plotly html 无从谈起。

所以任何图，最终**必须在某处栅格化成图片文件**。用户（顾问出身）的原始直觉「要先把图画出来成文件再引用」是对的——这是必经之路，不是可避开的难点。真正要设计的是：**图片文件在哪产生、模型怎么描述一张图、预览和导出怎么共用这条产物**。

---

## 2. 目标 / 非目标 / 范围

### 2.1 目标

- **两类图都覆盖**：数据图（数据驱动）+ 结构图（拓扑/版式驱动）。
- **全流程打通**：模型生成 → 落盘项目工作区 → 正文按位置引用 → 工作区预览所见即所得 → docx 导出正确嵌图。
- **咨询级质量下限**：中文、专业配色、一图一结论、数据标签、来源标注——「能进报告」而非玩具（已用样图验证 matplotlib 能到这个下限，见附录 A）。
- **防编造**：图里的数字不能是模型凭空捏造，必须挂来源、留痕、可被审查。
- **零回归**：不破坏 DeepSeek 官渠兼容、多租户隔离、R3 写门禁、计费口径、信任边界、渲染并发安全。

### 2.2 非目标（明确不做）

- **交互式图表**（Plotly/ECharts 的 hover/zoom）——docx 嵌不进，v2 只做静态栅格图。
- **最终排版级「图 N」自动编号 / 图表目录**——交给导出后的排版期，和现有「不承诺最终中文排版稿」边界一致。
- **模型「看」自己生成的图做视觉校验**——本 spec 不引入对生成图的回读视觉环（成本/复杂度）。审查停留在文本层（标题/来源/数据留痕）。
- **图片输入**——N6 附件管线已覆盖（材料图片转写喂模型），方向相反，本 spec 不碰。
- **桑基图（Sankey）**——缺 plotly，matplotlib/graphviz 画都别扭，列 backlog，v2 不做。
- **assets 进文件树可管理**（看/删图的文件面板 UI）——v2.0 降级不做，见 §4.3、§12。

### 2.3 范围与分期

v2 完整能力覆盖两类图，但实施分两个子期（详见 §9）：

- **v2.0**：数据图（matplotlib）+ 签名结构图（后端模板绘制，同引擎、无新二进制依赖）+ 整条「尾巴」（资源路由 / 预览 / 导出前硬校验 / 审查维度 / 部署依赖）。
- **v2.1**：任意结构图（graphviz，新增原生二进制依赖 + 拓扑 DSL）。

---

## 3. 架构总览：一条尾巴 + 两个可插拔的头

**核心洞察：数据图和结构图不是两个系统，是「一条共用的尾巴 + 两个可插拔的头」。** 它们只在「怎么描述一张图 + 用哪个引擎渲染」（头）分叉，共用「渲染产物 → 落盘 → 引用 → 预览 → 导出」（尾）。

```
模型：给数据 / 给拓扑
   │   create_chart / create_diagram 工具（两个入口，对应两个头）
   │   —— 工具自带 preflight stage 门禁（§4.1）
   ▼
后端渲染器（可插拔的「头」）—— Agg 后端 + OO API，并发安全（§4.8）
   ├─ 数据图渲染器   ← 结构化数据      引擎：matplotlib        （v2.0）
   ├─ 签名结构图渲染器 ← 版式化 spec     引擎：matplotlib/后端绘制 （v2.0）
   └─ 任意结构图渲染器 ← 拓扑（节点+边）  引擎：graphviz          （v2.1）
   │
   │   —————————————— 以下是「共用的尾巴」——————————————
   │   出 PNG → content/assets/<chart_id>.png（原子写）
   │   旁存 content/assets/<chart_id>.json（图的 kind + spec + 来源 + 时间）
   ▼
返回一句  ![结论式标题](assets/<chart_id>.png)
   │   模型用现成的 append_report_draft / edit_file 把这句塞进正文对应位置（零新写入机制、吃 R3 写门禁）
   │
   ├─ 预览：新增 GET /api/projects/{id}/assets/{path} 二进制路由（走 require_project 租户隔离）
   │        预览器把 markdown 里相对 src `assets/x.png` 重写指向该路由 → 所见即所得
   └─ 导出：导出前 asset 硬校验（缺图带清单失败）→ pandoc 加 --resource-path → docx 原生嵌图
```

这条架构的价值：**新增一种图 = 加一个渲染器分支，尾巴完全复用**。用户担心的「预览实时长啥样」「导出会不会走样」都落在尾巴里，是确定解不是未知数（§4.5 / §4.6）。

---

## 4. 详细设计

### 4.1 授权接口：两个工具 + 生成/插入分离 + preflight 门禁

新增两个工具，注册进 `chat.py:_build_tools`（4405），dispatch 进 `_execute_tool`（4893）。分两个而非一个 mega-tool，是因为数据图和结构图的参数结构差异大，分开各自 schema 更聚焦、模型更不易填错。

**`create_chart`（数据图）**

```
create_chart(
  kind: "bar" | "grouped_bar" | "stacked_bar" | "horizontal_bar"
      | "line" | "pie" | "donut" | "waterfall" | "funnel"
      | "scatter" | "bubble" | "heatmap",
  title: str,                     # 必填，结论式标题（见 §4.7）
  data: {...},                    # kind 相关的结构化数据
  source: str,                    # 必填，数据来源（进图脚 + sidecar 留痕）
  options?: { unit?, legend?, forecast_from?, annotations?, ... }
)
```

`data` 按 kind 分形（示例）：
- `grouped_bar`：`{categories: [...], series: [{name, values:[...]}, ...]}`
- `line`：`{x: [...], series: [{name, values:[...], style?: "solid"|"dashed"}]}`（`forecast_from` 标记从哪个 x 起转虚线预测）
- `waterfall`：`{steps: [{label, delta}], total_label}`

**`create_diagram`（结构图）**

```
create_diagram(
  kind: # v2.0 模板：
        "matrix_2x2" | "value_chain" | "process" | "roadmap" | "pyramid"
        # v2.1 graphviz：
        | "flowchart" | "org_chart" | "tree",
  title: str,
  spec: {...},                    # kind 相关
  source?: str,
  options?: {...}
)
```

`spec` 按 kind 分形（示例）：
- `matrix_2x2`：`{x_axis: {label, low, high}, y_axis: {label, low, high}, quadrant_labels: [tl,tr,bl,br], items: [{label, x, y}]}`
- `process`：`{steps: [{label, note?}]}`
- `flowchart`（v2.1）：`{nodes: [{id, label, shape?}], edges: [{from, to, label?}]}`

> **schema 分期硬约束（Codex NIT）**：v2.0 的 `create_diagram.kind` enum **只含前 5 个模板类**（`matrix_2x2`/`value_chain`/`process`/`roadmap`/`pyramid`）；graphviz 三类（`flowchart`/`org_chart`/`tree`）**v2.1 才加进 enum**——v2.0 期这三类根本不在 schema 里，模型无从调用（而非「调了才 runtime-error」）。

**两个工具的统一返回**：

```
{ status: "ok" | "error",
  chart_id: str,
  markdown: "![<title>](assets/<chart_id>.png)",   # 模型直接拿去 append/edit
  asset_path: "content/assets/<chart_id>.png",
  output?: str,        # 人类可读说明 / 错误详情
}
```

**返回 markdown 的 alt 文本要净化（Codex NIT）**：`title` 里的 `]`/`[`/换行/markdown 语法会破坏 `![...]()` 引用 → 工具拼 `markdown` 时对 alt 转义/剥这些字符（alt 用净化版；完整 `title` 仍进图脚 + sidecar 不受影响）。非租户逃逸，但避免畸形草稿。

**关键约束（生成 ≠ 插入，两段分开看——这是 Codex 审纠正的核心）**：

- **插入**（把 `![](assets/x.png)` 写进草稿）由模型用现成 `append_report_draft`/`edit_file` 完成——**这一段天然吃 R3 canonical draft 写门禁**（stage / mutation 限额 / read-before-write / mtime CAS），无需新写入机制。
- **生成**（工具产出 png+json）是**工具执行期的独立副作用，发生在草稿写门禁之前、之外**——现有 stage/outline/read-before-write 门禁都只在 draft writer 内部（`append_report_draft`/`edit_file` 的分派里，见 chat.py 5196/4781），`_execute_tool` dispatch 前只有 S0 白名单守卫（chat.py:4917）。所以 `create_chart`/`create_diagram` **必须自带 preflight 门禁，复用草稿写入门禁里「适用于生成」的那几道**（Codex 审两轮纠正——不是宽松的「草稿已存在」，也不是含糊的「完全相同」）：
  - ✅ **阶段合格**（`report_writing.py:check_report_writing_stage`，76）——写作期才生成。
  - ✅ **大纲已确认**（`check_outline_confirmed`，90）——大纲没确认无处插图。
  - ✅ **fetch_url-pending 证据门**（复用 pure helper `report_writing.py:check_no_fetch_url_pending`，211——**不是** `ChatHandler._should_require_fetch_url_before_write`，后者是要文件路径的方法）——**关键且易漏**：否则模型能拿未 fetch 的搜索摘要直接画图，绕过「写作前必须 web_search→fetch_url→落 notes」的证据门（直接强化 §4.7 防编造）。
  - ❌ **不适用**：mixed-intent / mutation 限额 / read-before-write / mtime CAS 是**草稿写入的机械门禁**——生成不是 draft mutation，不套用（插入引用那一步才套，§4.4）。

  preflight 复用这些既有 pure helper，`_execute_tool` 分派前、渲染之前调用。
- **生成了但没插入**（模型失败/遗忘/被 mutation 限额拦）→ 游离 asset：生成工具**不预扣 draft mutation 名额**（生成不算 mutation，只有插入引用才算），游离图靠 §4.3 引用集 sweep 兜底清理。

完整的 per-kind data/spec schema 在实施 plan 里逐条定死；本 spec 只定形状。资源上限见 §4.8。

### 4.2 渲染引擎

- **数据图（matplotlib，v2.0）**：确定性、无浏览器依赖、能嵌 CJK、栅格质量够。已用样图验证下限（附录 A）。渲染逻辑封装在新叶子模块 `backend/chart_render.py`（**只依赖 matplotlib + stdlib，绝不 import chat/skill/main**，遵项目叶子铁律），暴露纯函数 `render_chart(kind, data, title, source, options) -> bytes(PNG)`。**必须 Agg 后端 + 面向对象 API（`Figure`+`FigureCanvasAgg`），不碰 pyplot 全局**——并发安全见 §4.8。
- **签名结构图（后端模板绘制，v2.0）**：2×2 矩阵、价值链、流程箭头、路线图、金字塔——这些是**版式固定**的咨询签名图，用 matplotlib 的图元（矩形/箭头/文本）后端手绘，house style 完全可控。同引擎、无新二进制依赖，所以和数据图一起进 v2.0。放 `backend/diagram_render.py`。
- **任意结构图（graphviz，v2.1）**：分支流程图/组织架构/树——拓扑任意、需自动排版。用 graphviz（轻量原生二进制 `dot`，渲染 PNG，走 subprocess）。代价：自动排版偏「技术感」，难到咨询精致度；新增系统二进制依赖 + 桌面分发难题（§5）。故独立成 v2.1。

**为什么不选 mermaid**：模型写 mermaid 文本最省心，但导出要 headless chromium 渲染，重、且和本项目一贯「避浏览器依赖」（见 opencode normalizer 避 puppeteer）冲突。明确不用。

### 4.3 资源存储与生命周期

- **落盘位置**：`content/assets/<chart_id>.png`（草稿 `content/report_draft_v1.md` 的同级 `assets/` 子目录，见 `skill.py` content 目录 1036）。相对引用 `assets/x.png` 对草稿天然成立，利于导出 resource-path（§4.6）。
- **旁存 sidecar**：`content/assets/<chart_id>.json` = `{kind, title, source, data/spec, created_at}`。三个用途：① 「把 2024 改成 4.0」这类改图不用重讲全部数据；② **强建议防编的数据留痕**（§4.7）；③ 供 S5 审查读取核对数据可溯性。
- **chart_id**：v2.0 每次 `create_chart` 铸新 id（短 uuid/slug），PNG + sidecar 成对写。「改图」= 新生成 + 模型把草稿里旧引用换成新引用（`edit_file`）。「原地按 chart_id 覆盖编辑」列 v2.0 nice-to-have，非首版必需。
- **原子写**：PNG/sidecar 走 temp + `os.replace`（对齐 R3 原子写不变式），避免预览读到半截图。
- **引用集与孤儿清理（需精确契约 + 防删在途图——Codex 审两轮）**：一个 asset「活」当且仅当被 `report_draft_v1.md` 引用。**引用扫描契约必须定死**（实施 plan 逐条测），覆盖：① markdown 图片 `![...](assets/<id>.png)`；② rehype-raw 允许的 raw HTML `<img src="assets/<id>.png">`；③ 带 query（`?v=...`）与 URL 编码路径的归一；④ 同一图重复引用；⑤ 用户手改草稿后引用变化。扫描产出「被引用 chart_id 集合」。
  - **sweep 与导出解耦（防竞态）**：`_sweep_orphan_assets(project)` **删** `assets/` 下不在集合里的 png+json，但**绝不在导出路径里跑**。竞态：导出不持项目锁（main.py:1247），而 `create_chart` 写 `a.png` → 用户在 `append_report_draft` 插入前导出 → 若导出顺手 sweep、看不到引用就删 `a.png` → chat 随后插入 `![](assets/a.png)` → 悬空引用。故 sweep 只在**插图成功后机会性跑 / 显式 GC**，导出路径只读不删（§4.6）。
  - **grace period 防删在途**：即便非导出路径，sweep 也只删「未引用**且** mtime 早于 grace 窗口（如 >10min）」的 asset——保护「刚生成、还没来得及插入」的图。删除原子、只删确证未引用者。
- **文件树集成 —— v2.0 明确降级为「不进文件树」**：原设想把 `assets/` 作只读分组进文件树「能看能删」。实测这比「往 `FILE_SEMANTICS` 加一条」重得多：`list_workspace_files`（skill.py:1379）**只枚举 `*.md`**、`get_file_semantics`（1510）是**精确 key 匹配**（而 asset 路径是动态 `assets/<id>.png`，无法预置精确 key）、`/files` 读走 `read_text()`（1417）读 PNG 会乱码、前端 `WorkspacePanel`（79）也只经 `/files` 加载。要真做「能看能删」需要：前缀式语义 + 二进制枚举 + 二进制 list/read/delete API+UI，是一整块新工作。**故 v2.0 降级**：图是「预览内内联可见的管线产物」，**不作为可管理文件进文件树**；删图 = 模型/用户在草稿里删掉引用（`edit_file`）→ orphan sweep 清落盘文件。文件树里管理 assets 列 v2.1/backlog（§12）。二进制读只走新 `/assets` 路由（§4.5），不碰 `/files`。

### 4.4 正文引用

模型拿到工具返回的 `markdown` 串（`![结论](assets/x.png)`），用现成 `append_report_draft`（起草/续写）或 `edit_file`（改已有正文，锚点插入）塞进正文对应位置。**不新增插入机制**。因此：

- 插入引用吃 canonical draft 的 `MAX_CANONICAL_MUTATIONS_PER_TURN`（现 10）限额、read-before-write、mtime CAS——**天然继承**。
- 草稿始终是自描述的可移植 markdown（图是相对引用，不是内联 base64 blob——见 §11 被否方案）。

### 4.5 预览渲染（「实时长啥样」）

现状：`FilePreviewPanel.jsx` 用 react-markdown + rehype-raw 渲染草稿，已有自定义 `img` 组件（FilePreviewPanel.jsx:20 的 `markdownComponents`），但 `src="assets/x.png"` 是相对页面 URL 的死链——**当前无二进制资源路由**（`main.py` 只有 `/files` 文本读 873/881）。

改动：

1. **后端新增二进制资源路由** `GET /api/projects/{project_id}/assets/{asset_path:path}`，`Depends(require_project)` 走多租户归属校验（非属主 404），返回 `FileResponse`(PNG)。**路径根约定死**：`{asset_path}` 相对 `content/assets/`（即 `<id>.png`），前端重写时把 markdown src 的**前导 `assets/` 段剥掉**再拼 URL（路由根比 markdown 相对根深一层 `assets/`）。路径守卫：`Path.resolve()` + 确认 target 在 `content/assets/` 之内（拒 `..`/symlink 越界，对齐 W2-C 下载端点守卫）。
2. **前端 img src 重写（比「改一行」略重，Codex NIT）**：把相对 `assets/x.png` 重写为 `/api/projects/<currentProjectId>/assets/<encodeURIComponent 后的 path>?v=<mtime或hash>`（`?v=` cache-bust，改图后预览刷新）。**现状约束**：`markdownComponents` 是**模块级常量**、`img` 用裸 `<img src={src}>`（FilePreviewPanel.jsx:20），组件**当前无 `projectId` prop**（63），`WorkspacePanel` 也没传（372）。故需把 markdown 组件改成**按 projectId 记忆化的工厂/闭包**（或走 context），并给 URL 编码。绝对 URL / `data:` / `http(s)` 的 src 不重写（原样）。
3. **作用域（v2.0 收敛到草稿单一文件，Codex 审一致性）**：v2.0 图引用**只出现在 `report_draft_v1.md`**（chart 工具产的是草稿可插引用、模型只经 `append_report_draft`/`edit_file` 插进 canonical draft）——所以预览 src 重写**只对草稿**、§4.3 引用扫描**也只扫草稿**，两处口径一致。plan 文件虽用户可编辑但 v2.0 不放图。主聊天气泡（`MarkdownMessage.jsx`）v2.0 不渲染图——已知边界、非目标。

预览显示的就是最终 docx 里那张图（同一 PNG），天然所见即所得。

### 4.6 导出（「会不会走样」）

现状：`report_tools.export_reviewable_draft(report_path, output_dir)` 跑 `pandoc [report_path, "-o", tmp]`（report_tools.py:44），**无 `--resource-path`**、默认进程 cwd，且**返回码 0 即当成功、不查告警**（report_tools.py:52）。草稿里的 `assets/x.png` 是相对草稿的，pandoc 默认相对 cwd 解析 → 找不到。

改动（**localized 到 `report_tools.py`**）：

1. **导出前 asset 硬校验（新增门禁，不靠 pandoc 告警）**：用 §4.3 引用扫描列出草稿引用的全部 asset → 逐个确认落盘存在 → **有缺失就带「具体缺失清单」友好失败、根本不进 pandoc**。理由：pandoc 缺图可能 rc=0 只告警，产出**静默丢图**的 docx——对「承诺 docx 存活图」的功能不可接受。这是硬门禁。**导出全程只读 assets、绝不 sweep**（§4.3 竞态）。校验读当前草稿字节；校验→pandoc 间若草稿被并发改（新增引用），窗口内新引用图可能未校验——**接受此窄 TOCTOU**（最坏=该次导出缺图、重导出自愈，与 W2-C 既有导出 TOCTOU 接受口径一致）。
2. pandoc 命令加 `--resource-path <草稿父目录>`（即 `str(Path(report_path).parent)` = `content/`）→ `assets/x.png` 相对该目录解析命中，pandoc 原生嵌 PNG 进 docx。
3. **图宽控制**：引用串带 pandoc 图片属性 `![title](assets/x.png){width=80%}` 或渲染时定目标物理尺寸，避免整页超宽。具体值实施期实测定。
4. **导出后验证（纳入测试）**：一张真实图 → 引用进草稿 → 校验 → 导出 → 打开 docx 断言图已作为嵌入 media 存在且尺寸合理。全链 make-or-break 用例。

### 4.7 插图纪律（编辑规矩，最考验「是否咨询级」）

这块决定输出是「咨询级」还是「AI 味堆图」。分**写死的纪律**和**已定的策略选择**。

**写死进 prompt / 审查的纪律**（麦肯锡那套，无争议）：

- **一图一结论**：`title` 写结论、不写主题。样图那样「数字化转型增速领跑」，不是「各业务线营收」。图是用来证明标题那句话的。
- **不装饰**：无真实数据支撑不画；三五个数一句话能说清的，别硬画成图。
- **每张图必挂来源**：`source` 必填 → 进图脚 `来源：xxx` + sidecar 留痕；来源要能在 `data-log.md`/材料里找到（复用 R4 来源可信度、`_EVIDENCE_MARKERS` 语境）。

**已定的策略选择**（brainstorm 拍板）：

- **插图触发 = 混合**：模型写正文时按上面纪律**自主插**，用户也能随时「这加个图 / 这个改成折线」（自然语言 → 模型调工具）。既不失控也不累。
- **防编造 = 强建议（非硬门禁）**：纪律写死 prompt + `source` 必填挂图脚 + sidecar 数据留痕 + S5 审查维度兜底。**不逐点代码校验每个数字能否追到 data-log**——因为咨询图里最常见的恰是**派生数据**（CAGR 算出来的、市场规模 = 分段求和、预测外推），硬门禁「每个数据点必须在原始来源里逐字出现」会大面积误伤合法派生图、且实现脆。强建议在守住底线（可溯、留痕、可审）和不添堵之间取平衡。
- **S5 独立审查加一维「图表」（比「加段文本」重，接触点要认清——Codex 审）**：`independent_review.py:IndependentReviewAgent` 的 5 维度**硬编在 review prompt 与输出契约里**（independent_review.py:48），完备性锚点又硬编在 `SkillEngine`（skill.py:349）、被审查校验读取（independent_review.py:860）。加第 6 维要动的不止 prompt 文本，还有：① review prompt 的维度结构与输出契约；② **倾向不进 `_has_effective_independent_review` 生产硬门禁**（保持与「防编=强建议」一致的 advisory 姿态，不改动完备性锚点契约）；③ **sidecar 作为「预构建 grounding」注入、不走审查 agent 的 `read_file`**：后端扫草稿图引用 → 载对应 sidecar → 经 `trust_boundary.py` 的 `UNTRUSTED_DATA`+中和器包好 → 作数据消息注入（**镜像现有 `build_placeholder_grounding` 的做法**）；审查 agent 的 `read_file`（independent_review.py:836）返回 raw、**不特判 sidecar**，故不能靠它读——避免未框定的模型自撰内容进审查上下文。+ 图多时 token 上限/抽样（§12）。审的内容：结论式标题 / 来源可溯（读 sidecar 核对）/ 非装饰 / 数字无明显编造迹象。**诚实边界**：审查 agent 是文本 LLM，**看不到栅格像素**，只能审文本层，**无法验证渲染图形是否忠实于数据**。可接受的 v2 边界。

### 4.8 渲染器安全：并发、资源限制、错误归一（Codex 审新增缺口）

渲染器要能在多租户并发下安全跑，且防 DoS/劣质图。

- **并发/线程安全**：web 态聊天流跑在 `_CHAT_STREAM_EXECUTOR`（8 worker，main.py:909），多项目可并发；请求锁是 per-project（chat.py:3653），**跨项目渲染会真并发**。matplotlib 的 pyplot 全局状态机**非线程安全**——渲染器**必须用 Agg 后端 + 面向对象 API**（`Figure` + `FigureCanvasAgg`，**不碰 `pyplot` 全局**），字体注册在进程启动一次、渲染期只读。graphviz（v2.1）走 subprocess，天然进程隔离。
- **资源限制**（新叶子 `backend/chart_limits.py`，对齐 N6 的 `material_limits.py`）：max series / max 数据点 / max 类目 / label 长度 / 输出像素 + DPI 上限 / sidecar json 字节上限。超限 → 可控错误，不 OOM / 不卡 worker。
- **超时的诚实边界（Codex 审纠正）**：matplotlib 渲染是 CPU 密集的 C 代码、跑在 worker 线程里，**Python 线程级 timeout 杀不掉卡住的渲染**（信号 timeout 只在主线程有效）。故 v2.0 **不承诺硬性渲染超时**，改**靠上面的输入/输出 caps 把最坏渲染耗时压到亚秒级**（matplotlib 对合格且受限的输入无失控循环，风险更多是大图 OOM、被尺寸 cap 挡住）。若 caps 证明不够，**子进程隔离 + kill-on-timeout** 是 v2.1+ 加固项（graphviz 本就走 subprocess，那支天然可硬超时+kill）。
- **错误归一**：渲染任何失败（坏 data、超限、字体缺、超时）→ 统一 `ChartRenderError` → 工具返回 `{status:"error", output:"人话"}`，绝不把栈/半图泄给用户，绝不崩 chat 流。

---

## 5. 落地前提 / 部署依赖

- ⚠️ **必须往仓库塞一个中文字体**（思源黑体 Source Han Sans / Noto Sans CJK SC，`.otf`/`.ttf`），matplotlib 启动时 `font_manager.addfont` 指向它。**样图能出中文是因为开发机 mac 有 Hiragino；kr-web-01 那台 Linux 服务器默认没有中文字体，不塞则全是方框**——这是最容易漏的坑，字体是仓库资产不是可选项。
- **matplotlib + numpy** 进 `requirements.txt`；服务器 venv 安装（mac 用 `uv pip install`）。**桌面 PyInstaller 打包**（`consulting_report.spec` 的 `datas` 现含 skill/frontend/私有文件/pandoc/转换依赖，见 spec:16，**未含 matplotlib 的 mpl-data 与 CJK 字体**）：这两样必须显式加进 `datas`，否则打包态无字体/无 mpl-data 崩。体积增加可接受（已有 onnxruntime 等大依赖）。
- **v2.1 追加**：服务器 `apt install graphviz`（原生二进制），跟现在装 pandoc / libreoffice 一个性质，进部署 runbook。**桌面/Windows 的 graphviz 分发是独立难题**（原生二进制 Windows 打包麻烦）——v2.1 结构图**可能先 server-only**，桌面支持另评估（§12）。
- 部署 runbook（`docs/managed-proxy-deployment.md` 类）补：字体校验 + matplotlib import 预检 + （v2.1）`dot -V` 预检。

---

## 6. 不破坏的既有约束 / 不变式（禁改区）

实施逐条守，Codex review 逐条查：

- **DeepSeek 官渠工具调用兼容**：新增 `create_chart`/`create_diagram` = 新工具 schema，走现有 tool-call 序列化；**不显式发 `tool_choice`、assistant follow-up 回传非空 `reasoning_content`、不塞 null 字段**（CLAUDE.md「DeepSeek 官渠兼容」段）。system prompt 只**追加**插图纪律文本，不碰 provider message 结构。回归 `test_chat_runtime.py` DeepSeek 用例不动。
- **多租户隔离**：`/assets` 路由必经 `require_project`（非属主 404）；图落 per-uid 项目工作区 `content/assets/`；无跨租户资源泄漏。
- **R3 写门禁 / canonical draft**：插入引用 = 普通 `append_report_draft`/`edit_file`，吃 mutation 限额 + read-before-write + mtime CAS；生成 asset 不算 draft mutation、但要过 §4.1 preflight stage 门禁。用户不能经 `/files` POST 写 `assets/`（非白名单 → `UserWriteForbiddenError` 403，天然拒）。
- **计费口径（Codex 审纠正——不是「零计费影响」）**：渲染本身**不调 LLM/managed、不计费**（`metering` 只包 `chat.completions.create`，metering.py:327）。但**有 prompt-token 开销**：两个工具 schema + 系统提示纪律文本会**增加每轮 prompt token**（tools 每轮随请求发送，chat.py:2945，按 prompt 计费）；且 `_fit_conversation_to_budget`（chat.py:610）**只估 message token、不含 tool-schema token** → 加工具会隐性吃真实上下文预算。**对策（已定）**：纪律文本精简、tool schema 紧凑；**加一条 tool-schema token 上限回归测试**（对齐现有「方法论块 ≤2k tiktoken」断言的做法），把两工具 schema 的 token 成本钉在预算内；**不改** `_fit_conversation_to_budget` 计 tool-schema（避免动 budget 核心逻辑、回归风险大）。**准确表述 = 「无额外 managed 渲染调用，但有 tool-schema/prompt token 开销，由回归测试钉住上限」**。
- **信任边界**：工具 args 是模型自撰（对研究数据的断言），非外部不可信输入；`title`/`source` 渲进预览是模型自己的文本，非注入面；渲染产物是图片（无文本注入）；sidecar json 后端写。**唯一要当心的新面**：§4.7 把 sidecar json 喂回 S5 审查会话时，**必须按不可信数据框定**（`UNTRUSTED_DATA`/中和器，复用 `trust_boundary.py`）——sidecar 里有模型自撰的 title/source/data，回灌审查若不框定即是注入面。其余：若数据源自 N6 不可信材料，走「模型读→洗成 chart 数据」，由防编纪律 + 审查维度覆盖。
- **渲染并发安全**：见 §4.8（Agg/OO、无 pyplot 全局）——不得因加图破坏多项目并发流。
- **导出回归**：加 `--resource-path` + 导出前校验不得破坏现有无图报告的导出（`test_report_tools.py` 现有用例保绿）。

---

## 7. 失败模式与降级

- **渲染失败**（坏 data/spec、超限、字体缺失、超时、graphviz 未装）：统一 `ChartRenderError` → 工具返回 `{status:"error", output:"人话原因"}` → 模型据此告知用户 / 重试 / **降级成 markdown 表格**（数据图退化为表是合理兜底）。
- **服务器缺 matplotlib/字体**：友好错误（对齐 pandoc-missing 文案），不崩。
- **预览资源 404**（图被删/未生成）：img 显示 alt 文本（结论式标题本身可读），不白屏。
- **导出时图缺失**：**不依赖 pandoc 告警**——§4.6 的导出前 asset 校验是硬门禁，缺图直接带「具体缺失清单」友好失败、不产出静默丢图的 docx。

---

## 8. 组件与叶子边界（便于隔离测试）

- `backend/chart_render.py`（新叶子，只依赖 matplotlib+stdlib）：`render_chart(...)->PNG bytes`，**Agg 后端 + OO API、无 pyplot 全局**（§4.8），纯函数、可独立测。
- `backend/diagram_render.py`（新叶子；v2.1 含 graphviz subprocess 分支）：`render_diagram(...)->PNG bytes`。
- `backend/chart_limits.py`（新叶子）：渲染资源上限常量（§4.8），对齐 `material_limits.py`。
- `backend/chart_assets.py`（新叶子，依赖 tenant 路径助手）：落盘/sidecar 原子写、引用扫描契约、孤儿清理。
- `chat.py`：加两个工具 schema + dispatch + **preflight 门禁（复用 `check_report_writing_stage`+`check_outline_confirmed`）**（§4.1）+ system-prompt 纪律文本（薄接线，重逻辑在叶子）。
- `main.py`：加 `/assets` 二进制路由。
- `report_tools.py`：加 `--resource-path` + **导出前 asset 校验硬门禁**（§4.6）。
- `independent_review.py`：加第 6 审查维度（review prompt 维度结构 + 输出契约 + **sidecar 预构建 grounding 框定**（不走审查 read_file）+ token 上限，§4.7）——**非「仅加文本」**。
- 前端：`FilePreviewPanel.jsx`（img 组件改 projectId 感知工厂 + src 重写）。

每个叶子有明确「做什么/怎么用/依赖谁」，可单独理解与测试。

---

## 9. 分期计划

- **v2.0（一个引擎打穿，最快能用）**：`create_chart` 全数据图 + `create_diagram` 签名结构图（matplotlib）+ 尾巴全套（`chart_render.py` / `chart_limits.py` / `chart_assets.py` / `/assets` 路由 / 预览 src 重写 / 导出前校验 + pandoc resource-path / S5 图表维度 / CJK 字体 + matplotlib 部署 + PyInstaller 打包）+ 插图纪律 prompt + preflight 门禁。
- **v2.1（加 graphviz 那支）**：`create_diagram` 的 `flowchart`/`org_chart`/`tree`（`diagram_render.py` graphviz 分支 + 拓扑 DSL + `apt install graphviz` 部署 + 桌面分发评估）。
- **backlog**：桑基图、原地按 chart_id 编辑、assets 进文件树可管理、聊天气泡渲染图、图 N 自动编号（导出排版期）。

---

## 10. 测试策略

- **后端**：
  - `test_chart_render.py`（新）：各 kind 渲染出非空 PNG、CJK 不方框（字体已注册断言）、坏数据/超限抛 `ChartRenderError`、Agg/OO 无 pyplot 全局（并发渲染不串图/不崩）。
  - `test_chart_assets.py`（新）：落盘/sidecar 原子写；引用扫描契约（md 图片 / raw-img / `?v=` query / URL 编码 / 重复引用 / 手改草稿）；sweep 只删未引用 + 误删防护。
  - `test_main_api.py`：`/assets` 路由属主放行 / 跨租户 404 / `..` 越界拒 / 二进制正确返回。
  - `test_report_tools.py`：resource-path 命中嵌图（导出 docx 断言含嵌入 media）+ **导出前 asset 校验**（缺图带清单失败 / 齐全放行）+ 无图报告不回归。
  - `test_chat_runtime.py`：两工具 schema + dispatch + **preflight 门禁复用 `check_report_writing_stage`+`check_outline_confirmed`+`check_no_fetch_url_pending`（阶段不合格 / 大纲未确认 / 仅 web_search 未 fetch_url → 拒生成）**；**两工具 schema token 成本 ≤ 预算上限**（回归钉死，§6）；DeepSeek 官渠用例不回归；插入 = 普通 mutation 吃限额；生成不预扣 mutation。
  - `test_skill_engine.py`：生成 asset 落 per-uid 工作区、preflight 阶段判断（若逻辑入 skill）。
  - `test_independent_review.py`：第 6 维度（sidecar 不可信框定 + 读 sidecar 核对，不破坏 5 维锚点契约）。
- **前端**：`filePreviewPanel.source`（img 组件 projectId 感知工厂、src 重写、绝对 URL 不重写、cache-bust、URL 编码）。
- **全链 E2E（手动/脚本）**：造数据 → create_chart → 插草稿 → 预览显示 → 导出前校验 → 导出 → docx 开图。

---

## 11. 被否决的方案（存档，防回潮）

- **内联 base64 data-URI**（`![](data:image/png;base64,...)` 直接进草稿）：预览/导出都零新路由（pandoc 也认 data URI）。**否**：base64 把草稿 .md 撑爆、每次 read/edit/摘要都拖着 blob、污染模型上下文与 mutation 逻辑。资源文件 + 相对引用更干净。
- **模型直接写 matplotlib/python 代码 + 后端沙箱执行**：最灵活。**否**：代码执行安全面、样式不一致、失败模式多、重。违背「系统承担复杂性、输出稳定专业」。改由结构化工具 + 后端固定手艺。
- **mermaid（模型写文本图）**：模型最省心。**否**：导出要 headless chromium，重且违背项目「避浏览器依赖」惯例。
- **render-at-export（草稿里存 spec、导出时才渲染）**：草稿更轻。**否**：预览要另跑一套渲染、失去「预览=导出同一张图」的所见即所得；render-at-generation 一次渲染两处复用更简单。
- **assets 进文件树可管理（v2.0）**：用户能在文件面板看/删图。**否（降到 backlog）**：需前缀语义 + 二进制枚举 + list/read/delete API+UI，重（§4.3）；v2.0 删图走「删草稿引用 + orphan sweep」足够。

---

## 12. 开放问题 / 待实施期决

1. **图宽/DPI 的具体值**：pandoc `{width=N%}` vs 渲染目标物理尺寸，实测 docx 效果后定。
2. **签名结构图的版式清单最终集**：2×2/价值链/流程/路线图/金字塔是否够覆盖，还是加「三地平线」「波士顿矩阵」等专名版式——实施期结合真实报告样本定。
3. **孤儿清理节奏**：插图后机会性 sweep vs 显式/手动 GC cadence——看试用期磁盘增长决定。（**导出永不 sweep** 已是 §4.3 硬规则，不在此权衡内。）
4. **sidecar 进 S5 审查上下文的 token 预算**：读全部 sidecar 核对 vs 抽样，视图数量与 token 成本定。
5. **原地按 chart_id 编辑** 是否提前到 v2.0：取决于「改图」在真实使用中的频率反馈。
6. **assets 进文件树可管理**（前缀语义 + 二进制枚举 + list/read/delete）是否从 backlog 提前——看用户是否真需要「文件面板里看/删图」。
7. ~~tool-schema token 开销是否扩 `_fit_conversation_to_budget`~~ **已定（§6）**：加 schema-size 回归测试钉上限、不改 budget fitter；实测值实施期填。
8. **v2.1 graphviz 桌面/Windows 分发**：server-only 还是啃 Windows 打包（§5）。

---

## 附录 A：样图（brainstorm 阶段已渲染验证）

brainstorm 期用 matplotlib + 系统 CJK 字体（Hiragino）渲染了三张样图验证质量下限：分组柱状（各业务线营收对比，带数据标签）、趋势折线（市场规模 + 预测虚线 + CAGR 标注）、利润桥瀑布（正负分色 + 连接线，120→42 净值正确）。全部中文干净、海军蓝配色（对齐前端 redesign `#1B2A4A`）、达到「能进报告」下限。证明：**模型全程不写代码，只给结构化数据，后端出图**——即本 spec 的 `create_chart` 后端手艺。
