import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const readSrc = (rel) => readFileSync(path.join(__dirname, rel), "utf-8");

const appSrc = readSrc("../src/App.jsx");
const workspaceSrc = readSrc("../src/components/WorkspacePanel.jsx");

test("WorkspacePanel root no longer hardcodes w-[28rem] and is width-driven + flex-shrink-0", () => {
  assert.doesNotMatch(workspaceSrc, /w-\[28rem\]/, "根 div 不能再写死 w-[28rem]");
  assert.match(workspaceSrc, /flex-shrink-0/, "根 div 需 flex-shrink-0，防被聊天区压缩到设定宽度以下");
  assert.match(workspaceSrc, /style=\{\{\s*width/, "根 div 宽度需由 style.width 驱动");
});

test("WorkspacePanel accepts a width prop", () => {
  // forwardRef 解构参数里需出现 width
  const header = workspaceSrc.slice(0, workspaceSrc.indexOf("}, ref) {"));
  assert.match(header, /\bwidth\b/, "WorkspacePanel props 需含 width");
});

test("App imports the workspace-resize pure helpers", () => {
  assert.match(appSrc, /from ['"]\.\/utils\/workspaceResize['"]/);
  assert.match(appSrc, /computeWorkspaceWidth/);
  assert.match(appSrc, /parseStoredWorkspaceWidth/);
});

test("App holds workspaceWidth state seeded from localStorage and passes it to WorkspacePanel", () => {
  assert.match(appSrc, /workspaceWidth/, "需有 workspaceWidth state");
  assert.match(appSrc, /parseStoredWorkspaceWidth\(/, "初始宽度从存储解析");
  assert.match(appSrc, /width=\{workspaceWidth\}/, "需把 workspaceWidth 传给 WorkspacePanel");
});

test("App renders a col-resize handle gated by showWorkspacePanel", () => {
  assert.match(appSrc, /cursor-col-resize/, "需有左右拖动手柄样式");
  assert.match(appSrc, /onMouseDown=\{startWorkspaceResize\}/, "手柄绑定 startWorkspaceResize");
  // 手柄与面板同受 showWorkspacePanel 控制（收起面板时不显示手柄）
  assert.match(appSrc, /showWorkspacePanel && \(/);
  // a11y：分隔条语义
  assert.match(appSrc, /role="separator"/);
  assert.match(appSrc, /aria-orientation="vertical"/);
});

test("App resize handler computes width from container rect and persists it", () => {
  assert.match(appSrc, /const startWorkspaceResize/, "需 startWorkspaceResize 处理器");
  assert.match(appSrc, /containerRef/, "需容器 ref 提供矩形给 computeWorkspaceWidth");
  assert.match(appSrc, /computeWorkspaceWidth\(/, "拖动用纯函数换算宽度");
  assert.match(appSrc, /localStorage\.setItem/, "拖动结束需持久化宽度");
  // window 级监听 + 卸载兜底清理，沿用 FilePreviewPanel 的拖动模式（防泄漏）
  assert.match(appSrc, /addEventListener\(['"]mousemove['"]/);
  assert.match(appSrc, /removeEventListener\(['"]mousemove['"]/);
});

test("measured container EXCLUDES the fixed Sidebar (clamp reserves MIN_CHAT from resizable region only)", () => {
  // 容器 ref 必须挂在 Sidebar 之后的可调区域 wrapper（flex-1），不能包住 Sidebar，
  // 否则 clamp 把整窗宽算进去会让聊天区被挤到 ~100px（codex 双轨 BLOCKER）。
  assert.match(appSrc, /ref=\{setContainerRef\}/, "容器用 callback ref（挂载即按真实宽度夹存储宽度）");
  const sidebarIdx = appSrc.indexOf("<Sidebar");
  const containerIdx = appSrc.indexOf("ref={setContainerRef}");
  assert.ok(sidebarIdx > -1 && containerIdx > sidebarIdx, "Sidebar 必须在被测容器之外（之前渲染）");
  // 被测 wrapper 是 flex-1（吃掉 Sidebar 之外的剩余空间）
  assert.match(appSrc, /ref=\{setContainerRef\} className="flex flex-1 min-w-0"/);
});

test("App clamps stored width against the real container on mount via callback ref", () => {
  // callback ref 内用 clampWorkspaceWidth 按真实矩形夹一次——修「存的宽超出当前窗口、启动就挤没聊天区」
  assert.match(appSrc, /const setContainerRef = useCallback/);
  const cbStart = appSrc.indexOf("const setContainerRef = useCallback");
  const cbBody = appSrc.slice(cbStart, cbStart + 320);
  assert.match(cbBody, /getBoundingClientRect/);
  assert.match(cbBody, /clampWorkspaceWidth/);
});
