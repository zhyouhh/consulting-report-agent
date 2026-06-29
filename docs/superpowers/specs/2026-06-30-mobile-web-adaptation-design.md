# 移动端适配设计 Spec（2026-06-30）

> 修订记录：v1 初稿（brainstorm 定稿）→ v2 吸收 Codex 单轨独立审首轮（NEEDS-WORK，6 BLOCKER + 2 NIT），全部核实属实并落入下文（标 `[R1]`）→ v3 吸收复审（NEEDS-WORK，2 BLOCKER[审查汇报 ref 链未写进合同、全屏审查层 vs 滑走仍跑语义冲突] + 2 NIT[Sidebar 自动关接线、isCoarsePointer try/catch]），标 `[R2]`。→ v4 吸收对抗式红队（NEEDS-WORK，2 BLOCKER[fixed 层在 transform 抽屉祖先下失效、移动键盘/100dvh/safe-area 被 v3 弄丢] + 2 NIT[AdminPanel 移动挂载位置、onCreateProject 成功才 closeAll]），标 `[R3]`。

## 1. 目标与定位

给 `consulting-report-agent` 的 **Web 前端**补移动端适配，让同事在手机上也能用。核心场景由用户拍板：**聊天推进 + 查看为主**——手机上能跟 agent 对话把报告往前推、读草稿/审查结果、切项目、看额度、上传材料、触发审查、导出下载；**不在手机上做逐字编辑正文这类重操作**（看得到、改不了，要改回电脑或让 agent 改）。

**最高铁律：桌面端不变。** 只要用户手里是鼠标设备，CRA 永远渲染现有三栏布局，走的是**与今天完全相同的代码路径**（`isMobile=false` 分支），因此行为不变。**[R1] 表述从「像素/行为字节级不变」降级为「桌面代码路径不变 → 行为不变」**——因为 source-guard 只能证明字符串存在、不能证明像素级恒等；保证靠「桌面分支 JSX 原样保留 + 新增项全部 `isMobile`/默认值守卫 + 现有测试全绿 + 桌面 DOM/视觉 smoke」四件套（§8）。本次是 **纯前端、零后端改动**：不加端点、不动数据流、不碰 DeepSeek 官渠兼容 / 信任边界（`ATTACHMENT_DATA_*`）/ 多租户隔离 / 计费。风格沿用现有海军蓝双主题设计系统全套 token，**不引入任何新颜色 / emoji**。

### 验证重心
Web 模式（`run_web.py` + 浏览器）为主，生产部署在 kr-web-01（`consulting.z0y0h.work`，CF 后面）。桌面打包态（PyWebView）始终是鼠标设备 → 永远走桌面三栏分支。移动端验收以**真机**为准。

## 2. 不退化的两条铁律

1. **桌面代码路径不变**：`isMobile=false` 时，`App.jsx` 渲染的桌面三栏 JSX（现有 `flex h-screen` 三栏壳 `App.jsx:392`、中右拖动条 `App.jsx:429-435`、宽度记忆、`showSidebar`/`showWorkspacePanel` 折叠、主题/init effect）**原样保留、逐行不改**。所有新增项（`MobileShell` 兄弟分支、面板新 prop）都带 `isMobile`/默认值守卫，桌面分支取默认值即与今天恒等。
2. **后端零改动**：移动端只是把现有面板组件（`Sidebar`/`ChatPanel`/`WorkspacePanel`）换个摆法，调同一批 API、同一套状态。不新增/改任何后端文件。

## 3. 触发判定：按设备而非按窗口宽度（核心决策）

移动端壳的触发信号是「主输入是不是手指」，不是「窗口多宽」。这条是 brainstorm 中用户明确拍板的修正，杜绝「桌面缩窗 / 左右分屏 → 误切手机版」。

### 3.1 判定合同（[R1] 定稿，原 NIT 1）
新增纯函数封装 `frontend/src/utils/deviceMode.js`：
```
export function isCoarsePointer() {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false; // fallback = 桌面
  try {
    return window.matchMedia('(pointer: coarse)').matches;
  } catch {
    return false; // [R2] matchMedia 抛错（老浏览器/异常查询）→ fallback 桌面
  }
}
```
- **媒体查询定为 `(pointer: coarse)`**（主指针为粗指针 = 触摸设备），不留实施期即兴。`matchMedia` 不存在/异常 → fallback `false`（桌面），fail-safe 不误判成移动。
- **鼠标设备**（桌面/笔记本/桌面打包态）：`isMobile=false`，**永远三栏，与窗口宽度无关**。窗口缩到很窄 = 与今天完全一致（该挤就挤、该出滚动条就出，本次不动）。
- **触摸设备**（手机/平板）：`isMobile=true`，走抽屉壳。

### 3.2 [R1] 首屏锁定，不监听运行时切换（原 BLOCKER 1）
**`isMobile` 在 `App.jsx` 挂载时同步读一次（`useState(() => isCoarsePointer())`），本会话锁定，不挂 `matchMedia` 的 `change` 监听。**

原因：若运行时翻转 `isMobile`，桌面↔移动两分支渲染不同子树 → `ChatPanel` 卸载重挂，丢失其**组件本地 state**（会话显示、输入框文字、上传中、待发送附件、流式状态——全在 `ChatPanel.jsx:66+`，不在 App 共享态里），且卸载 cleanup 不显式 abort 在途 stream（`ChatPanel.jsx:210`）。指针类型本就几乎不会中途变，首屏锁定既消除整类灾难又零功能损失。极端外接设备的兜底见 §9（手动开关，YAGNI 不做）。

### 3.3 诚实边角（已与用户议定，非 bug）
- **触屏笔记本**（带触摸屏 Windows 本）：主输入仍触控板/鼠标（`pointer: fine`）→ 判桌面三栏，99% 正确。
- **平板外接鼠标/触控板**：主指针可能 `fine` → 判桌面三栏，可接受。
- 极少数判错的兜底（手动「桌面/移动视图」开关）：YAGNI 不做，留 follow-up。

## 4. 移动端布局：抽屉壳（方案 B）

复用现有三个面板组件，只换外壳。桌面是横向 flex 三栏；移动端换成「聊天占满 + 两侧抽屉覆盖」。

```
聊天主视图：              点 ☰ → 左抽屉(项目)：       点 ▣ → 右抽屉(工作区)：
┌──────────────┐        ┌────────┬─────┐            ┌─────┬────────┐
│ ☰ 美羊羊项目 ▣ │        │项目列表 │ 聊  │            │ 聊  │工作区   │
├──────────────┤        │·美羊羊 │ 天  │            │ 天  │阶段/文件│
│   聊天气泡    │        │·喜羊羊 │(scrim│            │(scrim│/材料   │
│              │        │────── │ 半透 │            │ 半透 │        │
│ [输入框] 📎➤ │        │账号/额度│ 明)  │            │ 明)  │        │
└──────────────┘        └────────┴─────┘            └─────┴────────┘
```

### 4.1 [R1] 顶栏：复用 `ChatPanel` 现有 60px 头，不新增（原 BLOCKER 2）
`ChatPanel` 已自带 60px 顶栏（`ChatPanel.jsx:876-916`）：左 ☰ 切侧栏按钮（`onToggleSidebar`，`IconSidebar`）+ 项目名 + 「连接模式 · 当前阶段」+ 清空按钮 + 右 ▣ 切工作区按钮（`onToggleWorkspacePanel`，`IconPanelRight`）。

**移动端不再加任何顶栏**——直接复用这个头，把 `onToggleSidebar`/`onToggleWorkspacePanel` 两个回调**接到 MobileShell 的左/右抽屉开关**（而非桌面的列折叠）。这两个按钮在桌面/移动下是同一个 UI、只是 handler 指向不同，天然零双顶栏。

### 4.2 左抽屉 = `Sidebar` 组件（≈1:1）
项目列表（点一下 = 切项目 + 自动关抽屉，回到聊天）、账号/今日额度/登出/切主题/新建项目、删除确认弹窗——全保留。竖向 264px 列塞进抽屉天然贴合，**`Sidebar` 内部零改动**。**[R2] 「自动关抽屉」不改 `Sidebar`**：由 MobileShell 包装 `Sidebar` 的回调（`onSelectProject`/`onLoggedOut`/`onOpenAdmin` 外面再包一层 `closeAll()`），`Sidebar.jsx:86` 现有 `onSelectProject(project)` 调用点不动。**[R3] `onCreateProject` 必须「成功才 closeAll」（原 NIT 2）**：`ProjectCreateModal.jsx:32` 依赖 `onCreate` 返回 success 才关弹窗，包装器若无条件先 `closeAll()` 会在新建失败时把用户踢出流程 → 写成 `async (p) => { const ok = await onCreateProject(p); if (ok) closeAll(); return ok }`，且**透传返回值**。`onSelectProject`（同步、无失败态）`closeAll()` 后置即可。

### 4.3 右抽屉 = `WorkspacePanel` 组件（三 tab，部分降级）
现有三 tab（`WorkspacePanel.jsx:332` `[['stage','阶段'],['files','文件'],['materials','材料']]`）：

| Tab | 手机上 | 降级？ |
|---|---|---|
| **阶段** | 进度 stepper + 「独立审查」「导出/下载草稿」按钮全保留 | 不降级。审查走全屏弹层、导出 docx 手机浏览器可下载 |
| **文件** | 看文件树 + 读正文草稿 | **只读**：完全禁止进入编辑态（不只是隐藏按钮，见 §5）；文件树/预览分隔条改固定比例 |
| **材料** | 看列表 + 解析状态 + 上传 + 删除 | 轻度：上传走手机原生「拍照/选文件」 |

### 4.4 聊天主区 = `ChatPanel` 组件（全保留 + 白捡能力）
- 对话、流式、工具调用 pill、停止/abort、待发送附件分流全保留。
- **回形针上传**在手机上点一下直接调起相机/相册/文件选择器（HTML `<input type=file>` 原生行为，免费得到）——现场拍文件喂 agent，移动端反而比桌面顺。

### 4.5 独立审查窗口：浮窗 → 全屏弹层
现有「独立审查」是 `WorkspacePanel` 内的可拖动浮窗（`IndependentReviewDrawer.jsx:260` `onMouseDown={handleDragStart}`）。移动端经 `isMobile` prop 改为 **`position: fixed` 全屏弹层、且经 `createPortal(document.body)` 渲染**（[R3]：portal 到 body 是为了脱离右抽屉子树——否则抽屉若有 `transform` 祖先会改写 fixed 的 containing block，见 §4.7）：流式审查对话内容不变，从浮窗变满屏、去拖动。

**[R2] 控件语义拆清（原 BLOCKER 2）**：现有关闭按钮走 `handleActiveClose` = abort fetch + `/discard`（`IndependentReviewDrawer.jsx:186`）。移动端把它**明确标为「停止审查」**（语义即中止，符合用户预期），审查完成时自动关层、回到主聊天看汇报轮。

**[R2] v1 不做「运行中最小化/收起回聊天」**：审查运行中就是全屏看它流式——审查本身就是要看的内容，而主聊天的汇报轮在审查**完成后**才 fire（运行中聊天里没有新东西可看），所以「滑走看聊天」对审查无实际价值。最小化留 follow-up。**这样彻底消除「全屏盖住 vs 滑走仍跑」的语义冲突**（原 §4.6/§8 那条矛盾验收项已删）。

### 4.6 [R1] 抽屉交互 + 挂载策略（原 BLOCKER 4）
- **唤出**：点 ChatPanel 头的 ☰ / ▣。
- **关闭**：点 scrim 遮罩、抽屉关闭区、或切项目/触发动作后自动关。
- **互斥**：左右抽屉同时只开一个。
- **遮罩**：抽屉开时聊天区盖 `bg-scrim/N`（设计系统唯一允许的 `dark:` 例外）。
- **[R1] 挂载策略 = 常驻挂载、CSS 隐藏（`transform`/`visibility`/off-canvas），关闭抽屉绝不卸载 `WorkspacePanel`/`Sidebar`**。理由（[R2] 收敛到上传存活，审查改由 §4.5 fixed 层独立保证）：**材料上传 busy**（`WorkspacePanel.jsx:29`）在 `WorkspacePanel` 内、上传异步且用户会想同时干别的，卸载即中断；tab 选择 / 滚动位置也应保留。常驻挂载保证「**材料上传中关右抽屉 → 上传不中断**」。审查 stream 的存活**不依赖**抽屉挂载——它是 §4.5 的 `position: fixed` 全屏层，脱离抽屉容器。**这是移动壳特有行为，刻意区别于桌面 `{showWorkspacePanel && <WorkspacePanel/>}` 的卸载式折叠，不影响桌面。**

### 4.7 [R3] 移动端壳工程必做项（原红队 BLOCKER 1 + 2）

这些是移动端会直接踩坑的硬约束，实施期必做、验收必查：

**A. fixed 层 vs transform 祖先（CSS containing block 坑）**
- 任何 `position: fixed` 浮层（移动审查全屏层 §4.5、Sidebar 子树内的 `ProjectCreateModal`/`SettingsModal`/删除确认 `Sidebar.jsx:234/260`、App 级 `AdminPanel`）**一旦祖先带 `transform`/`filter`/`perspective`，其 containing block 会从 viewport 变成那个祖先**，导致「全屏」错位、跟随抽屉偏移。
- **规则（二者都要）**：① 移动审查层用 `createPortal(document.body)` 脱离抽屉子树（§4.5）；② **移动抽屉的开合动画禁用 `transform`/`filter`/`perspective`**，改用 `left`/`right`/`inset` 过渡或 `visibility`/`display` 切换——这样 Sidebar 内既有的 fixed 弹窗**无需改造**（不必给每个 modal 加 portal）即不破版。隐藏态仍保持组件挂载（§4.6 上传存活），用 `visibility:hidden`/off-canvas `left`、**不卸载**。

**B. 移动视口高度 / 软键盘 / 安全区（核心场景「手机聊天推进」的命门）**
- `MobileShell` 根高度用 **`100dvh`**（dynamic viewport height），**不用** `h-screen`/`100vh`——否则手机地址栏收放 + 软键盘弹出时高度算错。桌面分支仍 `h-screen`，不动（§2）。
- 聊天 composer（`ChatPanel.jsx:1093` 底部输入区）底部 padding 加 **`env(safe-area-inset-bottom)`**，避免被 iPhone 底部横条压住。这是移动专属样式，经 `isMobile` 或媒体查询作用，不改桌面渲染。
- 聊天气泡流 / 文件预览 / 材料列表 / 抽屉内容滚动容器用 **`min-h-0 overflow-y-auto`**，保证软键盘弹出、内容超长时各自可独立纵向滚动；抽屉打开时背景聊天区锁滚动防穿透。
- `index.css:39` 现无 safe-area 处理；移动样式新增，不动现有桌面规则。

## 5. 组件与改动边界（[R1] prop 合同补全，原 BLOCKER 5）

**新增：**
- `frontend/src/components/MobileShell.jsx`：移动壳。渲染 `ChatPanel`（主区，传抽屉开关 handler）+ 常驻挂载的左抽屉（`Sidebar`）/右抽屉（`WorkspacePanel`）+ scrim。持 `openLeft`/`openRight` 互斥 state。
- `frontend/src/utils/deviceMode.js`：`isCoarsePointer()`（§3.1）+ 抽屉互斥状态机纯函数（`openLeft`/`openRight`/`closeAll`，无 jsdom 可单测）。

**[R1] prop 传递链（写死合同）：**
```
App (isMobile=true)
  └─ MobileShell  // 持有 chatPanelRef + openLeft/openRight 互斥 state
       ├─ ChatPanel(ref=chatPanelRef, onToggleSidebar=openLeft, onToggleWorkspacePanel=openRight, ...现有 props 原样)
       ├─ Sidebar(onSelectProject/onCreateProject/onLoggedOut/onOpenAdmin = wrap(closeAll), ...其余现有 props 原样)  // Sidebar 本体零改，回调由 MobileShell 包装
       └─ WorkspacePanel(isMobile=true, width='100%',
            onTriggerSystemTurn=(t,m)=>chatPanelRef.current?.triggerSystemTurn(t,m),
            onDropPendingReviewTriggers=(t)=>chatPanelRef.current?.dropPendingReviewTriggers(t),
            ...其余现有 props 原样)
            ├─ FilePreviewPanel(isMobile=true)
            └─ IndependentReviewDrawer(isMobile=true)
```

**[R2] 审查汇报 ref 链（原 BLOCKER 1，不可漏）**：桌面靠 imperative ref——`ChatPanel` 经 ref 暴露 `triggerSystemTurn`/`dropPendingReviewTriggers`（`ChatPanel.jsx:752`），`WorkspacePanel` 审查完成调 `onTriggerSystemTurn`（`WorkspacePanel.jsx:191`），`App.jsx:448` 用 `chatPanelRef.current` 接上。MobileShell 必须持**同一个 `chatPanelRef`** 并把 `WorkspacePanel` 这两个回调接到**移动端这个 `ChatPanel` 实例**，否则审查完成后主聊天汇报轮不触发（审查白跑）。source-guard 锁这条链（§8.4）。

**最小改动（桌面取默认值 = 今天行为）：**
- `App.jsx`：根渲染加分支 `isMobile ? <MobileShell .../> : (现有三栏 JSX 原样保留)`。现有桌面 JSX 不重写不挪动。**[R3] 顶层挂载位置（原 NIT 1）**：`ErrorBoundary`（`App.jsx:390`）+ `Toaster`（`:391`）继续包在分支**外层**两分支共用；`{showAdmin && authUser?.is_admin && <AdminPanel/>}`（现位于桌面 JSX 内 `:454`）**上提为 `isMobile` 分支的兄弟**（在 ErrorBoundary 内、两壳之外渲染），否则移动端点「管理」只 set state 看不到面板。`AdminPanel` 自身的窄屏适配见 §7。
- `WorkspacePanel.jsx`：新增可选 `isMobile`（默认 false）。为 true：根宽度用 `'100%'` 覆盖 `width ?? DEFAULT_WORKSPACE_WIDTH`（`WorkspacePanel.jsx:324`）；向下传 `isMobile` 给 `FilePreviewPanel`/`IndependentReviewDrawer`。
- `FilePreviewPanel.jsx`：新增可选 `isMobile`（默认 false）。为 true：① **完全禁止进入编辑态**（不渲染/不响应「编辑」按钮 `FilePreviewPanel.jsx:319`，使 dirty guard / beforeunload `:82/:143` 在移动端天然不触发，而非只藏按钮）；② 文件树/预览分隔条改固定比例（不渲染 `cursor-row-resize` 拖动条）。
- `IndependentReviewDrawer.jsx`：新增可选 `isMobile`（默认 false）。为 true 渲染全屏弹层、去拖动；false 维持现有浮窗。
- **默认 false 全部 = 今天行为**，桌面分支不传或显式 false。

**不动：** `Sidebar.jsx`、`ChatPanel.jsx`（仅 handler 指向变，无 prop 增改）、现有 `utils/`、后端、token。

## 6. 状态管理

- `isMobile`：`App.jsx` `useState(() => isCoarsePointer())`，首屏锁定（§3.2），无运行时监听。
- 移动左右抽屉开关：`MobileShell` 自己的 `openLeft`/`openRight` 互斥 state，**独立于桌面 `showSidebar`/`showWorkspacePanel`**（避免与桌面折叠逻辑纠缠）。`ChatPanel` 头的两个 toggle 在移动下接这套。
- 长任务存活：右抽屉常驻挂载（§4.6），`WorkspacePanel` 内的审查/上传 state 不因关抽屉而丢。
- 其余状态（项目、会话、workspace summary、额度、主题）两分支共用同一份，无分叉。

## 7. [R1] Auth / Modal 移动端 pass（原 BLOCKER 3，必做）

主壳之外的**全屏门 + 模态框**当前是固定宽，窄屏会溢出，否则同事卡在登录/弹窗。这些是 `App.jsx` 早返回分支或顶层 modal，**不在三栏壳内**，须单独适配：

| 屏/弹窗 | 现状 | 移动端处理 |
|---|---|---|
| 登录 `Login.jsx:32` | `w-[344px]` | 必修（登录是硬门）：改 `w-[min(344px,calc(100vw-32px))]`，确认 ≤360px 屏不溢出 |
| 强制改密 `ForcePasswordChange.jsx:17` | `w-[360px]` | 同上，`w-[min(360px,calc(100vw-32px))]` |
| 新建报告 `ProjectCreateModal.jsx:44` | `w-[560px]` 双列 | 窄屏 `w-[calc(100vw-32px)] max-h-[calc(100dvh-32px)] overflow-y-auto` + 单列堆叠 |
| 设置 `SettingsModal.jsx:100` | `w-[560px]` 双列 | 同上，单列 |
| 管理员面板 `AdminPanel.jsx:52` | `w-[680px]` + 5 列 grid | 同上；用户表横向滚动或窄屏卡片化（admin 罕用于手机，至少不破版） |

**实现手段全用响应式 utility（断点或 `min()`/`calc`）**，不引入新 token，遵守 paletteGuard。这些是相对独立的小活，可单独成实施批次。

## 8. 测试与守护（[R1] 锁死「桌面代码路径不变」，原 BLOCKER 6）

无 jsdom（项目惯例）→ 纯函数单测 + source-guard 接线测 + 桌面 smoke + 真机验收。

1. **现有 460 前端测试全绿**（[R1] NIT 2：`cd frontend && node --test tests/` 实测 `tests 460`，spec 属实）——测桌面结构与行为，桌面被动即红。
2. **新增 source-guard**：断言 `App.jsx` 桌面分支结构原样（三栏 flex 壳 + 中右拖动条 `onMouseDown`/`cursor-col-resize` 仍在 `!isMobile` 分支）；`MobileShell` 只在 `isMobile` 分支渲染；面板新 prop 默认 false。
3. **`deviceMode.js` 纯函数单测**：`isCoarsePointer` 的 matchMedia-absent fallback、抽屉互斥状态机。
4. **`mobileShell.source` 接线测**：MobileShell 装配 `ChatPanel`(带 `chatPanelRef`)/`Sidebar`/`WorkspacePanel` + scrim + 两 toggle 接抽屉；**[R2] 审查汇报 ref 链**（`WorkspacePanel.onTriggerSystemTurn` 接 `chatPanelRef.current?.triggerSystemTurn`）；**[R2] `Sidebar` 回调被 `closeAll` 包装**（`onCreateProject` 成功才关）；右抽屉常驻挂载（关闭不卸载）；**[R3] 抽屉容器类名不含 `transform`/`filter`/`perspective` 工具类**（`translate-x`/`scale`/`rotate`/`blur` 等，守 §4.7-A）、移动审查层经 `createPortal`；**[R3] MobileShell 根高度用 `100dvh` 非 `h-screen`**、composer 含 `safe-area-inset-bottom`；prop 链 §5 正确。
5. **[R1] 桌面行为 smoke**：补一条桌面侧验证证明「字节级」之外的行为不变——`isMobile=false` 下渲染路径不变 + 关键交互（拖动分栏、切 tab、dirty guard）source-guard 仍指向 `!isMobile` 分支；条件允许时加 Playwright 桌面截图/DOM 快照（择一，记录所选手段）。
6. **paletteGuard / darkClassGuard 继续绿**：移动端无新 hex/emoji、除既有 `dark:bg-scrim/N` 外无新 `dark:`。
7. **build 绿。**
8. **Codex 单轨独立审到 APPROVED**（spec），实施期每批 commit 后 spec+quality 双轨审。
9. **真机验收**：部署 kr-web-01 后真实手机走：登录 → 切项目 → 对话推进 →（[R3]）**软键盘弹出后输入框仍可见可发送** → 开两抽屉 → 读草稿 → 上传/拍照材料 →（[R2]）材料上传中关右抽屉确认不中断 → 触发审查（[R3] 全屏层**真·满屏不偏移** → 完成自动回主聊天汇报；「停止审查」按钮可中止）→ 导出下载 docx；（[R3]）**新建项目/设置/管理弹窗 fixed 不破版、不溢出**。

## 9. 非目标（YAGNI）

- 手机逐字编辑正文 / 重度文件管理：不做（降级只读/查看）。
- 手动「桌面/移动视图」切换开关：不做，留 follow-up（§3.3 边角兜底）。
- 平板横屏特殊布局：触摸设备一律抽屉壳。
- 离线 / PWA / 安装到桌面：不做。
- 后端任何改动：零改动。

## 10. 部署

纯前端，走现有 frontend-only 流程：本地 `npm run build` → tar → `VPS-fix-private/.push-file.py kr-web-01` 推 → 服务器解到 `dist.new` + `chmod -R a+rX` + 原子 `mv dist dist.old && mv dist.new dist`，无须重启 systemd（`_SPAStaticFiles` 按请求读盘、SPA shell no-cache）。`dist.old` 留回滚。
