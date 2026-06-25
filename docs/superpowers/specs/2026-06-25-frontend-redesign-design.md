# 前端 UX 翻新设计 Spec（2026-06-25）

> 修订记录：v1 初稿 → v2 吸收 Codex 双轨审首轮（spec 轨 7 + quality 轨 5 BLOCKER + NIT），全部核实属实并落入下文（标 `[R1]`）→ v3 吸收双轨复审（spec 轨 4 + quality 轨 1 BLOCKER + NIT，标 `[R2]`）→ v4 吸收终审红队 quality 轨（**APPROVED**）4 条 NIT → v5 吸收 spec 轨终审红队 2 BLOCKER（WorkspacePanel 材料库 tab、RollbackMenu 高级回退）+ 2 NIT（StageAdvanceControl checkpoint 映射、ThinkingBlock 样式面），均标 `[R3]`。

## 1. 目标与定位

把 `consulting-report-agent` 的 Web 前端从现有「深紫黑底 `#0f0f23` + 薄荷绿 `#64ffda` + emoji 图标的 AI 味风格」整体翻新为 **「精致 macOS 工艺感 + MBB 专业权威」** 的设计系统：主色海军蓝 `#1B2A4A`，浅 / 深双主题，无 emoji、线性 SVG 图标、等宽数字、收敛圆角。

设计参考来自 `design_handoff_frontend_redesign/`（claude design 交付的高保真原型 + README）。

**核心约束（贯穿全文）：业务功能、流程、接口、状态机完全不变，只替换视觉与组件外观。** 所有阶段名、连接模式、字数阈值、checkpoint、拖动行为、鉴权、计费、流式契约严格对齐现有后端契约。这是一次**纯前端、零后端改动**的改造。

### 验证重心
**Web 模式（`run_web.py` + 浏览器）为主**——生产部署在 kr-web-01（`consulting.z0y0h.work`，CF 后面，国内同事使用）。桌面打包态（PyInstaller）作为顺带兼容。**[R1] 像素验收除 mac 外，必须加一台 Windows Chrome 截图**——国内同事大概率 Windows，`Microsoft YaHei` 与 mac `PingFang SC` 的字重/metrics 差异明显，中文走系统栈的取舍要在 Windows 上实测，不能只在 mac 看。

## 2. 真值源与两条不退化铁律

原型与生产代码各管一半，冲突时按下面两条铁律裁决：

1. **视觉真值源 = 原型 + README**。颜色、字号、字重、间距、圆角、阴影、hover/active、文案为最终值，像素级还原。
2. **功能真值源 = 现有生产代码**。原型是高保真**视觉** mock，会为了画面干净省掉真实控件（输入框、下拉、校验态、边缘态、生成中状态）。**原型画得比生产少时，以生产为准**——把真实控件按新视觉语言重绘，**绝不因为原型没画就删功能**。

### 2.1 已核实「原型 < 生产」的功能缺口（改到对应组件时必须保住，逐条 source 定位）
- **AdminPanel 额度列**：生产是可编辑 `<input defaultValue={u.daily_cap_yuan} onBlur={setCap}>`（`AdminPanel.jsx:77-80`），原型画成纯文本。必须保住可编辑 + `onBlur` 提交。AdminPanel 其余：改密（`prompt` 流）、禁用/启用、轮换邀请码、保存允许域名（含默认只读 hosts 提示）——全保。
- **[R1] StagePanel 阶段条是 7 段或 8 段，由后端 `delivery_mode` 决定**，不是固定 8 段：`shouldShowPresentationStage(deliveryMode)`（`workspaceSummary.js:55-59`）真时 `REPORT_AND_PRESENTATION_STAGES`（8 段，含 S6 准备演示），否则 `REPORT_ONLY_STAGES`（7 段，无 S6）。`StagePanel.jsx:22-25` 据此选。原型只画了 8 段静态图，实施时**必须保留按 deliveryMode 分支**。
- **[R1] ChatPanel composer 远超「附件 chip + textarea + 发送」**，生产真实能力（全保）：
  - 生成中「停止」按钮 + abort（`ChatPanel.jsx:1170` 停止、`306/453/616` abortController、停止后填「已停止生成」`601`）。
  - 已上传材料的可选 chips + 转写/转换状态（`materials.map` + `conversionStatusChip` `1088-1089`、`toggleMaterialSelection`）。
  - 待发送附件分流：图片 vs 文档不同入库路径（`buildTransientAttachmentsPayload`），`attachment_transcribed` SSE 关联（`402/431/444/565`），历史轮图片转写/失败标记。
  - 粘贴文件进 composer（`handleComposerPaste` `1147`）、拖拽上传 `dragActive`。
- **[R1] Sidebar 项目删除确认弹窗**（`Sidebar.jsx:62` 触发、`154-165` 弹窗）在原 §3 屏幕清单里没单列，容易漏换皮——纳入侧栏批次。
- 其余组件改皮前同样**先以生产代码盘点全部交互能力**，原型未画全的（下拉、校验态、空态、错误条、隐藏按钮、生成中态）一律以生产为准保留。

## 3. Token 地基

### 3.1 落地架构（方案 A：CSS 变量单一真值源 + Tailwind 语义类）

- **`frontend/src/index.css`**：`:root {}` 定义浅色全套 token，`.dark {}`（挂 `<html>`）覆盖成深色全套。删除现有写死的 `body{background:#0f0f23}`、`.prose-dark` 暗色专用块、`color-scheme:dark`，全部改为变量驱动（浅深自适应）。
- **[R1] token 用 channel（通道）形式定义，支持 Tailwind 透明度修饰符**：
  - 变量存**空格分隔的 RGB 通道**：`--card: 255 255 255;`（浅）/ `.dark{ --card: 38 39 44; }`。
  - Tailwind 映射成 `card: 'rgb(var(--card) / <alpha-value>)'`——这样 `bg-card`、`bg-card/50`、`border-border/40` 全部可用。
  - **理由**：现有代码已大量用透明度（`ConfirmDialog.jsx:72`、`ChatPanel.jsx:980` 等遮罩/半透明），若直接 `card:'var(--card)'`（完整色值）则 `/50` 修饰符失效、生成废 CSS。
  - **[R2] scrim（遮罩）特殊处理**：scrim 在设计表里**自带 alpha**，若只做 `rgb(var(--scrim) / <alpha-value>)` 则 `bg-scrim` 默认会变不透明（盖死整屏）。约定：scrim 存通道形式，**所有遮罩统一写 `bg-scrim/45 dark:bg-scrim/60`**（浅深各自的遮罩透明度），并把这处 `dark:` 列为 §3.1「禁大量 dark:」的**明确允许例外**（遮罩是唯一例外，其余仍走 token 自动切）。
  - 设计 token 表（README 38 项）逐项转通道形式；语义色（success/warn/error）同样建 token。
- **`frontend/tailwind.config.js`**：`theme.extend` 中
  - `colors`：全部语义名 → 通道映射（见上）。
  - `fontFamily`：`sans → ['Hanken Grotesk','PingFang SC','Microsoft YaHei','Noto Sans SC',system-ui,sans-serif]`；`mono → ['IBM Plex Mono',monospace]`。
  - `borderRadius`：卡片 11 / 窗口弹窗 14 / 按钮输入 7–8 / chip 5–6 / 图标按钮 6–7px。
  - `boxShadow`：`card`/`popover`/`float` 三档（README 给定值）。
  - `fontSize`：10.5 / 11 / 11.5 / 12 / 12.5 / 13 / 13.5 / 15 / 17 / 18px 阶。
  - `darkMode:'class'`（配合 `<html>.dark`）。**[R1] 但它只作极少数 token 无法表达场景的备用**——颜色一律走 token，**禁止组件里大量混用 `dark:*`**，否则 token 单一真值源会漂移（source guard 扫 `dark:` 出现次数设上限）。

### 3.2 主题切换状态与首屏防闪（[R1] 新立一节，消除原 §4.1 矛盾）

主题是**本次唯一允许新增的状态/effect**（其余组件业务逻辑一行不动，见 §4.1 精确定义）：

- **`frontend/src/utils/theme.js`（新，纯函数 + 副作用分离）**：`getInitialTheme()`（读 `localStorage['cra:theme']`，缺省 `'light'`）/ `applyTheme(t)`（给 `documentElement` 加去 `.dark`）/ `toggleTheme()`（翻转 + 持久化）。
- **状态归属**：theme state 放 `App.jsx`（顶层），通过 prop 下传给 `Sidebar` 的 ☀/☾ 控件；控件 onClick 调 toggle 并 setState，保证按钮 label/icon 重渲染。**默认浅色。**
- **[R1] 首屏防 FOUC**：必须在 `index.html` 的 `<head>` 放一段**极小同步 bootstrap 脚本**——在 React/CSS 首次绘制前读 `localStorage['cra:theme']` 并给 `<html>` 加 `.dark`，包 `try/catch`。等 React/App 执行再 apply 已经太晚，深色用户会看到浅色闪一下。`_SPAStaticFiles` 的 no-cache 头是对的（`backend/main.py`），但它不解决主题首屏闪烁——别动它、另加 bootstrap。
- **[R2] bootstrap 精确规格**（plan 锁死）：inline `<script>` 必须在 `<head>` 内、**排在 Vite module script 之前**；`try/catch` 包裹；key 精确 `cra:theme`；**只认值 `'dark'` 才加 `.dark`，其余（含缺省/异常）一律移除 `.dark`**（与 `applyTheme` 同语义，避免两处判定漂移）。source guard **不只验存在、还要验顺序**（script 在 module 之前）。
- **☀/☾ 用线性 SVG 图标**（不是 emoji 文字），进 §4.3 图标模块。

### 3.3 字体托管（[R1] 修正 Vite 管线表述）

- **自托管** Hanken Grotesk（拉丁，400/500/600/700）+ IBM Plex Mono（400/500）的 woff2。
- **[R1] 放 `frontend/src/assets/fonts/` 并在 `src/index.css` 里 `@font-face { src: url('./assets/fonts/xxx.woff2') }` import**——这条路径**才**走 Vite asset 管线、带 hash、落 `/assets/*` immutable 缓存。**不要放 `public/fonts/`**（public 不经 Vite hash，要手动版本化 + 自定缓存策略，徒增复杂）。`index.html` 去掉 Google Fonts `<link>`。
  - **[R2] 相对路径是 `./assets/fonts/`（不是 `../`）**——本仓库 `index.css` 直接位于 `src/` 下，`@font-face` url 相对 CSS 文件解析，`../` 会错指到 `frontend/assets/fonts/` 导致 build/解析失败。
- **[R1] FOUT**：`font-display:swap` 自托管首帧会先系统字、字体到位再换（FOUT），生产可接受；但**像素截图验收前必须 `await document.fonts.ready`**，否则截到系统字态误判。
- **[R1] 字体资产来源**：从 Google Fonts（开放字体许可 OFL）下载对应 woff2，文件名记入 plan；woff2 子集化可选（拉丁本就小，~100KB / ~80KB，不强制子集）。
- **中文不自托管 Noto Sans SC**（CJK 全字库 2–7MB 过重），走系统 CJK 栈。**[R1] 不写「视觉损失≈0」**——YaHei/PingFang 字重 metrics 有差异，靠 §1 的 Windows + mac 双截图验收兜住。

## 4. 组件改造策略

### 4.1 铁律（[R1] 精确定义「可改范围」，消除「一行不动」的不可执行表述）
**保留业务逻辑，重绘表现层。** 具体：
- **绝不动**（业务逻辑）：axios 调用、流式 fetch / SSE 解析、状态机、refs、effect 依赖数组、imperative handle、`utils/` 纯函数、send/abort/队列逻辑、信任边界。
- **按需改**（表现层）：JSX 结构与 className；含 emoji 的**显示文本分支**（emoji 不只在图标 class，也在内容字符串里，如 `ChatPanel.jsx:903`）；带旧色的 **inline style 对象 / JS 里的颜色**（如 `toast.js:6`、`Sidebar.jsx:111` 进度条 `style`）；展示型 token helper；以及 §3.2 那**一个**新增的 theme state/effect。

每个组件改皮前**先以生产代码盘点逻辑面 + 全部交互能力**（§2.1 / §6），改后逐条核对都在。emoji → 内联线性 SVG（stroke 1.7–2.4）。

### 4.2 改动顺序（先地基、由简到繁、风险递增）
| 批次 | 内容 | 备注 |
|---|---|---|
| 0 地基 | `index.css`（通道 token）+ `tailwind.config.js` + 字体 + `utils/theme.js` + `index.html` 防闪 bootstrap | 不碰组件，先让现有界面在浅深两套下 build + 渲染，验对地基再动组件 |
| 1 外壳 | `App.jsx` 三栏骨架 + 主题 state 接线 | ErrorBoundary、init effect 依赖、拖宽全部原样保留 |
| 2 认证 | `Login.jsx` / `ForcePasswordChange.jsx` | 最简单独立，先验 token |
| 3 侧栏 | `Sidebar.jsx`（账户区重组 + 头像/管理员 tag/主题切换 + 删除确认弹窗换皮） | 见 §4.4 |
| 4 对话 | **[R1] 先 `MarkdownMessage.jsx` / `ThinkingBlock.jsx`，再 `ChatPanel.jsx` 按区域分段**（header / 消息气泡 / 附件 chips / 上下文用量 / composer），**不一次性重写 1187 行、不移动 streaming/send/队列逻辑** | 最重，放中段 |
| 5 工作区 | `WorkspacePanel` / `StagePanel`（7/8 段分支）/ `StageAdvanceControl` / `FilePreviewPanel` / `RollbackMenu` / `ConfirmDialog` | 拖动 + 脏离开守卫密集区 |
| 6 弹窗/浮层 | `ProjectCreateModal` / `SettingsModal` / `AdminPanel` / `IndependentReviewDrawer` | 浮窗拖动、遮罩、流式审查，最后收口 |

### 4.3 共享图标模块（新）
`frontend/src/components/icons.jsx`：把反复用的线性 SVG（加号、回形针、发送、齿轮、☀/☾、盾牌、文件、删除、绿勾、关闭、停止…）收成一组可复用图标组件。单一职责、干净边界、纯展示。最终粒度（逐个内联 vs 全收进模块）按复用度在实施时定。

### 4.4 Sidebar 账户区是**重组**不是新功能
现有 `Sidebar.jsx:75-150` 已含：连接卡、用户名+登出（仅 `uid!=='local'`）、今日额度行+进度条+百分比、`👤 用户管理`（仅 admin）、`⚙ 连接设置`。redesign 做的是：拆成清晰层级（独立账户行 + 头像 + 管理员 tag / 连接卡含今日额度条 / 底排连接设置 + **新增主题切换**）；emoji（`🗑`/`👤`/`⚙`）换线性 SVG；**真正新增只有头像圆块 + 主题切换控件**。**底层判断条件照搬**：登出仅 `uid!=='local'`、额度行 `daily_cap_yuan` 是数字才显示（含 local）、admin-only。**[R2] `overCap/ratio` 阈值逻辑照搬，但颜色映射换 semantic token**（`barColor` 不再保留 `#ef4444`/`#f5a623`/`#64ffda`，改 error/warn/accent token；<80% accent、≥80% warn、overCap error）。

### 4.5 [R1] 非组件样式面也要变量化（原 spec 漏）
这些不在 `components/*.jsx` 里但带旧色，必须一并纳入双主题验收：
- `utils/toast.js`（`react-hot-toast` 自定义色，`toast.js:6-7`）。
- **代码高亮**：`FilePreviewPanel.jsx:10` 固定 import 深色 highlight 主题——需改成浅深可切。**[R2] 实现避坑**：**不要 import 两个全局 highlight CSS**（无 scope 时「后 import 覆盖先 import」，浅深会互相打架）；优先用 **CSS-变量驱动的 highlight 主题**，或把两套规则**显式 scope 到 `:root` / `.dark` 前缀下**。
- **Markdown 渲染**：`MarkdownMessage.jsx:17` 起表格 / 工具卡 / 链接 / code / blockquote 全硬编码暗色——**项目无 Tailwind typography 依赖**（`prose/prose-invert` 不是可靠来源），现有 markdown 样式是 `.prose-dark` + 内联自定义，需逐项改成变量驱动、浅深都对。KaTeX（`rehype-katex`）公式样式同样过一遍。
- **[R3]** `ThinkingBlock`（「思考过程」折叠块）：组件只输出 `thinking-block`/`thinking-content` 类，**真实样式在 `index.css:74`**——token 化双主题，同时保留 collapsed `<details>` 与 `unescapeThinkingContent` 行为（`ThinkingBlock.jsx:4`）。
- `ErrorBoundary.jsx` / loading 态 / 滚动条 / 原生控件 `color-scheme` 浅深自适应。

## 5. 不变式保护清单（换 JSX 时碰了就翻车 / 退化，有 source-guard 测试锁死）

逐组件改后逐条核对：

**App.jsx / 启动**
- init effect 依赖**必须** `[authUser?.uid, authUser?.must_change_password]`，**绝不**退回 `[authUser]`（额度刷新造新引用 → 整树卸载重挂 → 黑屏闪 + 消息丢）。`appInitGating.source.test.mjs`。
- `main.jsx` 外层 `ErrorBoundary` 包 `<App/>` 不能去。
- **[R1]** `index.html` 主题防闪 bootstrap 不能漏（§3.2）。

**拖动四处（纯函数 `workspaceResize.js`/`filePanelLayout.js` 不重写，直接复用）**
- 工作区拖宽：容器 ref 挂**排除固定宽 Sidebar 的内层 wrapper**；clamp 按可调区预留 `MIN_CHAT`；callback-ref 挂载重夹 + window resize 重夹。`workspaceResize.source.test.mjs`。clamp 常量 `DEFAULT 448 / MIN_WS 320 / MAX_WS 1100 / MIN_CHAT 360`。
- 文件树上下拖（`DEFAULT 30 / MIN 15 / MAX 70`）、独立审查浮窗 header 拖、拖拽上传：cleanup ref + 卸载兜底，照搬。

**ChatPanel（含 [R1] 流式契约）**
- 输入框乐观清空：`restoreInputForRetry` **双重守卫**（`sendSeqRef` 序号 + `setInput(prev=>prev===''?...)`）。`chatPanelComposerClear.source.test.mjs`。
- `forwardRef + useImperativeHandle` 暴露 `triggerSystemTurn`/`dropPendingReviewTriggers`；`pendingTriggerQueue`；拖拽 `dragActive`；**[R1] 生成中「停止」按钮 + abort 全保**（§2.1）。
- **[R1] 流式 fetch 必带 `credentials:'include'`**（`ChatPanel.jsx:464`）——web 态靠 httpOnly cookie，漏了登录态流直接断。
- **[R1] SSE 心跳注释行（`: keepalive`）必须被解析器忽略**，不能当数据帧。`sseHeartbeat.test.mjs`。

**额度刷新（Sidebar + App）**
- 三重守卫缺一不可：`quotaRefreshSeqRef` 序号 + `uid` 匹配 + `skipUnauthedHandler`。`sidebarQuota.source.test.mjs` / `apiUnauthed`。

**文件预览 / 工作区（含 [R1] CAS 契约）**
- 脏离开三按钮守卫（`fileEditState.guardLeave` 返 allow/confirm/block）；ref 链 App→WorkspacePanel→FilePreviewPanel（`attemptLeave`/`isEditing`）。
- `WorkspacePanel.loadFile` **同步 `setCurrentFile` 再异步 GET**；`latestFileRequestRef` 丢乱序响应。
- completion 靠 run-bound `{run_id, report_mtime_ns}`；`shouldApplyProjectResponse` 项目切换守卫。
- **[R1] 用户文件保存 `base_mtime_ns` 全程 opaque 字符串透传**（`WorkspacePanel.jsx:188` / `FilePreviewPanel.jsx:152`）——后端 pydantic 拒 number（422），别转 Number/int。

**[R3] WorkspacePanel 三 tab + 材料库契约**（原型按页换皮极易把材料库退化掉）
- **三 tab（阶段 / 文件 / 材料）全保**；**离开「文件」tab 走 dirty guard**（脏编辑态切 tab 弹三按钮）。
- 材料库（`WorkspacePanel.jsx:250/325`）：材料列表、空态、工作目录展示全保；删除材料仍 `DELETE /materials/{id}` 并触发 `onMaterialDeleted` + `onProjectMutated` 刷新链路（额度/工作区联动）。加 source guard 锁三 tab + 删除回调。

**[R3] RollbackMenu 高级回退契约**（别只换皮一级项、丢掉高级回退）
- 除 `ROLLBACK_HIDDEN_STAGES`/`getFirstLevelOption` 外，**保留高级展开区 + `getAdvancedRollbackOptions`**，及硬编码 clear 入口：`s0-interview-done?action=clear`、`outline-confirmed?action=clear`、`delivery-archived?action=clear`（`RollbackMenu.jsx:127`）。现有测试主要锁纯函数、不锁组件渲染这些入口——加 source guard 锁高级回退入口渲染，否则阶段回退能力丢失。每个 clear 仍弹 `ConfirmDialog`。

**StagePanel / 阶段推进（[R1]）**
- **阶段条 7/8 段由 `delivery_mode` 决定**（§2.1），保留 `shouldShowPresentationStage` 分支。
- `StageAdvanceControl` **只 POST checkpoint 后等后端刷新**，不得本地读 `deliveryMode` 自行推/算阶段。
- 按钮门禁：`isS4ReviewButtonVisible`（字数≥`report_word_floor`）、`isS1ConfirmOutlineEnabled`、`methodology_declared` flag、`ROLLBACK_HIDDEN_STAGES={S0,S1}`、`getFirstLevelOption`。阶段中文名走 `STAGE_NAMES`，**不暴露 S0–S7 代码**。
- **[R3] StageAdvanceControl 精确 checkpoint 映射照搬**（`StageAdvanceControl.jsx:48`）：S1 `outline-confirmed` / S4 `review-started` / S5 `review-passed`（或 clear `review-started` 回退）/ S6 `presentation-ready` / S7 `delivery-archived`；`stageToolsRunning` 时 S5 双按钮 disabled。换皮别改 action key / checkpoint 名。

**Login / 改密 / [R1] 全局错误展示**
- 错误 `detail` **必经 `normalizeAuthError`** 转字符串（422 是数组，直塞 React 子节点=白屏）；提交前客户端校验长度（用户名≥3、密码≥6）+ 提交 trim 用户名。`authError.test.mjs` / `loginErrorHandling.source.test.mjs`。改密 `validateNewPassword`（≥8）；`must_change_password` 硬门。
- **[R1] 全局规则**：**任何把后端 `detail` 渲染给用户的地方都不能把对象/数组塞 React 子节点**（CLAUDE.md 硬约束）。`IndependentReviewDrawer.jsx:87/300` 有同类风险点——本次一并归一（抽共享 `normalizeApiErrorDetail` 或确保只走字符串拼接 toast/alert）。

**独立审查浮窗（含 [R1] credentials + [R2] 完整状态机）**
- 前端生成 `run_id`（窗口全程不变）；open-effect 守 `isOpen` 上升沿；409 指数退避上限 5；completed 自动关**不** discard、用户关才 discard；`trigger_metadata` 全程 opaque 字符串**禁转 Number**。
- **[R1] stream + discard 的 fetch 都带 `credentials:'include'`**（`IndependentReviewDrawer.jsx:69/191`）。
- **[R2] 状态机其余分支全保**（`IndependentReviewDrawer.jsx:131/299`、`independentReviewDrawer.source.test.mjs:99`）：断流 / 收到 `[DONE]` 但无 `review-completed` → 转**可续审 errored 态**；errored 态**留存不自动关**、解锁 supplement 输入框；「继续审查」`resume` 带 supplement textarea 内容续审；**发起新审查前 drop 旧同类 pending trigger**（`WorkspacePanel.jsx:144`）。

**[R2] ProjectCreateModal / 创建 payload（web 多租户契约）**
- payload 从主题派生 `name`，且 **web 态绝不发送 `workspace_dir` / `initial_material_paths`**（后端按 `model_fields_set` 拒收 → 400）。`projectCreatePayload.js:13`、`projectCreateModal.test.mjs:39`。换皮别动 `projectCreatePayload` 的字段构造。

**[R2] SettingsModal / custom 契约（B3 接口，非纯视觉）**
- `managed_base_url` **只读展示、不得提交**（服务端强制回默认）；custom 模式三字段（API Key / API 地址 / 模型）+ 「获取模型」+ `custom_context_limit_override` 全保。`SettingsModal.jsx:4`、`settingsModal.source.test.mjs:5`。

**[R2] ChatPanel 清空对话生成中守卫**
- 「清空对话」header 图标按钮：handler 在 `loading || uploading` **早返**，按钮 `disabled`（`ChatPanel.jsx:288`、`chatPanelClearGuard.source.test.mjs:7`）。换 header 图标时别弄丢这层守卫。

**[R1] ConfirmDialog 可访问性 source-guard**（改皮别弄丢）
- `role="dialog"` + `aria-modal` + ESC 关闭 + 打开时 focus 到取消按钮 + 关闭恢复焦点（`ConfirmDialog.jsx:29` 等）。

**字体 / SPA**
- 自托管字体走 Vite asset 管线（带 hash），`index.html` 去 Google Fonts `<link>`；别碰 `_SPAStaticFiles` no-cache 头逻辑。

## 6. 测试与验证

三道独立验收闸：

1. **功能不退化（自动 + 人工盘点）**
   - 每组件改前从生产代码列「逻辑面 + 交互能力清单」（handler/输入/校验/边缘态/生成中态），改后逐条核对都在（§2.1）。
   - 全套前端 `node --test tests/` 保持绿。**[R1] source guard 处理原则**：断言**业务逻辑**的原样过；断言**旧表现结构/旧色值**的（如 `sidebarQuota.source.test.mjs:49` 锁了 `#ef4444`）**迁移成语义断言**（断 overCap→错误色 token 类名，而非保留硬编码 hex）——这是「只对齐不放宽」在颜色迁移下的正确落法。
   - `npm run build` 必过。后端零改动。
2. **[R1] 新增自动化护栏（防回归）**
   - 旧 palette 扫描：**[R3] 扫全部任意值颜色工具类**（`bg-[#`、`text-[#`、`border-[#`、`ring-[#`、`placeholder-[#`、`fill-[#`、`stroke-[#`）**+ JS/inline style 里的裸 hex**（如 `Sidebar.jsx:111` 进度条 `style` 的 `#ef4444`），旧紫/薄荷绿 hex、emoji 字符 → allowlist 制 source guard（命中即失败）；**allowlist 仅放 `index.css` 的 token 定义 + `tailwind.config.js`**。**[R2] emoji 扫描只盯「可见渲染 UI 文本」**，必须 allowlist 协议/解析常量——`chatPresentation.js:138` 等 legacy 工具日志前缀解析里的非 ASCII 标记**不能为了「无 emoji」误删**（删了破坏后端 SSE/tool 文本兼容）。
   - Tailwind token source guard：颜色 token 必含 `<alpha-value>`（防有人退回完整色值丢透明度）。
   - **[R3]** `index.html` 主题 bootstrap source guard **验顺序与语义**（不只存在）：script 在 `<head>` 内、**排在首个 Vite module script 之前**、含 `try/catch`、key 精确 `cra:theme`、**只 `=== 'dark'` 才加 `.dark`**。
   - `dark:` 出现次数上限 source guard（防 token 漂移，§3.1；遮罩 `dark:bg-scrim/60` 是 §3.1 允许例外，计入 allowlist）。
   - `utils/theme.js` 纯函数测（getInitialTheme/toggleTheme/applyTheme 逻辑）。
   - **[R3] 对比度测试按文本角色分阈值，别一刀切 WCAG 4.5**：原型弱文本 token 是**有意低对比**（`t3` 浅底约 3.26:1、深底约 2.98:1，见 README token 表），一刀切 4.5 会误报。**正文/主文本/操作文字测 ≥4.5；弱元信息/占位（`t3`）单独按更低阈值或豁免**。
3. **像素级视觉还原（人工，[R1] web + 双平台）**
   - `run_web.py` 起服务，chrome-devtools 逐屏截图，**浅深两套**对照 `Prototype-standalone.html`；**截图前 `await document.fonts.ready`**。**[R3] 并断言 Hanken Grotesk / IBM Plex Mono 字族确实 `status==='loaded'`**——否则字体 URL 写错时仍会截到稳定的系统 fallback、看不出问题。
   - **mac + Windows Chrome 都截**（验中文系统栈，§1）。
   - 核对：无 emoji、无紫、线性图标、等宽数字、圆角≤14px、主色 `#1B2A4A`、深色强调 `#7E97CC`、hover 态。
4. **Codex 双轨审（每组件 commit 后）**：spec 轨 + quality 轨独立审，「审→修→再审」到 APPROVED。§5 不变式 + §2.1 功能清单 = 核对基准。

## 7. 范围外 / 风险 / 待决

- **范围外**：任何后端 / 接口 / 状态机 / 计费 / 鉴权改动；新业务功能；信任边界（`ATTACHMENT_DATA_*` 等）调整；`utils/` 纯函数业务逻辑重写。
- **风险**：① 换 JSX 时碰坏 §5 不变式（缓解：逐条核对 + source guard + 新增护栏 + Codex 审）；② 原型功能缺口导致退化（缓解：§2 以生产为准 + 交互能力盘点）；③ ChatPanel 1187 行最大（缓解：§4.2 先子组件再分区域、不动 streaming/send）；④ 主题 FOUC / opacity modifier / 字体管线（缓解：§3 通道 token + index.html bootstrap + src/assets 字体）；⑤ Markdown/highlight 双主题工作量被低估（缓解：§4.5 单列）。
- **待决（不阻塞 spec，留 plan / 实施）**：执行方式（专用分支 vs 当前会话 subagent-driven）在 writing-plans 阶段定；图标模块最终粒度按复用度定；highlight 双主题用「两套 CSS 切」还是「CSS 变量主题」实施时定。

## 8. 交付物
- 翻新后的 `frontend/src/` 全套组件 + `index.css`（通道 token）+ `tailwind.config.js` + `utils/theme.js` + `components/icons.jsx` + 自托管字体 + `index.html` 主题 bootstrap。
- 非组件样式面（toast / highlight / Markdown / KaTeX / ErrorBoundary）全部变量化。
- 浅 / 深双主题、可切换、持久化、首屏不闪。
- 全套前端测试绿 + 新增护栏绿 + `npm run build` 过 + 浅深双主题 web 双平台逐屏视觉对照通过 + Codex 双轨审 APPROVED，且**功能零退化**。
