# 前端 UX 翻新 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `consulting-report-agent` 前端从「深紫黑+薄荷绿+emoji」翻新为「海军蓝 `#1B2A4A` + 浅深双主题可切换 + 线性 SVG 图标 + 自托管字体」的精致设计系统，纯前端零后端改动、业务逻辑只换皮、功能零退化。

**Architecture:** CSS 变量（通道形式 `--x: R G B`）做 token 单一真值源（`:root` 浅 / `.dark` 深），Tailwind `theme.extend.colors` 用 `rgb(var(--x) / <alpha-value>)` 映射成语义类；主题靠 `<html>.dark` + localStorage + `index.html` head 同步 bootstrap 防闪；逐组件保留 hooks/effect/ref/axios/状态机、只重绘 JSX/className/含 emoji 文本/旧色 inline style。

**Tech Stack:** React 18 / Vite 8 / Tailwind 3.4.x / PostCSS；Node 原生 `node:test`；自托管 woff2（Hanken Grotesk + IBM Plex Mono）；中文系统栈（PingFang SC / Microsoft YaHei）。

**真值源**：视觉=`design_handoff_frontend_redesign/`（README 逐屏规格 + token 表 + `Prototype-standalone.html` 高保真 mock）；功能=现有 `frontend/src/` 生产代码。冲突时原型定外观、生产定能力（spec §2）。

**Spec**：`docs/superpowers/specs/2026-06-25-frontend-redesign-design.md`（已 Codex 双轨审 5 轮 APPROVED）。所有「不变式」「功能缺口」引用均指该 spec 的 §5 / §2.1。

---

## Pre-flight（已就绪，勿重复）

- 分支 `feat/frontend-redesign` 已建、spec 已 commit。直接在此分支逐 task 提交。
- 现有前端测试基线 `cd frontend && node --test tests/` = **0 fail**（每个 task 收尾必须保持）。
- 不新增 npm 依赖（字体走自托管 woff2 文件，不装包）。
- 命令一律在 `frontend/` 下：`node --test tests/`（全部）、`node --test tests/xxx.test.mjs`（单文件）、`npm run build`。
- 可视化验证（web）：仓库根 `.venv/bin/python run_web.py` → 浏览器 `http://localhost:8888`，用 chrome-devtools MCP 截图。

---

## File Structure

**新建**
- `frontend/src/utils/theme.js` — 主题纯函数（getInitialTheme/applyTheme/toggleTheme）
- `frontend/src/components/icons.jsx` — 共享线性 SVG 图标组件集
- `frontend/src/assets/fonts/*.woff2` — 自托管 Hanken Grotesk(400/500/600/700) + IBM Plex Mono(400/500)
- `frontend/tests/theme.test.mjs` — theme.js 纯函数测
- `frontend/tests/themeBootstrap.source.test.mjs` — index.html bootstrap 顺序/语义 guard
- `frontend/tests/paletteGuard.source.test.mjs` — 旧 palette/emoji 扫描 guard
- `frontend/tests/tokenContract.source.test.mjs` — tailwind token 含 `<alpha-value>` guard

**改写（重绘表现层，保留逻辑）**
- 地基：`frontend/src/index.css`、`frontend/tailwind.config.js`、`frontend/index.html`
- 外壳：`frontend/src/App.jsx`
- 组件：`frontend/src/components/` 下 `Login` `ForcePasswordChange` `Sidebar` `MarkdownMessage` `ThinkingBlock` `ChatPanel` `WorkspacePanel` `StagePanel` `StageAdvanceControl` `FilePreviewPanel` `RollbackMenu` `ConfirmDialog` `ProjectCreateModal` `SettingsModal` `AdminPanel` `IndependentReviewDrawer` `ErrorBoundary`
- 旁路样式：`frontend/src/utils/toast.js`

**不动**：`frontend/src/utils/` 下其余纯函数（业务逻辑）、`frontend/src/api.js`、`frontend/src/main.jsx`（仅确认 ErrorBoundary 外包不动）、所有 `backend/`。

---

## 每组件「换皮」标准流程（批次 1–6 每个组件 task 都按此走）

1. **盘点**：打开生产组件，列出它的「逻辑面」（hooks / useEffect+依赖数组 / ref / imperative handle / axios / 事件 handler）+「交互能力」（按钮/输入/校验/空态/错误条/生成中态）。对照 spec §2.1 / §5 该组件条目。
2. **写/扩 source guard**（TDD）：把该组件的关键不变式写成 `*.source.test.mjs` 断言（effect 依赖、credentials、disabled 守卫、payload 字段、checkpoint key…）。先跑红（若是新断言且当前已满足则跳过红、用于回归锁）。
3. **换皮**：对照 `Prototype-standalone.html` 对应屏，重写 JSX 结构 + className（语义 token 类）+ 把 emoji/旧图标换 `icons.jsx` 的线性 SVG + 把含 emoji 的文本分支去 emoji + 旧色 inline style 换 token。**hooks/effect/ref/axios/状态机/handler 一行不动。**
4. **验功能**：`node --test tests/` 全绿（含新 guard）+ `npm run build` 过。逐条核对第 1 步盘点的交互能力都在。
5. **验视觉**：web 起服务，chrome-devtools 截该屏**浅 + 深两张**，对照原型；核对无 emoji/无紫/线性图标/等宽数字/圆角≤14px/主色 `#1B2A4A`/深色强调 `#7E97CC`/hover。
6. **提交**：`git add` 改动文件 + `git commit`。

> 视觉的「完整代码」真值源是原型 HTML，不在本 plan 内重抄上千行 JSX——plan 锁的是**流程纪律 + 不变式 + 验收**。每个组件 task 给出它**独有**的：文件、原型屏、必须守的 §5 不变式、交互能力清单、特殊坑。

---

## 批次 0：地基（精确代码，不碰组件）

### Task 0a: 自托管字体资产 + @font-face

**Files:**
- Create: `frontend/src/assets/fonts/HankenGrotesk-{400,500,600,700}.woff2`、`IBMPlexMono-{400,500}.woff2`
- Modify: `frontend/src/index.css`（顶部加 `@font-face`）

- [ ] **Step 1: 取字体 woff2**

从 Google Fonts（OFL 许可）下载并放入 `frontend/src/assets/fonts/`：Hanken Grotesk 字重 400/500/600/700、IBM Plex Mono 字重 400/500。文件名按上面 Create 列。可用 `https://gwfh.mranftl.com/fonts`（google-webfonts-helper）批量取 woff2。

- [ ] **Step 2: 在 `src/index.css` 顶部（`@tailwind` 之前）加 @font-face**

```css
@font-face{font-family:'Hanken Grotesk';font-style:normal;font-weight:400;font-display:swap;src:url('./assets/fonts/HankenGrotesk-400.woff2') format('woff2');}
@font-face{font-family:'Hanken Grotesk';font-style:normal;font-weight:500;font-display:swap;src:url('./assets/fonts/HankenGrotesk-500.woff2') format('woff2');}
@font-face{font-family:'Hanken Grotesk';font-style:normal;font-weight:600;font-display:swap;src:url('./assets/fonts/HankenGrotesk-600.woff2') format('woff2');}
@font-face{font-family:'Hanken Grotesk';font-style:normal;font-weight:700;font-display:swap;src:url('./assets/fonts/HankenGrotesk-700.woff2') format('woff2');}
@font-face{font-family:'IBM Plex Mono';font-style:normal;font-weight:400;font-display:swap;src:url('./assets/fonts/IBMPlexMono-400.woff2') format('woff2');}
@font-face{font-family:'IBM Plex Mono';font-style:normal;font-weight:500;font-display:swap;src:url('./assets/fonts/IBMPlexMono-500.woff2') format('woff2');}
```

> 相对路径 `./assets/fonts/`（不是 `../`）——`index.css` 在 `src/` 下，url 相对 CSS 文件解析（spec §3.3 [R2]）。放 `src/assets` 才走 Vite hash，**禁 public/fonts**。

- [ ] **Step 3: build 验证字体进 asset 管线**

Run: `cd frontend && npm run build`
Expected: PASS，`dist/assets/` 下出现带 hash 的 `*.woff2`。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/assets/fonts frontend/src/index.css
git commit -m "feat(fonts): self-host Hanken Grotesk + IBM Plex Mono woff2"
```

### Task 0b: index.css 通道 token（浅深两套 + 清旧色）

**Files:** Modify `frontend/src/index.css`

- [ ] **Step 1: 用通道 token 重写 index.css 主体**（保留 Task 0a 的 @font-face 在最顶）

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

:root{
  --bg:241 241 243; --chat:255 255 255; --ws:244 244 246;
  --card:255 255 255; --card2:250 250 251; --field:255 255 255;
  --border:227 227 229; --col:220 220 223; --hair:240 240 242; --track:232 232 234;
  --text:29 29 31; --t2:110 110 115; --t3:142 142 147;
  --accent:27 42 74; --abright:27 42 74;
  --asoft:233 236 242; --asoftb:215 220 232; --asoftt:27 42 74;
  --sel:27 42 74; --userbub:27 42 74; --stepdone:27 42 74; --dotfuture:200 200 204;
  --scrim:20 22 30; --scrim-a:0.45;
  --success:52 168 83; --warn:183 121 31; --error:217 83 79;
  color-scheme:light;
}
.dark{
  --bg:28 29 33; --chat:32 32 36; --ws:28 29 33;
  --card:38 39 44; --card2:44 45 49; --field:38 39 44;
  --border:52 53 59; --col:44 45 49; --hair:48 49 55; --track:52 53 59;
  --text:236 236 238; --t2:162 163 169; --t3:110 111 117;
  --accent:54 82 126; --abright:126 151 204;
  --asoft:46 60 87; --asoftb:60 74 102; --asoftt:157 176 216;
  --sel:43 62 98; --userbub:54 82 126; --stepdone:90 115 171; --dotfuture:74 75 81;
  --scrim:0 0 0; --scrim-a:0.6;
  --success:70 176 106; --warn:201 162 74; --error:217 83 79;
  color-scheme:dark;
}

body{margin:0;font-family:'Hanken Grotesk','PingFang SC','Microsoft YaHei','Noto Sans SC',system-ui,sans-serif;
  -webkit-font-smoothing:antialiased;background-color:rgb(var(--chat));color:rgb(var(--text));}
::-webkit-scrollbar{width:9px;height:9px;}
::-webkit-scrollbar-track{background:transparent;}
::-webkit-scrollbar-thumb{background:rgb(var(--t3)/.4);border-radius:5px;border:2px solid transparent;background-clip:padding-box;}

.selectable-content,.selectable-content *{user-select:text;-webkit-user-select:text;}
```

> 删掉原 `background:#0f0f23`、`color-scheme:dark` 写死、`.prose-dark` 整块（markdown/thinking/highlight 样式在各自组件 task 里重做成变量驱动，不再集中在这）。每个 token 值核对 README token 表（spec §3.1）。scrim 用独立 `--scrim-a` alpha（遮罩组件用 `rgb(var(--scrim)/var(--scrim-a))` 或 `bg-scrim/45 dark:bg-scrim/60`）。

- [ ] **Step 2: build 验证**

Run: `cd frontend && npm run build`
Expected: PASS（此刻组件仍用旧 `bg-[#...]`，页面会半旧半新，正常——批次 1+ 才逐个换）。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/index.css
git commit -m "feat(theme): channel-form CSS variable tokens for light/dark"
```

### Task 0c: tailwind.config.js 语义类映射

**Files:** Modify `frontend/tailwind.config.js`

- [ ] **Step 1: 写 theme.extend**

```js
/** @type {import('tailwindcss').Config} */
const c = (v) => `rgb(var(${v}) / <alpha-value>)`
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        bg:c('--bg'), chat:c('--chat'), ws:c('--ws'),
        card:c('--card'), card2:c('--card2'), field:c('--field'),
        border:c('--border'), col:c('--col'), hair:c('--hair'), track:c('--track'),
        text:c('--text'), t2:c('--t2'), t3:c('--t3'),
        accent:c('--accent'), abright:c('--abright'),
        asoft:c('--asoft'), asoftb:c('--asoftb'), asoftt:c('--asoftt'),
        sel:c('--sel'), userbub:c('--userbub'), stepdone:c('--stepdone'), dotfuture:c('--dotfuture'),
        scrim:c('--scrim'), success:c('--success'), warn:c('--warn'), error:c('--error'),
      },
      fontFamily: {
        sans:['Hanken Grotesk','PingFang SC','Microsoft YaHei','Noto Sans SC','system-ui','sans-serif'],
        mono:['IBM Plex Mono','monospace'],
      },
      borderRadius: { chip:'5px', tag:'6px', ibtn:'7px', btn:'8px', card:'11px', win:'14px' },
      boxShadow: {
        card:'0 1px 2px rgba(0,0,0,.04)',
        popover:'0 24px 60px rgba(0,0,0,.3)',
        float:'0 24px 70px rgba(0,0,0,.45)',
      },
      fontSize: {
        '2xs':'10.5px','11':'11px','xs':'11.5px','12':'12px','13':'12.5px',
        sm:'13px','15':'13.5px','base':'15px','lg':'17px','xl':'18px',
      },
    },
  },
  plugins: [],
}
```

> 注意 `content` 原本无 `./index.html`，加上（bootstrap/类名扫描）。`border` 等是 Tailwind 既有键，覆盖为 token；若个别工具类冲突在组件 task 暴露时按需调键名。

- [ ] **Step 2: build 验证 `<alpha-value>` 被 Tailwind 3.4 正确编译**

Run: `cd frontend && npm run build`
Expected: PASS。临时在某组件加 `className="bg-card/50"` 验证生成 `rgb(var(--card)/0.5)` 后回退（或在 0g 验）。

- [ ] **Step 3: Commit**

```bash
git add frontend/tailwind.config.js
git commit -m "feat(theme): map semantic Tailwind color/type/radius tokens"
```

### Task 0d: utils/theme.js + 测试

**Files:** Create `frontend/src/utils/theme.js`、`frontend/tests/theme.test.mjs`

- [ ] **Step 1: 写失败测试**

```js
// frontend/tests/theme.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { normalizeTheme, nextTheme } from '../src/utils/theme.js'

test('normalizeTheme: 只认 dark，其余回 light', () => {
  assert.equal(normalizeTheme('dark'), 'dark')
  assert.equal(normalizeTheme('light'), 'light')
  assert.equal(normalizeTheme(null), 'light')
  assert.equal(normalizeTheme('DARK'), 'light')
  assert.equal(normalizeTheme(undefined), 'light')
})
test('nextTheme 翻转', () => {
  assert.equal(nextTheme('light'), 'dark')
  assert.equal(nextTheme('dark'), 'light')
})
```

- [ ] **Step 2: 跑红**

Run: `cd frontend && node --test tests/theme.test.mjs`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现 theme.js**

```js
// frontend/src/utils/theme.js
export const THEME_KEY = 'cra:theme'
export function normalizeTheme(v){ return v === 'dark' ? 'dark' : 'light' }
export function nextTheme(t){ return normalizeTheme(t) === 'dark' ? 'light' : 'dark' }
export function getInitialTheme(){
  try { return normalizeTheme(localStorage.getItem(THEME_KEY)) } catch { return 'light' }
}
export function applyTheme(t){
  const theme = normalizeTheme(t)
  const root = document.documentElement
  if (theme === 'dark') root.classList.add('dark'); else root.classList.remove('dark')
  return theme
}
export function toggleTheme(cur){
  const t = nextTheme(cur)
  try { localStorage.setItem(THEME_KEY, t) } catch { /* ignore */ }
  applyTheme(t)
  return t
}
```

- [ ] **Step 4: 跑绿 + 全套**

Run: `cd frontend && node --test tests/theme.test.mjs && node --test tests/`
Expected: PASS，全套 0 fail。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/utils/theme.js frontend/tests/theme.test.mjs
git commit -m "feat(theme): theme util (normalize/apply/toggle) + tests"
```

### Task 0e: index.html 首屏防闪 bootstrap + 顺序 guard

**Files:** Modify `frontend/index.html`、Create `frontend/tests/themeBootstrap.source.test.mjs`

- [ ] **Step 1: 写 source guard（顺序 + 语义）**

```js
// frontend/tests/themeBootstrap.source.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
const html = readFileSync(new URL('../index.html', import.meta.url), 'utf8')
test('bootstrap 在 head 内、排在 module script 之前', () => {
  const headEnd = html.indexOf('</head>')
  const boot = html.indexOf('cra:theme')
  const mod = html.search(/<script[^>]+type=["']module["']/)
  assert.ok(boot !== -1 && boot < headEnd, 'bootstrap 应在 </head> 之前')
  assert.ok(mod === -1 || boot < mod, 'bootstrap 应排在 module script 之前')
})
test('bootstrap 语义：try/catch + 只 dark 加 .dark', () => {
  assert.match(html, /try\s*\{[\s\S]*cra:theme[\s\S]*===\s*['"]dark['"][\s\S]*classList\.add\(['"]dark['"]\)[\s\S]*\}\s*catch/)
})
```

- [ ] **Step 2: 跑红**

Run: `cd frontend && node --test tests/themeBootstrap.source.test.mjs`
Expected: FAIL。

- [ ] **Step 3: 在 `index.html` `<head>` 内、`<script type="module">` 之前插入**

```html
<script>
  try {
    if (localStorage.getItem('cra:theme') === 'dark') {
      document.documentElement.classList.add('dark');
    } else {
      document.documentElement.classList.remove('dark');
    }
  } catch (e) {}
</script>
```

- [ ] **Step 4: 跑绿 + build**

Run: `cd frontend && node --test tests/themeBootstrap.source.test.mjs && npm run build`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add frontend/index.html frontend/tests/themeBootstrap.source.test.mjs
git commit -m "feat(theme): synchronous anti-FOUC bootstrap in index.html"
```

### Task 0f: 共享图标模块 icons.jsx

**Files:** Create `frontend/src/components/icons.jsx`

- [ ] **Step 1: 实现一组线性 SVG 图标组件**

每个图标是 `({size=16, className=''})=>` 返回 stroke 风格 SVG（`stroke="currentColor"` `fill="none"` `stroke-width` 1.7–2.4，`viewBox="0 0 24 24"`）。先实现这次确定要用的：`IconPlus`（新建报告）、`IconPaperclip`（附件）、`IconSend`、`IconGear`（设置）、`IconSun`/`IconMoon`（主题）、`IconShield`（管理后台）、`IconFile`、`IconTrash`、`IconCheck`、`IconClose`、`IconStop`、`IconSidebar`（切工作区）。颜色全靠 `currentColor`（由父级 text token 决定），故图标自动随主题。

示例（其余照此写）：
```jsx
export const IconPlus = ({size=16, className=''}) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
       strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
    <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
  </svg>
)
```

- [ ] **Step 2: build 验证**

Run: `cd frontend && npm run build`
Expected: PASS（暂无人 import，仅验语法）。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/icons.jsx
git commit -m "feat(icons): shared linear SVG icon set (currentColor)"
```

### Task 0g: 地基验收 + palette/token guard

**Files:** Create `frontend/tests/paletteGuard.source.test.mjs`、`frontend/tests/tokenContract.source.test.mjs`

- [ ] **Step 1: 写 token 契约 guard**

```js
// frontend/tests/tokenContract.source.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
const cfg = readFileSync(new URL('../tailwind.config.js', import.meta.url), 'utf8')
test('颜色 token 走 <alpha-value> 通道形式', () => {
  assert.match(cfg, /rgb\(var\(\$\{v\}\) \/ <alpha-value>\)/)
  assert.match(cfg, /darkMode:\s*['"]class['"]/)
})
```

- [ ] **Step 2: 写 palette/emoji 扫描 guard（allowlist 制）**

```js
// frontend/tests/paletteGuard.source.test.mjs
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
const dir = new URL('../src/components/', import.meta.url)
// 扫所有组件：任意值颜色工具类 + JS 裸 hex（允许 currentColor/transparent）
const ARB = /\b(?:bg|text|border|ring|placeholder|fill|stroke)-\[#[0-9a-fA-F]{3,8}\]/
const EMOJI = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u
test('组件无遗留任意值颜色类（迁移完成后启用，逐组件解除 allowlist）', () => {
  // 初始 allowlist=尚未迁移的组件文件名集合；每个组件 task 完成后从此集合移除该文件。
  const ALLOW = new Set([/* 见下：随迁移进度缩小 */])
  for (const f of readdirSync(dir)) {
    if (!f.endsWith('.jsx') || ALLOW.has(f)) continue
    const src = readFileSync(new URL(f, dir), 'utf8')
    assert.ok(!ARB.test(src), `${f} 仍有任意值颜色类`)
    assert.ok(!EMOJI.test(src), `${f} 仍有 emoji（协议常量在 utils 不在组件，组件应无 emoji）`)
  }
})
```

> ALLOW 初始放**全部尚未迁移的组件 .jsx**（批次 1–6 起始集合）；每完成一个组件 task，从 ALLOW 移除该文件——guard 随迁移收紧，迁移完 ALLOW 空。emoji 协议常量在 `utils/chatPresentation.js`，不在扫描目录（spec §6 [R2]）。

- [ ] **Step 3: 跑全套 + 手动双主题冒烟**

Run: `cd frontend && node --test tests/`
Expected: PASS。
再 `cd .. && .venv/bin/python run_web.py`，chrome-devtools 截首屏：默认浅色；`localStorage.setItem('cra:theme','dark');location.reload()` 后深色无 FOUC。（此刻组件多数仍旧样式，只验地基切换通路。）

- [ ] **Step 4: Commit**

```bash
git add frontend/tests/paletteGuard.source.test.mjs frontend/tests/tokenContract.source.test.mjs
git commit -m "test(theme): token contract + palette/emoji migration guards"
```

---

## 批次 1：外壳 App.jsx

### Task 1: App.jsx 三栏骨架 + 主题接线

**Files:** Modify `frontend/src/App.jsx`（必要时 `frontend/src/components/ErrorBoundary.jsx`、`frontend/src/utils/toast.js`）

**原型屏**：整窗三栏布局（README 布局骨架）。

**必须守的不变式（spec §5 / 现有测试）**：
- init effect 依赖**保持** `[authUser?.uid, authUser?.must_change_password]`（`appInitGating.source.test.mjs`）。
- 工作区拖宽：容器 ref 在排除 Sidebar 的内层 wrapper、clamp 预留 MIN_CHAT、callback-ref 重夹、window resize 重夹（`workspaceResize.source.test.mjs`）。
- 额度刷新三重守卫（`sidebarQuota`/`apiUnauthed`）。
- `main.jsx` 外层 ErrorBoundary 不动。

**新增（唯一允许的新 state）**：theme state。

- [ ] **Step 1: 盘点 + 跑现有相关 guard 确认基线绿**

Run: `cd frontend && node --test tests/appInitGating.source.test.mjs tests/workspaceResize.source.test.mjs`
Expected: PASS。

- [ ] **Step 2: 接 theme state**

在 App 顶层加：
```jsx
import { getInitialTheme, applyTheme, toggleTheme } from './utils/theme'
const [theme, setTheme] = useState(getInitialTheme)
useEffect(() => { applyTheme(theme) }, [theme])
const onToggleTheme = () => setTheme(t => toggleTheme(t))
```
把 `theme` / `onToggleTheme` 透传给 `Sidebar`。**注意**：theme 的 useEffect 是新增的、与 init effect（`[uid, must_change_password]`）**分开**，绝不把 theme 塞进 init effect 依赖。

- [ ] **Step 3: 重绘三栏布局 className（语义 token）**

按原型重写最外层容器、Sidebar 容器、可调区 wrapper（`ref=setContainerRef`，含 ChatPanel + 拖条 + WorkspacePanel）、竖向拖条样式。`workspaceResize.js` 纯函数、`containerRef`/`workspaceWidth`/`showWorkspacePanel` 逻辑**不动**，只换 className/结构。旧 `bg-[#...]` → `bg-bg`/`bg-chat` 等。

- [ ] **Step 3b: 迁移 `utils/toast.js` 的硬编码色（app 全局反馈）**

`toast.js` 是 `react-hot-toast` 的自定义样式（`toast.js:6-7` 等带旧 hex），不在 paletteGuard 扫描目录（`components/`）内。改成主题感知：成功/错误/普通 toast 的底/字/边用 `rgb(var(--card))`/`rgb(var(--text))`/`rgb(var(--success))`/`rgb(var(--error))`（toast 是运行时 JS、用 `getComputedStyle(document.documentElement).getPropertyValue` 或直接 `rgb(var(--x))` 字符串），随 `.dark` 自动切。`showSuccess`/`showError` 等调用签名**不动**。

- [ ] **Step 4: 验**

Run: `cd frontend && node --test tests/ && npm run build`
Expected: PASS。web 截浅深两张，三栏布局对原型；拖宽仍可用、刷新不闪。

- [ ] **Step 5: 从 paletteGuard ALLOW 移除 `App.jsx`（若 App 在扫描范围；App 在 src/ 非 components/，则单列断言或纳入扩展扫描）+ Commit**

```bash
git add frontend/src/App.jsx frontend/tests/
git commit -m "feat(redesign): App shell three-column layout + theme wiring"
```

---

## 批次 2：认证（最简单，先练手）

### Task 2a: Login.jsx

**原型屏**：登录/注册居中卡片。
**不变式（spec §5）**：错误 `detail` 经 `normalizeAuthError` 转字符串（`authError.test.mjs`/`loginErrorHandling.source.test.mjs`）；提交前校验用户名≥3/密码≥6 + trim 用户名。
**交互能力**：登录/注册切换、邀请码字段（注册态）、错误条、主按钮、模式切换链接。

- [ ] **Step 1: 盘点 + 基线 guard**：`node --test tests/loginErrorHandling.source.test.mjs tests/authError.test.mjs` → PASS。
- [ ] **Step 2: 换皮**（卡片/输入/按钮/错误条用 token；wordmark 用 icons 或字母 R 方块；`normalizeAuthError` 调用 + 校验逻辑不动）。
- [ ] **Step 3: 验**：`node --test tests/ && npm run build` → PASS；web 截浅深 + 故意短用户名验错误条不白屏。
- [ ] **Step 4: ALLOW 移除 `Login.jsx` + Commit** `feat(redesign): Login reskin`。

### Task 2b: ForcePasswordChange.jsx

**原型屏**：首次登录改密居中卡片。
**不变式**：`validateNewPassword`（≥8）；改完刷新 authUser 才放行。
- [ ] Step 1 盘点。Step 2 换皮（保留校验 + 提交逻辑）。Step 3 验（`node --test tests/ && npm run build`，web 截浅深）。Step 4 ALLOW 移除 + Commit `feat(redesign): ForcePasswordChange reskin`。

---

## 批次 3：侧栏

### Task 3: Sidebar.jsx（账户区重组 + 主题切换 + 删除弹窗）

**原型屏**：左侧栏（wordmark / 新建报告 / 进行中列表 / 账户行 / 连接卡含今日额度 / 底排设置+主题）。
**不变式（spec §4.4 / §5）**：
- 登出仅 `uid!=='local'`；额度行 `daily_cap_yuan` 是数字才显示（含 local）；admin-only 管理入口。
- `overCap/ratio` 阈值逻辑照搬，`barColor` 换 success/warn/error token（不留 `#ef4444`/`#64ffda`）。
- `describeConnectionMode` / `quotaLabel` / `quotaRatio` 调用不动。
**新增**：头像圆块、管理员 tag、主题切换 ☀/☾（用 `IconSun`/`IconMoon` + 接 props `theme`/`onToggleTheme`）。
**emoji 换 SVG**：`🗑`→`IconTrash`、`👤`→`IconShield`/头像、`⚙`→`IconGear`、`+ 新建报告`→`IconPlus`。
**交互能力**：新建报告（开 modal）、选/删项目（删走确认弹窗）、连接卡点击开设置、登出、管理后台、设置、主题切换。

- [ ] **Step 1: 盘点 + 基线**：`node --test tests/sidebarQuota.source.test.mjs` → PASS。
- [ ] **Step 2: 扩 source guard**：在 `sidebarQuota.source.test.mjs` 把锁死 `#ef4444` 的断言**迁移成语义断言**（断 overCap 用 `error` token 类，如 `bg-error`/`text-error`，不再断 hex）。先改测试（红），再实现。
- [ ] **Step 3: 换皮**（账户区拆三层；保留所有显示条件判断与回调）。
- [ ] **Step 4: 验**：`node --test tests/ && npm run build` → PASS；web 截浅深；验 admin/非-admin、local/web 用户的额度行与登出显隐正确、主题按钮可切。
- [ ] **Step 5: ALLOW 移除 `Sidebar.jsx` + Commit** `feat(redesign): Sidebar account block restructure + theme toggle`。

---

## 批次 4：对话

### Task 4a: MarkdownMessage.jsx

**不变式（spec §4.5）**：项目无 Tailwind typography 依赖；现有硬编码暗色 markdown 样式（表格/工具卡/链接/code/blockquote/KaTeX）改成 token 驱动、浅深都对；`react-markdown` + remark/rehype 链不动。
- [ ] Step 1 盘点（列出所有硬编码色的渲染元素）。Step 2 换皮成 token 类（两主题都验）。Step 3 验 `node --test tests/ && npm run build` + web 截一条含表格/代码/公式的消息浅深两张。Step 4 ALLOW 移除 + Commit `feat(redesign): MarkdownMessage token-driven dual theme`。

### Task 4b: ThinkingBlock.jsx + index.css thinking 样式

**不变式（spec §4.5 [R3]）**：保留 collapsed `<details>` + `unescapeThinkingContent`；真实样式从 `index.css` 旧 `.prose-dark`/thinking 块迁成 token（可保留 `.thinking-block`/`.thinking-content` 类但用 `rgb(var(--...))`）。
- [ ] Step 1 盘点。Step 2 迁样式 + 换皮。Step 3 验 + web 截思考块展开/折叠浅深。Step 4 ALLOW 移除 + Commit `feat(redesign): ThinkingBlock dual-theme styles`。

### Task 4c: ChatPanel.jsx（按区域分段，不一次重写）

**原型屏**：中间对话区（header / 线程消息 / 上下文用量条 / 输入区）。
**不变式（spec §5）**：
- 流式 fetch 带 `credentials:'include'`；SSE 心跳 `: keepalive` 忽略（`sseHeartbeat.test.mjs`）。
- 输入框乐观清空双重守卫（`chatPanelComposerClear.source.test.mjs`）。
- `forwardRef + useImperativeHandle`（triggerSystemTurn/dropPendingReviewTriggers）；pendingTriggerQueue；拖拽 dragActive；生成中「停止」按钮 + abort。
- 「清空对话」handler 在 `loading||uploading` 早返 + 按钮 disabled（`chatPanelClearGuard.source.test.mjs`）。
**交互能力（spec §2.1，必须全保）**：停止按钮、材料可选 chips + `conversionStatusChip`、图片/文档分流入库、`attachment_transcribed` 关联、粘贴文件（`handleComposerPaste`）、拖拽上传、Enter 发送/Shift+Enter 换行、上下文用量条、清空/切工作区 header 按钮。
**emoji 换 SVG**：回形针→`IconPaperclip`、发送→`IconSend`、停止→`IconStop`、清空/工作区 header 图标；助手标识改海军蓝小圆点（无头像）；含 emoji 的内容分支（`ChatPanel.jsx:903` 一带）去 emoji。

- [ ] **Step 1: 盘点 + 基线 guard**：`node --test tests/chatPanelComposerClear.source.test.mjs tests/chatPanelClearGuard.source.test.mjs tests/sseHeartbeat.test.mjs` → PASS。
- [ ] **Step 2: 按区域换皮（顺序：① MarkdownMessage 已先行 → ② header → ③ 消息气泡（助手圆点标识 / 用户海军蓝气泡圆角 13 13 4 13）→ ④ 思考/工具调用 chip → ⑤ 上下文用量条 → ⑥ 附件 chips + 材料选择 → ⑦ composer textarea + 按钮）。每区域只换结构/className/图标，绝不移动 `startStream`/`sendMessage`/abort/队列/SSE 解析逻辑。**
- [ ] **Step 3: 验功能**：`node --test tests/ && npm run build` → PASS；逐条核对交互能力清单（尤其停止、材料 chips、粘贴、拖拽、清空守卫）。
- [ ] **Step 4: 验视觉**：web 截浅深；造一条带工具调用 + 思考块 + 附件 chip 的对话；拖文件到输入区验虚线提示。
- [ ] **Step 5: ALLOW 移除 `ChatPanel.jsx`（+ `MarkdownMessage.jsx`/`ThinkingBlock.jsx` 已移）+ Commit** `feat(redesign): ChatPanel reskin by region (logic preserved)`。

---

## 批次 5：工作区

### Task 5a: WorkspacePanel.jsx（三 tab + 材料库）

**原型屏**：右侧工作区（段控 tabs：阶段/文件/材料）。
**不变式（spec §5 [R3]）**：三 tab 全保；离开「文件」tab 走 dirty guard；材料库列表/空态/工作目录展示全保；删除走 `DELETE /materials/{id}` + `onMaterialDeleted` + `onProjectMutated`；completion 靠 run-bound `{run_id, report_mtime_ns}`；`shouldApplyProjectResponse` 项目切换守卫；ref 链（attemptLeave/isEditing）。
- [ ] Step 1 盘点 + 写 source guard 锁三 tab + 删除回调（红→绿）。Step 2 换皮（tabs 段控 + 材料库；保留所有回调/守卫）。Step 3 验 `node --test tests/ && npm run build` + web 截三 tab 浅深 + 删一份材料验回调。Step 4 ALLOW 移除 + Commit `feat(redesign): WorkspacePanel tabs + materials reskin`。

### Task 5b: StagePanel.jsx

**原型屏**：阶段卡 + 8/7 段 stepper + 字数进度 + 按钮区 + 完成/下一步清单 + ⋯ 回滚入口。
**不变式（spec §5）**：阶段条 7/8 段由 `shouldShowPresentationStage(deliveryMode)` 决定；`isS4ReviewButtonVisible`（字数≥`report_word_floor`）；阶段中文名走 `STAGE_NAMES`、不暴露 S0–S7；stepper 完成/当前/未来三态 + 连接线填充。emoji/旧色换 token+SVG（绿勾→`IconCheck`、空心圈用 dotfuture 描边）。
- [ ] Step 1 盘点。Step 2 换皮（保留 deliveryMode 分支 + 门禁）。Step 3 验 + web 截「仅报告(7段)」与「报告+演示(8段)」两态浅深。Step 4 ALLOW 移除 + Commit `feat(redesign): StagePanel stepper reskin (7/8 by delivery_mode)`。

### Task 5c: StageAdvanceControl.jsx

**不变式（spec §5 [R3]）**：checkpoint 映射照搬（S1 outline-confirmed / S4 review-started / S5 review-passed|clear review-started / S6 presentation-ready / S7 delivery-archived）；只 POST 后等后端刷新、不本地推阶段；`stageToolsRunning` 禁 S5 双按钮；按钮文案随 stageCode（`stagePanelButtons`）。
- [ ] Step 1 盘点。Step 2 换皮（按钮样式 token，逻辑/action key 不动）。Step 3 验 + web 截各阶段按钮态。Step 4 ALLOW 移除 + Commit `feat(redesign): StageAdvanceControl buttons reskin`。

### Task 5d: FilePreviewPanel.jsx + 代码高亮双主题

**原型屏**：文件树（默认 30%）/ 预览，可上下拖。
**不变式（spec §5/§4.5）**：树/预览拖动（`filePanelLayout` 纯函数复用、cleanup ref）；脏离开三按钮守卫（`fileEditState.guardLeave`）；编辑/保存/取消工具栏（仅 editable）；`base_mtime_ns` opaque 字符串透传；`react-markdown` 预览链不动；**代码高亮不 import 两套无 scope 全局 CSS**（改 CSS 变量版或 scope 到 `:root`/`.dark`，spec §4.5 [R3]）。
- [ ] Step 1 盘点（含 `FilePreviewPanel.jsx:10` 固定深色 highlight import）。Step 2 换皮 + highlight 改双主题（推荐 CSS 变量高亮规则在 index.css 按 token）。Step 3 验 + web 截 markdown 文件 + 代码文件预览浅深，验编辑态 + 脏离开弹窗。Step 4 ALLOW 移除 + Commit `feat(redesign): FilePreviewPanel + scoped dual-theme highlight`。

### Task 5e: RollbackMenu.jsx

**不变式（spec §5 [R3]）**：`ROLLBACK_HIDDEN_STAGES={S0,S1}`、`getFirstLevelOption`、**高级展开区 + `getAdvancedRollbackOptions`** + 三个硬编码 clear 入口（s0-interview-done / outline-confirmed / delivery-archived）；每个 clear 弹 `ConfirmDialog`。
- [ ] Step 1 盘点 + 写 source guard 锁高级回退入口渲染（红→绿）。Step 2 换皮（菜单/下拉 token）。Step 3 验 + web 截各阶段一级项 + 高级展开浅深。Step 4 ALLOW 移除 + Commit `feat(redesign): RollbackMenu reskin (advanced options preserved)`。

### Task 5f: ConfirmDialog.jsx

**不变式（spec §5 [R2]）**：`role="dialog"` + `aria-modal` + ESC 关 + 打开 focus 取消按钮 + 关闭恢复焦点；`white-space:pre-line` 多行 body；遮罩点击关 + 内容 stopPropagation。遮罩用 `bg-scrim/45 dark:bg-scrim/60`。
- [ ] Step 1 盘点（含 a11y）。Step 2 换皮（保留 a11y 行为）。Step 3 验 + web 截弹窗浅深 + 验 ESC/focus。Step 4 ALLOW 移除 + Commit `feat(redesign): ConfirmDialog reskin (a11y preserved)`。

---

## 批次 6：弹窗/浮层

### Task 6a: ProjectCreateModal.jsx

**不变式（spec §5 [R2]）**：payload 从主题派生 `name`，web 态**绝不发** `workspace_dir`/`initial_material_paths`（`projectCreatePayload.js` 不动）；7 种报告类型下拉 + 主题 + 截止日期 + 预期篇幅，三者非空校验。遮罩用 scrim。
- [ ] Step 1 盘点 + 基线 `node --test tests/projectCreateModal.test.mjs`。Step 2 换皮（保留 payload 构造 + 校验）。Step 3 验 + web 截浅深 + 验创建流。Step 4 ALLOW 移除 + Commit `feat(redesign): ProjectCreateModal reskin`。

### Task 6b: SettingsModal.jsx

**不变式（spec §5 [R2]）**：`managed_base_url` 只读展示不提交；两模式卡（试用/自定义，选中 accent 描边 + asoft 底）；custom 三字段 + 获取模型 + `custom_context_limit_override` 全保 + custom 非空校验（`settingsModal.source.test.mjs`）。
- [ ] Step 1 盘点 + 基线 `node --test tests/settingsModal.source.test.mjs`。Step 2 换皮（保留契约）。Step 3 验 + web 截两模式卡浅深。Step 4 ALLOW 移除 + Commit `feat(redesign): SettingsModal reskin (custom contract preserved)`。

### Task 6c: AdminPanel.jsx

**不变式（spec §2.1）**：额度列保**可编辑 input + onBlur setCap**（不退化纯文本）；改密 prompt 流、禁用/启用、轮换邀请码、保存允许域名（含默认只读 hosts 提示）全保；`capPayload`/`validateNewPassword`/`formatYuan` 不动。
- [ ] Step 1 盘点（逐个核对 reload/saveHosts/setCap/resetPassword/toggleDisabled/rotateInvite）。Step 2 换皮（用户表 + 邀请码 + 域名区，保留所有 input/onBlur/handler）。Step 3 验 + web 截弹窗浅深 + 验改额度 input 仍可编辑提交。Step 4 ALLOW 移除 + Commit `feat(redesign): AdminPanel reskin (editable cap preserved)`。

### Task 6d: IndependentReviewDrawer.jsx

**原型屏**：右下浮窗（480×600，可拖）。
**不变式（spec §5）**：前端生成 `run_id`（窗口全程不变）；open-effect 守 isOpen 上升沿；409 退避上限 5；completed 自动关不 discard、用户关才 discard；`trigger_metadata` opaque 禁转 Number；stream + discard 带 `credentials:'include'`；**完整状态机**：断流/[DONE]无 review-completed→可续审 errored、errored 留存不自动关、supplement resume、新审查 drop 旧 pending；header `cursor:move` 拖动 `position{x,y}`；错误 detail 不直塞 React 子节点（spec §5 全局规则，本组件一并归一）。
- [ ] Step 1 盘点 + 基线 `node --test tests/independentReviewDrawer.source.test.mjs tests/reviewChatWindow.test.mjs`。Step 2 换皮 + 把 detail 渲染归一成字符串（复用/抽 `normalizeApiErrorDetail`）；浮窗用 `shadow-float` + scrim 无（浮窗非遮罩）。Step 3 验 + web 截浮窗 running/errored/completed 浅深 + 验拖动。Step 4 ALLOW 移除 + Commit `feat(redesign): IndependentReviewDrawer reskin + detail normalization`。

---

## 批次 7：收尾验收

### Task 7: 全量验收 + 护栏收紧

- [ ] **Step 1: paletteGuard ALLOW 应为空**（所有组件已迁移）；若 `App.jsx` 在 `src/` 根需单独扫描，补一条断言扫 `src/App.jsx`。Run: `cd frontend && node --test tests/` → PASS。
- [ ] **Step 2: 全套测试 + build**：`node --test tests/ && npm run build` → 全绿。
- [ ] **Step 3: 对比度按角色测**（spec §6 [R3]）：正文/主文本/操作文字对应 token 算 ≥4.5:1；`t3` 弱文本单独低阈值/豁免。可写一个小脚本读 index.css token 算对比度，正文类断言、弱文本记录不 fail。
- [ ] **Step 4: 双平台逐屏视觉对照**：web 起服务，mac Chrome + Windows Chrome 各截全部屏浅深两套，对照 `Prototype-standalone.html`；核对清单（无 emoji/无紫/线性图标/等宽数字/圆角≤14px/主色/深色强调/hover）+ 中文系统栈在 Windows 雅黑下可接受。
- [ ] **Step 5: Codex 双轨审整分支**（CLAUDE.md 惯例）：spec 轨 + quality 轨独立审 diff，「审→修→再审」到 APPROVED。
- [ ] **Step 6: finishing-a-development-branch**：决定 merge/PR/cleanup。

---

## 风险与回退

- 任一组件 task 若发现某不变式无法在「只换皮」下保住（spec §4.1 边界外），停下、记录、按需在该 task 补最小逻辑说明，不擅自改业务逻辑。
- 每个 commit 是一个可回滚单元；批次 0 地基若验收不过，不进批次 1。
- ChatPanel（最大）按区域分步提交；若单步过大，进一步拆 header/气泡/composer 三 commit。
