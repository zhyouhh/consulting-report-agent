import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

// 文件内链 wiring 守护（2026-07-09 试用反馈③）：
// 聊天区工具 pill / 正文反引号文件名 → onOpenWorkspaceFile → WorkspacePanel.openFile
//（桌面 App 确保面板可见；移动端 MobileShell 拉开右抽屉）。

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const read = (rel) => readFileSync(path.join(__dirname, rel), "utf-8");

test("ToolCallPill renders a link only for successful file-target events, as span (not nested button)", () => {
  const s = read("../src/components/ToolCallPill.jsx");
  assert.match(s, /import \{ pathFromToolEvent \} from '\.\.\/utils\/workspaceFileLinks'/);
  // 只在成功 + 有回调时给链接（失败/进行中的写入没有可看的产物）
  assert.match(s, /onOpenFile && status === 'success' \? pathFromToolEvent\(event\) : null/);
  // 外层 Tag 可能是 button（expandable）——内链必须是 span+role="link"，不得嵌套 <button>
  const linkBlock = s.slice(s.indexOf("{linkPath ? ("), s.indexOf(") : (", s.indexOf("{linkPath ? (")));
  assert.match(linkBlock, /role="link"/);
  assert.doesNotMatch(linkBlock, /<button/);
  // 点击不冒泡到 pill 的展开/收起
  assert.match(s, /e\.stopPropagation\(\)/);
  // append_report_draft（arg 空）显示中文名兜底
  assert.match(s, /displayName\(linkPath\)/);
});

test("assistantTextRender links backticked known filenames only when onOpenFile provided", () => {
  const s = read("../src/components/assistantTextRender.jsx");
  assert.match(s, /import \{ resolveWorkspaceFileLink \} from '\.\.\/utils\/workspaceFileLinks'/);
  // 无回调 → 逐字回退共享组件（零回归：审查窗等旧调用不受影响）
  assert.match(s, /onOpenFile \? buildFileLinkComponents\(onOpenFile\) : assistantMarkdownComponents/);
  // 只重写 inline code；匹配不上回落 baseCode
  assert.match(s, /const baseCode = assistantMarkdownComponents\.code/);
  assert.match(s, /resolveWorkspaceFileLink\(raw\)/);
  assert.match(s, /return baseCode\(\{ node, inline, children, \.\.\.props \}\)/);
});

test("WorkspacePanel exposes openFile through the edit-guard (attemptLeave)", () => {
  const s = read("../src/components/WorkspacePanel.jsx");
  const handleBlock = s.slice(s.indexOf("useImperativeHandle(ref"), s.indexOf("}), [loadFile])"));
  assert.match(handleBlock, /openFile: \(path\) => \{/);
  assert.match(handleBlock, /setActiveTab\('files'\)/);
  assert.match(handleBlock, /loadFile\(path\)/);
  // 编辑态 dirty 必须经 attemptLeave（三按钮弹窗）再跳
  assert.match(handleBlock, /fp\.attemptLeave\(doOpen\)/);
});

test("WorkspacePanel imperative handle sits AFTER loadFile declaration (TDZ crash guard)", () => {
  // deps 数组 [loadFile] 在 hook 调用点即求值：若 hook 排在 const loadFile 之前，
  // 渲染直接 ReferenceError 崩整个面板（codex 整分支审 BLOCKER——纯 source 测试渲染不到，
  // 用源码顺序锁死）。
  const s = read("../src/components/WorkspacePanel.jsx");
  const loadFileIdx = s.indexOf("const loadFile = useCallback");
  const handleIdx = s.indexOf("useImperativeHandle(ref");
  assert.ok(loadFileIdx !== -1 && handleIdx !== -1);
  assert.ok(
    loadFileIdx < handleIdx,
    "useImperativeHandle references loadFile in deps — it must come after the declaration",
  );
});

test("assistant prose file-link click suppresses enclosing anchor navigation", () => {
  const s = read("../src/components/assistantTextRender.jsx");
  // [`outline.md`](url) → 按钮嵌在 <a> 内：不 preventDefault 会同时触发锚点导航把 SPA 导走。
  assert.match(s, /e\.preventDefault\(\); e\.stopPropagation\(\); onOpenFile\(linkPath\)/);
});

test("App ensures the workspace panel is visible before opening a file (desktop)", () => {
  const s = read("../src/App.jsx");
  const handler = s.slice(
    s.indexOf("const handleOpenWorkspaceFile"),
    s.indexOf("}", s.indexOf("workspacePanelRef.current?.openFile(path)\n  }")) + 1,
  );
  assert.match(handler, /setShowWorkspacePanel\(true\)/);
  // 面板收起时 ref 未挂载：setTimeout 排到 commit 后再调 openFile
  assert.match(handler, /setTimeout\(\(\) => workspacePanelRef\.current\?\.openFile\(path\), 0\)/);
  // 桌面 ChatPanel 接线
  const chatTagEnd = s.indexOf("/>", s.indexOf("<ChatPanel"));
  const chatTag = s.slice(s.indexOf("<ChatPanel"), chatTagEnd);
  assert.match(chatTag, /onOpenWorkspaceFile=\{handleOpenWorkspaceFile\}/);
});

test("MobileShell opens the right drawer and threads openFile via its workspacePanelRef", () => {
  const s = read("../src/components/MobileShell.jsx");
  assert.match(s, /const workspacePanelRef = useRef\(null\)/);
  const handler = s.slice(
    s.indexOf("const handleOpenWorkspaceFile"),
    s.indexOf("return (", s.indexOf("const handleOpenWorkspaceFile")),
  );
  assert.match(handler, /nextDrawerState\(d, 'openRight'\)/);
  assert.match(handler, /workspacePanelRef\.current\?\.openFile\(path\)/);
  // ChatPanel 收内链回调；右抽屉 WorkspacePanel 挂 ref（常驻挂载，ref 始终可用）
  assert.match(s, /onOpenWorkspaceFile=\{handleOpenWorkspaceFile\}/);
  assert.match(s, /<WorkspacePanel\s*\n\s*ref=\{workspacePanelRef\}/);
});
