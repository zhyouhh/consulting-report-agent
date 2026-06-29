# 移动端适配设计 Spec（2026-06-30）

> 状态：v1 初稿（brainstorm 定稿，待 Codex 双轨审）。

## 1. 目标与定位

给 `consulting-report-agent` 的 **Web 前端**补移动端适配，让同事在手机上也能用。核心场景由用户拍板：**聊天推进 + 查看为主**——手机上能跟 agent 对话把报告往前推、读草稿/审查结果、切项目、看额度、上传材料、触发审查、导出下载；**不在手机上做逐字编辑正文这类重操作**（看得到、改不了，要改回电脑或让 agent 改）。

**最高铁律：桌面端体验零变化。** 只要用户手里是鼠标设备，CRA 永远是现在熟悉的三栏布局，渲染出来的每个像素和每个行为字节级不变，并由测试锁死。本次是一次 **纯前端、零后端改动** 的改造：不加端点、不动数据流、不碰 DeepSeek 官渠兼容 / 信任边界（`ATTACHMENT_DATA_*` 等）/ 多租户隔离 / 计费。风格沿用现有海军蓝双主题设计系统的全套 token，**不引入任何新颜色 / emoji**。

### 验证重心
Web 模式（`run_web.py` + 浏览器）为主，生产部署在 kr-web-01（`consulting.z0y0h.work`，CF 后面，国内同事使用）。桌面打包态（PyWebView）始终是鼠标设备 → 永远走桌面三栏分支，天然不受影响。移动端验收以**真机**为准：部署后在真实手机浏览器点一遍。

## 2. 不退化的两条铁律

1. **桌面零变化**：鼠标设备永远渲染现有三栏布局（`App.jsx` 现有 `flex h-screen` 三栏壳 + 中右拖动条 + 宽度记忆全保留）。桌面分支的 JSX 原样保留、一行不改，移动端壳只在触摸设备渲染。
2. **后端零改动**：移动端只是把现有的三个面板组件（`Sidebar` / `ChatPanel` / `WorkspacePanel`）换个摆法，调的还是同一批 API、同一套状态。不新增/修改任何后端文件。

## 3. 触发判定：按设备而非按窗口宽度（核心决策）

**移动端壳的触发信号是「主输入是不是手指」，不是「窗口多宽」。** 这条是 brainstorm 中用户明确拍板的修正，目的是杜绝「桌面缩窗 / 左右分屏 → 误切手机版」。

- **判定**：`window.matchMedia('(pointer: coarse)')`（主指针为粗指针 = 触摸设备）。为稳健可与 `(hover: none)` 取交集，最终媒体查询在实施时定（建议 `(pointer: coarse)`，必要时 `(pointer: coarse) and (hover: none)`）。
- **鼠标设备（桌面 / 笔记本 / 桌面打包态）**：`isMobile = false`，**永远三栏，与窗口宽度无关**。窗口缩到很窄会怎样？**与今天完全一致**——该横向挤就挤、该出滚动条就出（这是现有行为，本次一个像素都不动）。
- **触摸设备（手机 / 平板）**：`isMobile = true`，走抽屉壳。
- **响应式**：在 `App.jsx` 挂载时读一次写入 `isMobile` state，并挂 `matchMedia` 的 `change` 监听以应对极端切换（如平板外接/拔除指点设备）；卸载清理监听。指针类型很少中途变，监听是兜底、成本极低。

### 诚实边角（已与用户议定，非 bug）
- **触屏笔记本**（带触摸屏的 Windows 本）：主输入仍是触控板/鼠标（`pointer: fine`）→ 判成桌面三栏，99% 情况正确。
- **平板外接鼠标/触控板**：主指针可能变 `fine` → 判成桌面三栏，可接受。
- **极少数设备判错的兜底**：手动「切换桌面/移动视图」开关。**本次 YAGNI 不做**，真有用户反馈再加（记为 follow-up）。

## 4. 移动端布局：抽屉壳（方案 B）

复用现有三个面板组件，只换它们外面的「壳」。桌面是横向 flex 三栏壳；移动端换成「聊天占满 + 两侧抽屉覆盖」。

```
聊天主视图：              点左上 ☰ → 左抽屉(项目)：     点右上 ▣ → 右抽屉(工作区)：
┌──────────────┐        ┌────────┬─────┐            ┌─────┬────────┐
│ ☰ 美羊羊项目 ▣ │        │项目列表 │ 聊  │            │ 聊  │工作区   │
├──────────────┤        │·美羊羊 │ 天  │            │ 天  │阶段/文件│
│   聊天气泡    │        │·喜羊羊 │(scrim│            │(scrim│/材料   │
│              │        │────── │ 半透 │            │ 半透 │        │
│ [输入框] 📎➤ │        │账号/额度│ 明)  │            │ 明)  │        │
└──────────────┘        └────────┴─────┘            └─────┴────────┘
```

### 4.1 顶栏（移动端新增，桌面无此物）
- 左：☰ 按钮 → 开/关左抽屉（对应桌面左侧栏）。
- 中：当前项目名。
- 右：▣ 按钮（复用桌面 `IconPanelRight` 镜像图标）→ 开/关右抽屉（对应桌面工作区）。
- 图标全用现有 `components/icons.jsx` 的线性 SVG，无 emoji。

### 4.2 左抽屉 = `Sidebar` 组件（≈1:1）
- 项目列表（点一下 = 切项目 + 自动关抽屉，回到聊天）、账号 / 今日额度 / 登出 / 切主题 / 新建项目、删除确认弹窗——全保留。
- 这块本来就是竖向 264px 列，塞进抽屉天然贴合，**Sidebar 内部零改动**。

### 4.3 右抽屉 = `WorkspacePanel` 组件（三 tab，部分降级）
WorkspacePanel 现有三 tab（`WorkspacePanel.jsx:332` `[['stage','阶段'],['files','文件'],['materials','材料']]`）：

| Tab | 手机上 | 降级？ |
|---|---|---|
| **阶段** | 进度 stepper + 「独立审查」「导出/下载草稿」按钮全保留 | 不降级。审查走流式小窗、导出 docx 手机浏览器可下载 |
| **文件** | 看文件树 + 读正文草稿 | **只读**：隐藏「编辑」按钮；文件树/预览的拖动分隔条改固定比例（拖动是鼠标专属，手机用不了） |
| **材料** | 看列表 + 解析状态 + 上传 + 删除 | 轻度：上传走手机原生「拍照/选文件」，不做重管理 |

### 4.4 聊天主区 = `ChatPanel` 组件（全保留 + 白捡能力）
- 对话、流式、工具调用 pill、停止/abort、待发送附件分流等全保留。
- **回形针上传**在手机上点一下直接调起相机/相册/文件选择器（HTML `<input type=file>` 原生行为，免费得到）——同事可现场拍文件喂 agent，是移动端反而比桌面顺的点。

### 4.5 独立审查窗口：浮窗 → 全屏弹层
现有「独立审查」是可拖动浮窗（`IndependentReviewDrawer.jsx:260` `onMouseDown={handleDragStart}`），手机拖不动也放不下。移动端改为**全屏弹层**：流式审查对话内容不变，只是从浮窗变满屏（顶部留关闭按钮）。判定同 §3 的 `isMobile`。

### 4.6 抽屉交互细节
- **唤出**：点顶栏 ☰ / ▣。
- **关闭**：点 scrim 遮罩（复用现有 `scrim` token）、点抽屉内的关闭区、或切项目/触发动作后自动关。
- **互斥**：左右抽屉同时只开一个（开一个自动关另一个），避免叠层。
- **遮罩**：抽屉打开时聊天区盖一层 `bg-scrim/N` 半透明遮罩（这是设计系统里唯一允许的 `dark:` 例外用法，见 redesign 段约定）。

## 5. 组件与改动边界

**新增组件：**
- `frontend/src/components/MobileShell.jsx`：移动端壳。含顶栏（☰ / 项目名 / ▣）+ 聊天主区（渲染 `ChatPanel`）+ 左抽屉（渲染 `Sidebar`）+ 右抽屉（渲染 `WorkspacePanel`）+ scrim 遮罩。持左右抽屉开关 state。
- `frontend/src/utils/deviceMode.js`：纯函数 + matchMedia 封装。`isCoarsePointer()` / 抽屉互斥状态机（`openLeft`/`openRight`/`closeAll`，纯函数，无 jsdom 可单测）。

**最小改动（桌面零影响，仅加分支/加 prop）：**
- `App.jsx`：根渲染加分支——`isMobile ? <MobileShell .../> : (现有三栏 JSX 原样保留)`。**现有桌面 JSX 不重写、不挪动**，只在外层加 `!isMobile` 守卫 + 一个 `isMobile && <MobileShell>` 兄弟分支。所有现有 props/state/effect 继续喂给两个分支共用的面板组件。
- `WorkspacePanel.jsx` / `FilePreviewPanel.jsx`：新增可选 prop `isMobile`（默认 false）。为 true 时：① 隐藏「编辑」按钮（`FilePreviewPanel`），② 文件树/预览分隔条改固定比例（不渲染 `cursor-row-resize` 拖动条），③ 接受移动端宽度（抽屉里给 ~85vw 或全宽，替代桌面 `width` prop）。**默认 false 时行为与今天完全一致**（桌面分支不传或传 false）。
- 独立审查窗口组件：新增 `isMobile` prop，为 true 渲染全屏弹层、为 false 维持现有可拖动浮窗。

**不动的：** `Sidebar.jsx`（抽屉里原样复用）、`ChatPanel.jsx`（主区原样复用）、所有 `utils/` 现有纯函数、所有后端、所有 token 定义。

## 6. 状态管理

- `isMobile`：`App.jsx` state，源自 `deviceMode.isCoarsePointer()` + matchMedia 监听（§3）。
- 移动端左右抽屉开关：**独立于桌面的 `showSidebar`/`showWorkspace`**（那两个是桌面列折叠状态）。移动端用 `MobileShell` 自己的 `openLeft`/`openRight` state，避免与桌面折叠逻辑纠缠。桌面那两个状态在移动分支下不参与渲染。
- 其余状态（项目、会话、workspace summary、额度、主题）**两个分支共用同一份**，无分叉。

## 7. 移动端工程注意点（实施期必处理）

- **虚拟键盘 + 视口高度**：移动端壳的全屏高度用 `100dvh`（dynamic viewport height）而非 `100vh`/`h-screen`，否则手机软键盘弹出时输入框会被键盘盖住。这是移动端真坑，必须处理。
- **触摸滚动**：聊天气泡流、文件预览、材料列表在抽屉/主区内需可独立纵向滚动（`overflow-y-auto` + 触摸惯性），抽屉打开时背景聊天区锁滚动（防穿透）。
- **viewport meta**：`index.html` 已有 `width=device-width, initial-scale=1.0`（无需改）。
- **安全区**：底部输入框考虑 `env(safe-area-inset-bottom)`（iPhone 底部横条），避免被遮。实施期视觉验收时确认。

## 8. 测试与守护（锁死桌面零变化）

无 jsdom（项目惯例）→ 纯函数单测 + source-guard 组件接线测 + 真机验收。

1. **现有 460 前端测试全绿**——它们在测桌面结构与行为，桌面一旦被动立刻红。
2. **新增 source-guard**：断言 `App.jsx` 桌面分支结构原样还在（三栏 flex 壳 + 中右拖动条 `onMouseDown`/`cursor-col-resize` 仍在 `!isMobile` 分支内），移动端壳只在 `isMobile` 分支渲染。
3. **`deviceMode.js` 纯函数单测**：抽屉互斥状态机、`isCoarsePointer` 派生逻辑。
4. **`mobileShell.source` 接线测**：MobileShell 正确装配三个面板 + 顶栏按钮 + scrim；WorkspacePanel/FilePreviewPanel/审查窗口 `isMobile` prop 默认 false 守卫（默认即桌面行为）。
5. **paletteGuard 继续绿**：移动端不准引入新 hex / `bg-[#..]` / emoji，全用现成 token。
6. **`darkClassGuard` 继续绿**：除既有 `dark:bg-scrim/N` 例外外无新 `dark:` 前缀。
7. **build 绿**。
8. **Codex 双轨审**（spec + quality，每批 commit 后到 APPROVED）。
9. **真机验收**：部署 kr-web-01 后，用户在真实手机浏览器走一遍——切项目、对话推进、开两个抽屉、读草稿、上传/拍照材料、触发审查、导出下载 docx。

## 9. 非目标（YAGNI）

- 手机上**逐字编辑正文 / 重度文件管理**：明确不做（用户拍板降级为只读/查看）。
- **手动「桌面/移动视图」切换开关**：YAGNI，本次不做，留 follow-up。
- **平板横屏特殊布局**：触摸设备一律走抽屉壳（不为平板横屏单独做三栏 + 触摸拖动适配）。
- **离线 / PWA / 安装到桌面**：本次不做。
- **后端任何改动**：零改动。

## 10. 部署

纯前端，走现有 frontend-only 流程：本地 `npm run build` → tar → `VPS-fix-private/.push-file.py kr-web-01` 推 → 服务器解到 `dist.new` + `chmod -R a+rX` + 原子 `mv dist dist.old && mv dist.new dist`，**无须重启 systemd**（`_SPAStaticFiles` 按请求读盘、SPA shell no-cache）。`dist.old` 留回滚。
