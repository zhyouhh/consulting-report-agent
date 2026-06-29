# 移动端适配 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 CRA Web 前端加移动端抽屉壳（聊天占满 + 左/右抽屉），触摸设备启用、鼠标设备永远走现有桌面三栏，桌面行为零变化。

**Architecture:** `App.jsx` 首屏按 `isCoarsePointer()` 锁定 `isMobile`，`isMobile ? <MobileShell/> : (现有三栏 JSX 原样)`。`MobileShell` 复用 `ChatPanel`/`Sidebar`/`WorkspacePanel` 三个面板组件，只换外壳：聊天主区复用 ChatPanel 自带顶栏（☰/▣ 接抽屉），左右抽屉 off-canvas `left/right` 滑动（**禁 transform**，避免破坏内部 fixed 弹窗）、常驻挂载（关闭不卸载，保上传/审查存活）。叶子组件加 `isMobile`（默认 false）只读/全屏分支。auth 屏 + 模态框补窄屏宽度。纯前端、零后端改动。

**Tech Stack:** React 18 + Tailwind（CSS 变量 token 单一真值源）+ Vite；测试 Node 原生 `node:test`（无 jsdom）——纯函数直测 + source-guard（读 `.jsx` 源断言字符串）。

**真值源 Spec：** `docs/superpowers/specs/2026-06-30-mobile-web-adaptation-design.md`（Codex 单轨审 APPROVED，v6）。

> 修订：v1 初稿 → v2 吸收 Codex plan 审 R1（NEEDS-WORK，7 BLOCKER[右抽屉布局无高/宽基准、禁-transform guard 被注释自炸、MobileShell 注释 ☰/▣ 触 paletteGuard、Task4 未改既有审查测试、Task2 下传断言假阳性、漏 Sidebar 删除确认弹窗、onInsertPrompt 未关抽屉] + 4 NIT），全部落入下文。

**通用约束（每个 task 都成立）：**
- 不引入新颜色 / 裸 hex / emoji（`paletteGuard` 守）；除既有 `dark:bg-scrim/N` 外不加 `dark:`（`darkClassGuard` 守）。
- 所有 `isMobile` prop **默认 false**，桌面取默认 = 今天行为。
- 后端、DeepSeek、信任边界、租户隔离零改动。
- 测试命令在 `frontend/` 下跑：单文件 `node --test tests/<file>.mjs`，全量 `node --test tests/`，构建 `npm run build`。

---

## 文件结构

**新建：**
- `frontend/src/utils/deviceMode.js` — `isCoarsePointer()` + 抽屉互斥状态机 `nextDrawerState()`（纯函数）。
- `frontend/src/components/MobileShell.jsx` — 移动端壳（抽屉布局 + 回调包装 + chatPanelRef）。
- `frontend/tests/deviceMode.test.mjs` — deviceMode 纯函数单测。
- `frontend/tests/mobileShell.source.test.mjs` — MobileShell 接线 source-guard。
- `frontend/tests/mobileAuthModals.source.test.mjs` — auth 屏 + 模态框窄屏 source-guard。

**修改（仅加 `isMobile`/分支，桌面取默认）：**
- `frontend/src/components/WorkspacePanel.jsx` — 加 `isMobile`（width 100% + 下传）。
- `frontend/src/components/FilePreviewPanel.jsx` — 加 `isMobile`（禁编辑态 + 去拖动条）。
- `frontend/src/components/IndependentReviewDrawer.jsx` — 加 `isMobile`（createPortal 全屏 + 「停止审查」+ 去拖动）。
- `frontend/src/App.jsx` — `isMobile` 首屏锁定 + 分支 + 上提 AdminPanel。
- `frontend/src/components/Login.jsx`、`ForcePasswordChange.jsx`、`ProjectCreateModal.jsx`、`SettingsModal.jsx`、`AdminPanel.jsx` — 窄屏宽度。

---

## Task 1: deviceMode 纯函数（设备判定 + 抽屉互斥）

**Files:**
- Create: `frontend/src/utils/deviceMode.js`
- Test: `frontend/tests/deviceMode.test.mjs`

- [ ] **Step 1: 写失败测试**

```js
// frontend/tests/deviceMode.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import {
  isCoarsePointer,
  nextDrawerState,
  DRAWER_NONE, DRAWER_LEFT, DRAWER_RIGHT,
} from "../src/utils/deviceMode.js";

test("isCoarsePointer: matchMedia 缺失 → false（fallback 桌面）", () => {
  const prev = globalThis.window;
  globalThis.window = {}; // 无 matchMedia
  assert.equal(isCoarsePointer(), false);
  globalThis.window = prev;
});

test("isCoarsePointer: matchMedia 命中 coarse → true", () => {
  const prev = globalThis.window;
  globalThis.window = { matchMedia: (q) => ({ matches: q === "(pointer: coarse)" }) };
  assert.equal(isCoarsePointer(), true);
  globalThis.window = prev;
});

test("isCoarsePointer: matchMedia 抛错 → false（fail-safe 桌面）", () => {
  const prev = globalThis.window;
  globalThis.window = { matchMedia: () => { throw new Error("boom"); } };
  assert.equal(isCoarsePointer(), false);
  globalThis.window = prev;
});

test("nextDrawerState: 互斥——开右关左、开左关右", () => {
  assert.equal(nextDrawerState(DRAWER_LEFT, "openRight"), DRAWER_RIGHT);
  assert.equal(nextDrawerState(DRAWER_RIGHT, "openLeft"), DRAWER_LEFT);
});

test("nextDrawerState: toggle 同侧→关、close→none", () => {
  assert.equal(nextDrawerState(DRAWER_LEFT, "toggleLeft"), DRAWER_NONE);
  assert.equal(nextDrawerState(DRAWER_NONE, "toggleLeft"), DRAWER_LEFT);
  assert.equal(nextDrawerState(DRAWER_RIGHT, "toggleRight"), DRAWER_NONE);
  assert.equal(nextDrawerState(DRAWER_LEFT, "close"), DRAWER_NONE);
});

test("nextDrawerState: 未知 action 原样返回", () => {
  assert.equal(nextDrawerState(DRAWER_RIGHT, "wat"), DRAWER_RIGHT);
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `node --test tests/deviceMode.test.mjs`
Expected: FAIL（`Cannot find module .../deviceMode.js`）

- [ ] **Step 3: 实现**

```js
// frontend/src/utils/deviceMode.js
// 设备模式判定 + 抽屉互斥状态机（纯函数，无副作用，可在 node:test 直接测）。
// 移动壳是否启用按「主输入是不是手指」判定，与窗口宽度脱钩（spec §3）。

export function isCoarsePointer() {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false; // fallback = 桌面
  try {
    return window.matchMedia("(pointer: coarse)").matches;
  } catch {
    return false; // matchMedia 抛错（老浏览器/异常查询）→ fallback 桌面
  }
}

// 抽屉互斥：状态是单一枚举，天然保证「同时只开一个」。
export const DRAWER_NONE = "none";
export const DRAWER_LEFT = "left";
export const DRAWER_RIGHT = "right";

export function nextDrawerState(current, action) {
  switch (action) {
    case "toggleLeft": return current === DRAWER_LEFT ? DRAWER_NONE : DRAWER_LEFT;
    case "toggleRight": return current === DRAWER_RIGHT ? DRAWER_NONE : DRAWER_RIGHT;
    case "openLeft": return DRAWER_LEFT;
    case "openRight": return DRAWER_RIGHT;
    case "close": return DRAWER_NONE;
    default: return current;
  }
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `node --test tests/deviceMode.test.mjs`
Expected: PASS（6 测试全过）

- [ ] **Step 5: 提交**

```bash
git add frontend/src/utils/deviceMode.js frontend/tests/deviceMode.test.mjs
git commit -m "feat(mobile): deviceMode — coarse-pointer detection + drawer mutex state machine"
```

---

## Task 2: WorkspacePanel 接 `isMobile`（width 100% + 下传）

**Files:**
- Modify: `frontend/src/components/WorkspacePanel.jsx`（签名加 `isMobile`，下传给 FilePreviewPanel / IndependentReviewDrawer）
- Test: `frontend/tests/workspacePanel.source.test.mjs`（已存在，追加用例）

- [ ] **Step 1: 追加失败测试**

```js
// 追加到 frontend/tests/workspacePanel.source.test.mjs 末尾
test("WorkspacePanel accepts isMobile (default false) and threads it down", () => {
  const s = wsSrc();
  // 签名含 isMobile 默认 false
  assert.match(s, /isMobile\s*=\s*false/);
  // [R1] 两条独立断言——防自闭合 <FilePreviewPanel/> 的 isMobile 假满足审查窗那条
  assert.match(s, /<FilePreviewPanel[\s\S]*?isMobile=\{isMobile\}/);
  assert.match(s, /<IndependentReviewDrawer[\s\S]*?isMobile=\{isMobile\}/);
});
```

> [R1] WorkspacePanel 渲染审查窗的 JSX tag 名是 `IndependentReviewDrawer`（import 别名，见 `WorkspacePanel.jsx:455`）；若实际别名不同，断言与实现同步对齐真实 tag 名。

- [ ] **Step 2: 跑测试确认失败**

Run: `node --test tests/workspacePanel.source.test.mjs`
Expected: FAIL（`isMobile = false` 未出现）

- [ ] **Step 3: 实现**

在 `WorkspacePanel` 函数签名的解构 props 里加 `isMobile = false`（与现有 `width` 等并列）。`width` 无需特判——MobileShell 传 `width="100%"`，现有 `style={{ width: width ?? DEFAULT_WORKSPACE_WIDTH }}` 直接生效。

在渲染 `<FilePreviewPanel ... />`（文件 tab）加 `isMobile={isMobile}`；在渲染审查窗组件（`IndependentReviewDrawer.jsx` 默认导出 `ReviewChatWindow`，按现有 import 名）处加 `isMobile={isMobile}`。

> 实现锚点：WorkspacePanel 当前用 `width ?? DEFAULT_WORKSPACE_WIDTH`（约 `:324`）。FilePreviewPanel 在 `activeTab === 'files'` 分支渲染；审查窗在 `:455` 一带渲染。

- [ ] **Step 4: 跑测试 + 回归**

Run: `node --test tests/workspacePanel.source.test.mjs`
Expected: PASS
Run: `node --test tests/`
Expected: 全绿（现有全套 + 新增不回归；测试总数以实际为准，勿写死）

- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/WorkspacePanel.jsx frontend/tests/workspacePanel.source.test.mjs
git commit -m "feat(mobile): WorkspacePanel accepts isMobile, threads to FilePreview + review window"
```

---

## Task 3: FilePreviewPanel 接 `isMobile`（禁编辑态 + 去拖动条）

**Files:**
- Modify: `frontend/src/components/FilePreviewPanel.jsx`
- Test: `frontend/tests/filePreviewPanel.source.test.mjs`（已存在，追加用例）

- [ ] **Step 1: 追加失败测试**

```js
// 追加到 frontend/tests/filePreviewPanel.source.test.mjs 末尾
test("FilePreviewPanel isMobile (default false): blocks edit + drops resize divider", () => {
  const s = readFileSync(path.join(__dirname, "../src/components/FilePreviewPanel.jsx"), "utf-8");
  assert.match(s, /isMobile\s*=\s*false/);
  // 进入编辑的 handler 在移动端早返回（只读）
  assert.match(s, /handleEnterEdit[\s\S]{0,200}?if\s*\(\s*isMobile\s*\)\s*return/);
  // 「编辑」按钮、拖动分隔条都被 !isMobile 门控
  assert.match(s, /!isMobile[\s\S]*?编辑<\/button>|\{!isMobile && [\s\S]*?编辑/);
  assert.match(s, /!isMobile[\s\S]*?cursor-row-resize/);
});
```

（若该测试文件的 `readFileSync`/`__dirname` 头与本片段不一致，复用文件已有的 helper 读取 FilePreviewPanel 源即可。）

- [ ] **Step 2: 跑测试确认失败**

Run: `node --test tests/filePreviewPanel.source.test.mjs`
Expected: FAIL

- [ ] **Step 3: 实现**

1. 组件签名解构 props 加 `isMobile = false`。
2. `handleEnterEdit`（约 `:192`，`const handleEnterEdit = useCallback(async () => {`）函数体**第一行**加守卫：
   ```js
   if (isMobile) return // 移动端只读：完全不进入编辑态（spec §4.3/§5）
   ```
3. 「编辑」按钮（约 `:321` `<button onClick={handleEnterEdit} ...>编辑</button>`）整体用 `{!isMobile && (...)}` 包裹：
   ```jsx
   {!isMobile && (
     <button onClick={handleEnterEdit} className="px-3 py-[5px] rounded-ibtn text-12 bg-accent text-white">编辑</button>
   )}
   ```
4. 拖动分隔条（约 `:304-305` 的 `<div onMouseDown={startTreeResize} className="h-[6px] cursor-row-resize ...">`）整体用 `{!isMobile && (...)}` 包裹——移动端文件树/预览保持默认 `treePct`（三七分）固定比例，不拖动。
5. **[R1 NIT] `handleEnterEdit` 的 `useCallback` deps 加 `isMobile`**：当前是 `[currentFile, onReloadFile]`（`:206`）→ 改为 `[currentFile, onReloadFile, isMobile]`，别靠「首屏锁定」隐含成立（isMobile 在闭包里被读，须进 deps 才是正确 React 写法）。

- [ ] **Step 4: 跑测试 + 回归**

Run: `node --test tests/filePreviewPanel.source.test.mjs`
Expected: PASS
Run: `node --test tests/`
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/FilePreviewPanel.jsx frontend/tests/filePreviewPanel.source.test.mjs
git commit -m "feat(mobile): FilePreviewPanel isMobile — read-only (no edit), fixed split (no drag)"
```

---

## Task 4: IndependentReviewDrawer 接 `isMobile`（createPortal 全屏 + 「停止审查」+ 去拖动）

**Files:**
- Modify: `frontend/src/components/IndependentReviewDrawer.jsx`（默认导出 `ReviewChatWindow`）
- Test: `frontend/tests/independentReviewDrawer.source.test.mjs`（已存在，追加用例）

- [ ] **Step 1: 追加失败测试**

```js
// 追加到 frontend/tests/independentReviewDrawer.source.test.mjs 末尾
test("ReviewChatWindow isMobile (default false): portal fullscreen, no drag, stop-label", () => {
  const s = readFileSync(path.join(__dirname, "../src/components/IndependentReviewDrawer.jsx"), "utf-8");
  assert.match(s, /import \{ createPortal \} from ['"]react-dom['"]/);
  assert.match(s, /isMobile\s*=\s*false/);
  // 移动端经 createPortal 挂 document.body（脱离抽屉子树，spec §4.5/§4.7-A）
  assert.match(s, /isMobile\s*\?\s*createPortal\([\s\S]*?document\.body\)/);
  // 移动端关闭按钮语义为「停止审查」
  assert.match(s, /停止审查/);
  // 移动端不绑拖动
  assert.match(s, /isMobile\s*\?\s*undefined\s*:\s*handleDragStart/);
  // 移动端可见文字「停止审查」（不止 title——手机看不到 title，[R1 NIT]）
  assert.match(s, /\{isMobile \? ['"]停止审查['"]/);
});
```

**[R1] 同时改既有两条测试**（改造让它们的硬断言失效，不改会回归红）：
- `independentReviewDrawer.source.test.mjs:35` 的 `assert.match(src, /aria-label="关闭"/);` → `assert.match(src, /aria-label=\{isMobile \? "停止审查" : "关闭"\}/);`
- `independentReviewDrawer.source.test.mjs:41` 的 `assert.match(src, /onMouseDown=\{handleDragStart\}/);` → `assert.match(src, /onMouseDown=\{isMobile \? undefined : handleDragStart\}/);`
- `:36`（`onClick={handleActiveClose}`）、`:42`（`cursor-move` 仍在桌面分支三元里）保持，不改。

- [ ] **Step 2: 跑测试确认失败**

Run: `node --test tests/independentReviewDrawer.source.test.mjs`
Expected: FAIL

- [ ] **Step 3: 实现**

1. 顶部 import 加：`import { createPortal } from 'react-dom'`（`:1` 一带）。
2. 签名 `export default function ReviewChatWindow({ ... })`（`:19`）解构加 `isMobile = false`。
3. 根节点（`:253` `return (` → `:257` 的外层 `<div className="fixed bottom-4 right-4 w-[480px] h-[600px] ... z-50 flex flex-col" style={style}>`）改造为按 `isMobile` 切换 className/拖动/style，并把整个窗口元素提到变量后按 `isMobile` 决定是否 portal：

   ```jsx
   const windowEl = (
     <div
       className={isMobile
         ? "fixed inset-0 w-full bg-card z-50 flex flex-col"
         : "fixed bottom-4 right-4 w-[480px] h-[600px] bg-card border border-border rounded-win shadow-float z-50 flex flex-col"}
       style={isMobile ? { height: '100dvh' } : style}
     >
       <div
         onMouseDown={isMobile ? undefined : handleDragStart}
         className={isMobile
           ? "px-4 py-3 border-b border-border flex items-center justify-between select-none"
           : "px-4 py-3 border-b border-border flex items-center justify-between cursor-move select-none"}
       >
         {/* ...标题/进度（原样）... */}
         <button
           onClick={handleActiveClose}
           className={isMobile
             ? "text-12 text-error border border-error/40 rounded-btn px-3 py-1"
             : "text-t2 hover:text-text text-lg leading-none px-2"}
           title={isMobile ? "停止审查" : "关闭"}
           aria-label={isMobile ? "停止审查" : "关闭"}
         >
           {isMobile ? '停止审查' : (/* 桌面原子节点（× / 图标）原样保留 */ <原桌面子节点/>)}
         </button>
       </div>
       {/* ...正文流 + errored 分支（原样）... */}
     </div>
   )

   return isMobile ? createPortal(windowEl, document.body) : windowEl
   ```

   > 把现有 `:253` 起的 JSX 整体搬进 `windowEl` 常量，只改：① 外层 div 的 className/style 三元；② 拖动 header 的 `onMouseDown`/className 三元；③ 关闭按钮 `title`/`aria-label` 三元（errored 分支里 `:321` 那个二级「关闭」按钮文案可保留，不影响主路径，按需也加 `isMobile ? '停止审查' : '关闭'`）。其余（标题、进度、正文、补充输入框、handleActiveClose 行为）**一字不改**。`handleActiveClose` 仍是 abort+discard，移动端语义即「停止审查」。

- [ ] **Step 4: 跑测试 + 回归**

Run: `node --test tests/independentReviewDrawer.source.test.mjs`
Expected: PASS
Run: `node --test tests/`
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/IndependentReviewDrawer.jsx frontend/tests/independentReviewDrawer.source.test.mjs
git commit -m "feat(mobile): review window isMobile — portal fullscreen, stop-review label, no drag"
```

---

## Task 5: MobileShell 创建（壳结构 + chatPanelRef + 抽屉互斥 + ChatPanel + scrim + 100dvh/safe-area + 无 transform）

**Files:**
- Create: `frontend/src/components/MobileShell.jsx`
- Test: `frontend/tests/mobileShell.source.test.mjs`

- [ ] **Step 1: 写失败测试**

```js
// frontend/tests/mobileShell.source.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const src = () => readFileSync(path.join(__dirname, "../src/components/MobileShell.jsx"), "utf-8");

test("MobileShell 装配三面板 + chatPanelRef + 抽屉互斥", () => {
  const s = src();
  assert.match(s, /import ChatPanel from ['"]\.\/ChatPanel['"]/);
  assert.match(s, /import Sidebar from ['"]\.\/Sidebar['"]/);
  assert.match(s, /import WorkspacePanel from ['"]\.\/WorkspacePanel['"]/);
  assert.match(s, /from ['"]\.\.\/utils\/deviceMode['"]/);
  assert.match(s, /useRef\(null\)/);                 // chatPanelRef
  assert.match(s, /ref=\{chatPanelRef\}/);
  assert.match(s, /nextDrawerState/);                // 互斥状态机驱动
});

test("MobileShell: ChatPanel 顶栏 toggle 接抽屉（不新增顶栏）", () => {
  const s = src();
  assert.match(s, /onToggleSidebar=\{toggleLeft\}/);
  assert.match(s, /onToggleWorkspacePanel=\{toggleRight\}/);
});

test("MobileShell: scrim 关闭 + 100dvh + safe-area", () => {
  const s = src();
  assert.match(s, /bg-scrim\//);                     // scrim 遮罩
  assert.match(s, /onClick=\{closeAll\}/);
  assert.match(s, /100dvh/);                         // 根高度（spec §4.7-B）
  assert.match(s, /safe-area-inset-bottom/);         // composer 安全区
});

test("MobileShell: 抽屉/壳禁 CSS 变换类（保内部 fixed 弹窗，spec §4.7-A）", () => {
  const s = src();
  // [R1] 只扫 className 字符串字面量 + inline style，避免注释里的字样导致 guard 自炸/漏检
  const classNames = [...s.matchAll(/className="([^"]*)"/g)].map((m) => m[1]).join(" ");
  assert.doesNotMatch(classNames, /\b(transform|transform-gpu|translate-[xy]-|-translate-[xy]-|scale-|rotate-|skew-[xy]-|perspective-|blur-|backdrop-blur|filter)\b/);
  assert.doesNotMatch(s, /style=\{\{[^}]*\b(transform|filter)\s*:/);
});

test("MobileShell: 抽屉 wrapper 有显式宽度基准 + 拉满高度（[R1] BLOCKER）", () => {
  const s = src();
  // 左抽屉显式 264px + h-full + flex；右抽屉显式宽 + h-full + flex（给 WorkspacePanel width:100% 视口基准 + 子面板拉满高）
  assert.match(s, /left-0[^"]*h-full w-\[264px\] flex/);
  assert.match(s, /right-0[^"]*h-full w-\[min\(100vw,28rem\)\] flex/);
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `node --test tests/mobileShell.source.test.mjs`
Expected: FAIL（文件不存在）

- [ ] **Step 3: 实现（先到 ChatPanel + scrim，抽屉容器骨架，Sidebar/WorkspacePanel 内容在 Task 6/7 填）**

```jsx
// frontend/src/components/MobileShell.jsx
import { useRef, useState } from 'react'
import ChatPanel from './ChatPanel'
import Sidebar from './Sidebar'
import WorkspacePanel from './WorkspacePanel'
import { nextDrawerState, DRAWER_NONE, DRAWER_LEFT, DRAWER_RIGHT } from '../utils/deviceMode'

// 移动端壳（spec §4）：聊天占满 + 左/右抽屉覆盖，复用桌面三面板，只换摆法。
// 抽屉滑动用 left/right 定位，刻意不用会生成 containing block 的 CSS 变换（保内部 fixed 弹窗满屏，spec §4.7-A）。
// 常驻挂载（关闭不卸载→上传/审查存活，spec §4.6）；根 100dvh + composer safe-area（spec §4.7-B）。
export default function MobileShell(props) {
  const {
    projects, currentProjectId, settings, authUser, theme,
    project, workspace, materials, workspaceRefreshToken, injectedPrompt, workspaceStageCode,
    onSelectProject, onCreateProject, onDeleteProject, onSettingsSaved,
    onLoggedOut, onOpenAdmin, onToggleTheme,
    onMaterialsMerged, onMaterialDeleted, onProjectMutated, onCheckpointSet,
    onInsertPrompt, onInjectedPromptConsumed,
  } = props

  const chatPanelRef = useRef(null)
  const [drawer, setDrawer] = useState(DRAWER_NONE)
  const closeAll = () => setDrawer(DRAWER_NONE)
  const toggleLeft = () => setDrawer((d) => nextDrawerState(d, 'toggleLeft'))
  const toggleRight = () => setDrawer((d) => nextDrawerState(d, 'toggleRight'))

  // Sidebar 回调包装：动作后自动关左抽屉，Sidebar 本体零改（spec §4.2）。
  const handleSelectProject = (p) => { onSelectProject(p); closeAll() }
  const handleCreateProject = async (p) => { const ok = await onCreateProject(p); if (ok) closeAll(); return ok }
  const handleLoggedOut = () => { closeAll(); onLoggedOut() }
  const handleOpenAdmin = () => { closeAll(); onOpenAdmin() }
  // 删除项目刻意不关左抽屉：删完顺手在列表挑下一个（spec §4.2 [R5]）。

  // 审查完成：触发主聊天汇报轮后关右抽屉，落到聊天（spec §5 [R4]）。
  const handleTriggerSystemTurn = (t, m) => { chatPanelRef.current?.triggerSystemTurn(t, m); closeAll() }
  const handleDropPendingReviewTriggers = (t) => chatPanelRef.current?.dropPendingReviewTriggers(t)
  // [R1] 「继续扩写」/回退插入 prompt 后关右抽屉，否则输入框在抽屉背后像没反应。
  const handleInsertPrompt = (text) => { onInsertPrompt(text); closeAll() }

  return (
    <div className="relative w-screen bg-bg overflow-hidden" style={{ height: '100dvh' }}>
      {/* 聊天主区：复用 ChatPanel 自带 60px 顶栏（侧栏按钮/工作区按钮接抽屉）。safe-area 由本壳承担、不碰 ChatPanel。 */}
      <div className="absolute inset-0 flex flex-col" style={{ paddingBottom: 'env(safe-area-inset-bottom)' }}>
        <ChatPanel
          ref={chatPanelRef}
          projectId={currentProjectId}
          project={project}
          settings={settings}
          workspace={workspace}
          materials={materials}
          onMaterialsMerged={onMaterialsMerged}
          onProjectMutated={onProjectMutated}
          onToggleSidebar={toggleLeft}
          onToggleWorkspacePanel={toggleRight}
          injectedPrompt={injectedPrompt}
          onInjectedPromptConsumed={onInjectedPromptConsumed}
        />
      </div>

      {/* scrim：抽屉打开时盖聊天、点击关闭。唯一允许的 dark: 例外（spec §4.6）。 */}
      {drawer !== DRAWER_NONE && (
        <div onClick={closeAll} aria-hidden="true" className="absolute inset-0 z-20 bg-scrim/40 dark:bg-scrim/60" />
      )}

      {/* 左抽屉 = Sidebar。显式 w-[264px] h-full flex 给子面板高度基准（Sidebar 内部 w-[264px]），off-canvas left、常驻挂载。 */}
      <div
        className="absolute top-0 left-0 z-30 h-full w-[264px] flex transition-[left] duration-200 ease-out"
        style={{ left: drawer === DRAWER_LEFT ? '0' : '-110%', visibility: drawer === DRAWER_LEFT ? 'visible' : 'hidden' }}
      >
        <Sidebar
          projects={projects}
          currentProjectId={currentProjectId}
          settings={settings}
          onSelectProject={handleSelectProject}
          onCreateProject={handleCreateProject}
          onDeleteProject={onDeleteProject}
          onSettingsSaved={onSettingsSaved}
          authUser={authUser}
          onLoggedOut={handleLoggedOut}
          onOpenAdmin={handleOpenAdmin}
          theme={theme}
          onToggleTheme={onToggleTheme}
          currentStageCode={workspaceStageCode}
        />
      </div>

      {/* 右抽屉 = WorkspacePanel。显式 w-[min(100vw,28rem)] h-full flex overflow-hidden 给 width:100% 基准 + 拉满高度，off-canvas right、常驻挂载。 */}
      <div
        className="absolute top-0 right-0 z-30 h-full w-[min(100vw,28rem)] flex overflow-hidden transition-[right] duration-200 ease-out"
        style={{ right: drawer === DRAWER_RIGHT ? '0' : '-110%', visibility: drawer === DRAWER_RIGHT ? 'visible' : 'hidden' }}
      >
        <WorkspacePanel
          isMobile={true}
          width="100%"
          projectId={currentProjectId}
          workspace={workspace}
          materials={materials}
          refreshToken={workspaceRefreshToken}
          onMaterialsMerged={onMaterialsMerged}
          onMaterialDeleted={onMaterialDeleted}
          onProjectMutated={onProjectMutated}
          onCheckpointSet={onCheckpointSet}
          onInsertPrompt={handleInsertPrompt}
          onTriggerSystemTurn={handleTriggerSystemTurn}
          onDropPendingReviewTriggers={handleDropPendingReviewTriggers}
        />
      </div>
    </div>
  )
}
```

> 注：本 task 已把完整壳写好（含 Task 6/7 的回调），因为壳是一体的；Task 6/7 仅追加 source-guard 锁死「Sidebar 回调包装语义」与「右抽屉 isMobile/审查接线」，并跑回归。

- [ ] **Step 4: 跑测试确认通过**

Run: `node --test tests/mobileShell.source.test.mjs`
Expected: PASS（4 测试）

- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/MobileShell.jsx frontend/tests/mobileShell.source.test.mjs
git commit -m "feat(mobile): MobileShell — drawer shell, ChatPanel header wiring, scrim, 100dvh/safe-area, no-transform off-canvas"
```

---

## Task 6: MobileShell 左抽屉 Sidebar 回调包装（closeAll / 新建成功才关 / 删除保留）

**Files:**
- Modify: `frontend/src/components/MobileShell.jsx`（Task 5 已写入；本 task 仅锁测）
- Test: `frontend/tests/mobileShell.source.test.mjs`（追加）

- [ ] **Step 1: 追加失败测试**

```js
test("MobileShell: Sidebar 回调由壳包装——选/登出/管理 closeAll，新建成功才关，删除不关", () => {
  const s = src();
  assert.match(s, /handleSelectProject = \(p\) => \{ onSelectProject\(p\); closeAll\(\) \}/);
  assert.match(s, /handleCreateProject = async \(p\) => \{ const ok = await onCreateProject\(p\); if \(ok\) closeAll\(\); return ok \}/);
  assert.match(s, /handleLoggedOut = \(\) => \{ closeAll\(\); onLoggedOut\(\) \}/);
  assert.match(s, /handleOpenAdmin = \(\) => \{ closeAll\(\); onOpenAdmin\(\) \}/);
  // 删除项目直接透传 onDeleteProject（刻意不 closeAll，spec §4.2 [R5]）
  assert.match(s, /onDeleteProject=\{onDeleteProject\}/);
  // Sidebar 本体零改：包装在壳里，不要求 Sidebar 自己加 closeAll
});
```

- [ ] **Step 2: 跑测试**

Run: `node --test tests/mobileShell.source.test.mjs`
Expected: PASS（Task 5 已实现这些；若失败说明 Task 5 代码与合同不符，回 Task 5 对齐）

- [ ] **Step 3: 实现**

无新增代码（Task 5 已写入）。若 Step 2 失败，修正 MobileShell 中对应包装至与测试逐字一致。

- [ ] **Step 4: 回归**

Run: `node --test tests/`
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add frontend/tests/mobileShell.source.test.mjs
git commit -m "test(mobile): lock Sidebar callback wrapping (closeAll/create-success/delete-keeps-open)"
```

---

## Task 7: MobileShell 右抽屉审查接线 + 常驻挂载锁测

**Files:**
- Modify: `frontend/src/components/MobileShell.jsx`（Task 5 已写入；本 task 仅锁测）
- Test: `frontend/tests/mobileShell.source.test.mjs`（追加）

- [ ] **Step 1: 追加失败测试**

```js
test("MobileShell: 右抽屉 WorkspacePanel isMobile + width100% + 审查汇报 closeAll + 常驻挂载", () => {
  const s = src();
  assert.match(s, /<WorkspacePanel[\s\S]*?isMobile=\{true\}/);
  assert.match(s, /width="100%"/);
  // 审查完成：触发主聊天汇报轮后关右抽屉（spec §5 [R4]）
  assert.match(s, /handleTriggerSystemTurn = \(t, m\) => \{ chatPanelRef\.current\?\.triggerSystemTurn\(t, m\); closeAll\(\) \}/);
  assert.match(s, /onTriggerSystemTurn=\{handleTriggerSystemTurn\}/);
  assert.match(s, /onDropPendingReviewTriggers=\{handleDropPendingReviewTriggers\}/);
  // 常驻挂载：WorkspacePanel 不被 {drawer===... && } 条件卸载，只用 visibility/off-canvas 隐藏
  assert.doesNotMatch(s, /drawer === DRAWER_RIGHT && <WorkspacePanel/);
  assert.match(s, /visibility: drawer === DRAWER_RIGHT \? 'visible' : 'hidden'/);
  // [R1] 继续扩写/回退插入 prompt 后关右抽屉（否则输入框在抽屉背后像没反应）
  assert.match(s, /handleInsertPrompt = \(text\) => \{ onInsertPrompt\(text\); closeAll\(\) \}/);
  assert.match(s, /onInsertPrompt=\{handleInsertPrompt\}/);
});
```

- [ ] **Step 2: 跑测试**

Run: `node --test tests/mobileShell.source.test.mjs`
Expected: PASS（Task 5 已实现）

- [ ] **Step 3: 实现**

无新增代码（Task 5 已写入）。若失败修正至与测试一致。

- [ ] **Step 4: 回归 + 构建**

Run: `node --test tests/`
Expected: 全绿
Run: `npm run build`
Expected: 构建成功（MobileShell 现已被 import 前需 Task 8 接入；本步只验证组件本身可编译——若 build 因 MobileShell 未被引用而 tree-shake 提示，忽略，Task 8 接入）

- [ ] **Step 5: 提交**

```bash
git add frontend/tests/mobileShell.source.test.mjs
git commit -m "test(mobile): lock right-drawer isMobile/review-report-closeAll + keep-mounted"
```

---

## Task 8: App.jsx 接入（isMobile 首屏锁定 + 分支 + 上提 AdminPanel）

**Files:**
- Modify: `frontend/src/App.jsx`
- Test: `frontend/tests/appInitGating.source.test.mjs`（已存在，追加用例）

- [ ] **Step 1: 追加失败测试**

```js
// 追加到 frontend/tests/appInitGating.source.test.mjs 末尾（复用文件已有的 appSrc()/读取 helper）
test("App: isMobile 首屏锁定 + MobileShell 分支 + 桌面分支结构原样 + AdminPanel 上提", () => {
  const s = appSrc(); // 若无该 helper，按文件现有方式读 ../src/App.jsx
  // 首屏锁定，无运行时 matchMedia 监听
  assert.match(s, /import \{ isCoarsePointer \} from ['"]\.\/utils\/deviceMode['"]/);
  assert.match(s, /useState\(\(\) => isCoarsePointer\(\)\)/);
  assert.doesNotMatch(s, /addEventListener\(['"]change['"][\s\S]*?pointer: coarse/);
  // 分支
  assert.match(s, /import MobileShell from ['"]\.\/components\/MobileShell['"]/);
  assert.match(s, /isMobile \? \(\s*<MobileShell/);
  // 桌面分支关键结构仍在（!isMobile 的 else 分支）
  assert.match(s, /flex h-screen bg-bg/);
  assert.match(s, /ref=\{setContainerRef\}/);
  assert.match(s, /onMouseDown=\{startWorkspaceResize\}/);
  assert.match(s, /cursor-col-resize/);
  assert.match(s, /showWorkspacePanel &&/);
  // AdminPanel 上提为分支兄弟（在 ErrorBoundary 内、两壳之外渲染一次）
  assert.match(s, /\{showAdmin && authUser\?\.is_admin && <AdminPanel/);
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `node --test tests/appInitGating.source.test.mjs`
Expected: FAIL

- [ ] **Step 3: 实现**

1. 顶部 import：
   ```js
   import { isCoarsePointer } from './utils/deviceMode'
   import MobileShell from './components/MobileShell'
   ```
2. 与其它 `useState` 并列加（首屏锁定，无监听，spec §3.2）：
   ```js
   const [isMobile] = useState(() => isCoarsePointer())
   ```
3. 把 return 的 `<ErrorBoundary>...</ErrorBoundary>` 内容改为分支结构。当前结构（`:390-456`）是：
   ```jsx
   <ErrorBoundary>
     <Toaster position="top-right" />
     <div className="flex h-screen bg-bg">
       {showSidebar && (<Sidebar ... />)}
       <div ref={setContainerRef} className="flex flex-1 min-w-0">
         <ChatPanel ref={chatPanelRef} ... />
         {showWorkspacePanel && (<>...</>)}
       </div>
       {showAdmin && authUser?.is_admin && <AdminPanel onClose={() => setShowAdmin(false)} />}
     </div>
   </ErrorBoundary>
   ```
   改为：
   ```jsx
   <ErrorBoundary>
     <Toaster position="top-right" />
     {isMobile ? (
       <MobileShell
         projects={projects}
         currentProjectId={currentProjectId}
         settings={settings}
         authUser={authUser}
         theme={theme}
         project={currentProject}
         workspace={workspace}
         materials={materials}
         workspaceRefreshToken={workspaceRefreshToken}
         injectedPrompt={injectedPrompt}
         workspaceStageCode={workspaceProjectId === currentProjectId ? workspace?.stage_code : undefined}
         onSelectProject={handleSelectProject}
         onCreateProject={createProject}
         onDeleteProject={deleteProject}
         onSettingsSaved={loadSettings}
         onLoggedOut={() => setAuthUser(null)}
         onOpenAdmin={() => setShowAdmin(true)}
         onToggleTheme={onToggleTheme}
         onMaterialsMerged={handleMaterialsMerged}
         onMaterialDeleted={handleMaterialDeleted}
         onProjectMutated={handleProjectMutated}
         onCheckpointSet={loadWorkspace}
         onInsertPrompt={(text) => setInjectedPrompt(text)}
         onInjectedPromptConsumed={() => setInjectedPrompt(null)}
       />
     ) : (
       <div className="flex h-screen bg-bg">
         {showSidebar && (<Sidebar ... 原样 ... />)}
         <div ref={setContainerRef} className="flex flex-1 min-w-0">
           <ChatPanel ref={chatPanelRef} ... 原样 ... />
           {showWorkspacePanel && (<> ... 原样 ... </>)}
         </div>
       </div>
     )}
     {showAdmin && authUser?.is_admin && <AdminPanel onClose={() => setShowAdmin(false)} />}
   </ErrorBoundary>
   ```
   **关键**：① 桌面 `<div className="flex h-screen bg-bg">...</div>` 内部三栏 JSX **逐行不动**，只是被包进 `: ( ... )` 的 else 分支；② `{showAdmin && ... <AdminPanel/>}` 从桌面 div **内部移到分支外**作两壳共用兄弟（避免落进移动抽屉子树的 transform 祖先；AdminPanel 是 fixed overlay，移出 flex div 渲染等价、桌面行为不变，spec §5 [R3]）。

   > 校验 prop 名：`currentProject`/`handleSelectProject`/`createProject`/`deleteProject`/`loadSettings`/`handleMaterialsMerged`/`handleMaterialDeleted`/`handleProjectMutated`/`loadWorkspace`/`workspaceRefreshToken`/`injectedPrompt`/`workspaceProjectId`/`workspace` 均为 App 中现有标识符（见桌面分支原有传参 `:394-451`）。逐一对齐，勿臆造。

- [ ] **Step 4: 跑测试 + 回归 + 构建**

Run: `node --test tests/appInitGating.source.test.mjs`
Expected: PASS
Run: `node --test tests/`
Expected: 全绿（全套 + 新增；测试总数以实际为准，勿写死）
Run: `npm run build`
Expected: 构建成功

- [ ] **Step 5: 提交**

```bash
git add frontend/src/App.jsx frontend/tests/appInitGating.source.test.mjs
git commit -m "feat(mobile): App wires isMobile (first-paint lock) → MobileShell branch; hoist AdminPanel out of desktop div"
```

---

## Task 9: Auth 屏窄屏（Login + ForcePasswordChange）

**Files:**
- Modify: `frontend/src/components/Login.jsx:32`、`frontend/src/components/ForcePasswordChange.jsx:17`
- Test: `frontend/tests/mobileAuthModals.source.test.mjs`（新建）

- [ ] **Step 1: 写失败测试**

```js
// frontend/tests/mobileAuthModals.source.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const read = (p) => readFileSync(path.join(__dirname, "../src/components/", p), "utf-8");

test("Login 卡片窄屏不溢出（min(344px, calc(100vw-32px))）", () => {
  const s = read("Login.jsx");
  assert.match(s, /w-\[min\(344px,calc\(100vw-32px\)\)\]/);
  assert.doesNotMatch(s, /className="[^"]*\bw-\[344px\]/); // 旧写死宽度已去
});

test("ForcePasswordChange 卡片窄屏不溢出", () => {
  const s = read("ForcePasswordChange.jsx");
  assert.match(s, /w-\[min\(360px,calc\(100vw-32px\)\)\]/);
  assert.doesNotMatch(s, /className="[^"]*\bw-\[360px\]/);
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `node --test tests/mobileAuthModals.source.test.mjs`
Expected: FAIL

- [ ] **Step 3: 实现**

- `Login.jsx:32`：把卡片容器的 `w-[344px]` 替换为 `w-[min(344px,calc(100vw-32px))]`（其余 class 不动）。
- `ForcePasswordChange.jsx:17`：把 `w-[360px]` 替换为 `w-[min(360px,calc(100vw-32px))]`。

> 这两个屏在桌面（≥376px 宽）仍渲染 344/360px（`min` 取较小者 = 固定值），仅在 <376px 窄屏收缩，桌面零变化。

- [ ] **Step 4: 跑测试 + 回归**

Run: `node --test tests/mobileAuthModals.source.test.mjs`
Expected: PASS
Run: `node --test tests/`
Expected: 全绿（注意 `loginErrorHandling.source` 等既有 Login 测试不回归）

- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/Login.jsx frontend/src/components/ForcePasswordChange.jsx frontend/tests/mobileAuthModals.source.test.mjs
git commit -m "feat(mobile): auth screens (login/force-password) shrink on narrow viewport, desktop unchanged"
```

---

## Task 10: 模态框窄屏（ProjectCreateModal + SettingsModal + AdminPanel）

**Files:**
- Modify: `frontend/src/components/ProjectCreateModal.jsx:44`、`SettingsModal.jsx:100`、`AdminPanel.jsx:52`、`Sidebar.jsx:238`（[R1] 删除确认弹窗）
- Test: `frontend/tests/mobileAuthModals.source.test.mjs`（追加）

- [ ] **Step 1: 追加失败测试**

```js
test("ProjectCreateModal 窄屏：宽收缩 + 限高滚动", () => {
  const s = read("ProjectCreateModal.jsx");
  assert.match(s, /w-\[min\(560px,calc\(100vw-32px\)\)\]/);
  assert.match(s, /max-h-\[calc\(100dvh-32px\)\]/);
  assert.match(s, /overflow-y-auto/);
});

test("SettingsModal 窄屏：宽收缩 + 限高滚动", () => {
  const s = read("SettingsModal.jsx");
  assert.match(s, /w-\[min\(560px,calc\(100vw-32px\)\)\]/);
  assert.match(s, /max-h-\[calc\(100dvh-32px\)\]/);
  assert.match(s, /overflow-y-auto/);
});

test("AdminPanel 窄屏：宽收缩 + 限高滚动 + 用户表横向滚动 + 内层 min-w", () => {
  const s = read("AdminPanel.jsx");
  assert.match(s, /w-\[min\(680px,calc\(100vw-32px\)\)\]/);
  assert.match(s, /max-h-\[calc\(100dvh-32px\)\]/);
  assert.match(s, /overflow-x-auto/); // 外层可横向滚动
  assert.match(s, /min-w-\[/);        // [R1] 内层网格给 min-width 才会真的产生横向滚动
});

test("ProjectCreate/Settings 双列窄屏回退单列", () => {
  // [R1] 双列在 <480px 改单列，避免挤压
  assert.match(read("ProjectCreateModal.jsx"), /grid-cols-1 min-\[480px\]:grid-cols-2/);
  assert.match(read("SettingsModal.jsx"), /grid-cols-1 min-\[480px\]:grid-cols-2/);
});

test("Sidebar 删除确认弹窗窄屏不溢出（[R1]）", () => {
  const s = read("Sidebar.jsx");
  assert.match(s, /w-\[min\(384px,calc\(100vw-32px\)\)\]/);
  assert.doesNotMatch(s, /className="[^"]*\bw-96\b/); // 旧固定 384px 已去
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `node --test tests/mobileAuthModals.source.test.mjs`
Expected: FAIL

- [ ] **Step 3: 实现**

- `ProjectCreateModal.jsx:44`：容器 `w-[560px]` → `w-[min(560px,calc(100vw-32px))] max-h-[calc(100dvh-32px)] overflow-y-auto`；若内部为双列网格（`grid-cols-2` 类），加窄屏单列回退（如把固定 `grid-cols-2` 改 `grid-cols-1 min-[480px]:grid-cols-2`，仅在 ≥480px 才双列）。
- `SettingsModal.jsx:100`：同上宽/高/滚动；双列同样加 `grid-cols-1 min-[480px]:grid-cols-2` 回退（若有）。
- `AdminPanel.jsx:52`：`w-[680px]` → `w-[min(680px,calc(100vw-32px))] max-h-[calc(100dvh-32px)] overflow-y-auto`；用户表 5 列 grid 外层包 `overflow-x-auto` + **内层网格容器加 `min-w-[640px]`**（[R1]：只给外层 `overflow-x-auto` 而内层会自适应收缩、不产生横向滚动；须给内层 min-width 才真能横滚）；额度列可编辑 input 保留（见 CLAUDE.md「redesign 三处差距 follow-up」AdminPanel 约束）。
- **[R1] `Sidebar.jsx:238` 删除确认弹窗**：`w-96`（384px fixed overlay，360px 手机会溢出）→ `w-[min(384px,calc(100vw-32px))]`。

> 桌面（≥592/712px 宽）`min()` 取固定值、`min-[480px]:` 命中双列，渲染与今天一致；仅窄屏收缩/单列/横滚。

- [ ] **Step 4: 跑测试 + 回归 + 构建**

Run: `node --test tests/mobileAuthModals.source.test.mjs`
Expected: PASS
Run: `node --test tests/`
Expected: 全绿（`adminPanel.source`/`settingsModal.source`/`projectCreateModal`/`sidebar.source` 既有测试不回归）
Run: `npm run build`
Expected: 成功

- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/ProjectCreateModal.jsx frontend/src/components/SettingsModal.jsx frontend/src/components/AdminPanel.jsx frontend/src/components/Sidebar.jsx frontend/tests/mobileAuthModals.source.test.mjs
git commit -m "feat(mobile): modals (create/settings/admin) + delete-confirm shrink on narrow viewport, desktop unchanged"
```

---

## Task 11: 全量回归 + 护栏 + 桌面 smoke 收口

**Files:**
- Test: 全套；无新代码（除非发现回归）

- [ ] **Step 1: 全量前端测试**

Run: `cd frontend && node --test tests/`
Expected: 全绿。重点确认护栏：`paletteGuard`（无新色/emoji）、`darkClassGuard`（只 `dark:bg-scrim/N`）、`appInitGating`（桌面分支结构 + isMobile 锁定）、`mobileShell.source`、`deviceMode`、`mobileAuthModals.source`。

- [ ] **Step 2: 构建**

Run: `npm run build`
Expected: 成功，无新 warning 阻断。

- [ ] **Step 3: 桌面行为人工 smoke（鼠标设备路径不变）**

在桌面浏览器（鼠标）开发态跑 `npm run dev`，确认：三栏布局、中右拖动分栏、切 tab、文件编辑 dirty 守卫、独立审查浮窗、新建/设置/管理弹窗——**与改造前一致**（`isMobile=false`，走 else 分支）。

- [ ] **Step 4: 移动 smoke（开发态用 Chrome DevTools 设备模拟或真机）**

Chrome DevTools 切设备模拟（触摸）或真机访问 dev 服务：确认抽屉壳出现、☰/▣ 开关左右抽屉、scrim 关闭、聊天可发、文件 tab 只读、审查全屏、弹窗不溢出、软键盘弹出输入框可见。

- [ ] **Step 5: 提交（若有回归修复）**

```bash
git add -A
git commit -m "test(mobile): full regression + guards green; desktop & mobile smoke pass"
```

> 部署在合并后单独走（spec §10：本地 `npm run build` → tar → `.push-file.py kr-web-01` → 服务器 `dist.new` 原子 swap，无须重启 systemd）。真机验收按 spec §8.9 清单走。

---

## 自检（plan vs spec 覆盖）

- §3 触发判定（isCoarsePointer + 首屏锁定）→ Task 1 + Task 8 ✅
- §4.1 复用 ChatPanel 顶栏 → Task 5（`onToggleSidebar=toggleLeft` 等）✅
- §4.2 左抽屉 Sidebar + 回调包装（含删除保留）→ Task 5/6 ✅
- §4.3 右抽屉三 tab + 文件只读 → Task 7 + Task 3 ✅
- §4.5 审查 fixed 全屏 + portal + 停止审查 → Task 4 ✅
- §4.6 常驻挂载（上传存活）→ Task 5/7（visibility 隐藏不卸载）✅
- §4.7-A 禁 transform + portal → Task 4 + Task 5（source-guard）✅
- §4.7-B 100dvh/safe-area/滚动 → Task 5 ✅
- §5 prop 合同 + 审查 ref 链 closeAll → Task 5/7/8 ✅
- §5 AdminPanel/Toaster 挂载 → Task 8 ✅
- §7 Auth/Modal 窄屏 → Task 9/10 ✅
- §8 测试守护（含桌面分支不变 source-guard）→ 各 task source-guard + Task 11 ✅
- §9 非目标（无最小化、无手动开关、无后端改动）→ 计划未触及，符合 ✅
