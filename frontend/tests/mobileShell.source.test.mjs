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
  assert.match(s, /useRef\(null\)/);
  assert.match(s, /ref=\{chatPanelRef\}/);
  assert.match(s, /nextDrawerState/);
});

test("MobileShell: ChatPanel 顶栏 toggle 接抽屉（不新增顶栏）", () => {
  const s = src();
  assert.match(s, /onToggleSidebar=\{toggleLeft\}/);
  assert.match(s, /onToggleWorkspacePanel=\{toggleRight\}/);
});

test("MobileShell: scrim 关闭 + 100dvh + safe-area", () => {
  const s = src();
  assert.match(s, /bg-scrim\//);
  assert.match(s, /onClick=\{closeAll\}/);
  assert.match(s, /100dvh/);
  assert.match(s, /safe-area-inset-bottom/);
  assert.match(s, /min-h-0/);
});

test("MobileShell: 抽屉/壳禁 CSS 变换类（保内部 fixed 弹窗）", () => {
  const s = src();
  // 只扫 className 字符串字面量 + inline style，避免注释里的字样导致 guard 自炸/漏检
  const classNames = [...s.matchAll(/className="([^"]*)"/g)].map((m) => m[1]).join(" ");
  assert.doesNotMatch(classNames, /\b(transform|transform-gpu|translate-[xy]-|-translate-[xy]-|scale-|rotate-|skew-[xy]-|perspective-|blur-|backdrop-blur|filter)\b/);
  assert.doesNotMatch(s, /style=\{\{[^}]*\b(transform|filter)\s*:/);
});

test("MobileShell: 抽屉 wrapper 有显式宽度基准 + 拉满高度", () => {
  const s = src();
  assert.match(s, /left-0[^"]*h-full w-\[264px\] flex/);
  assert.match(s, /right-0[^"]*h-full w-\[min\(100vw,28rem\)\] flex/);
});

test("MobileShell: 设置保存后关左抽屉（onSettingsSaved 包装 closeAll），切主题刻意不关", () => {
  const s = src();
  assert.match(s, /handleSettingsSaved = \(\.\.\.args\) => \{ const r = onSettingsSaved\?\.\(\.\.\.args\); closeAll\(\); return r \}/);
  assert.match(s, /onSettingsSaved=\{handleSettingsSaved\}/);
  // 切主题保持裸透传（不 closeAll）
  assert.match(s, /onToggleTheme=\{onToggleTheme\}/);
});

test("MobileShell: Sidebar 回调由壳包装——选/登出/管理 closeAll，新建成功才关，删除不关", () => {
  const s = src();
  assert.match(s, /handleSelectProject = \(p\) => \{ onSelectProject\(p\); closeAll\(\) \}/);
  assert.match(s, /handleCreateProject = async \(p\) => \{ const ok = await onCreateProject\(p\); if \(ok\) closeAll\(\); return ok \}/);
  assert.match(s, /handleLoggedOut = \(\) => \{ closeAll\(\); onLoggedOut\(\) \}/);
  assert.match(s, /handleOpenAdmin = \(\) => \{ closeAll\(\); onOpenAdmin\(\) \}/);
  // 删除项目直接透传 onDeleteProject（刻意不 closeAll）
  assert.match(s, /onDeleteProject=\{onDeleteProject\}/);
});

test("MobileShell: 右抽屉 WorkspacePanel isMobile + width100% + 审查汇报 closeAll + 常驻挂载", () => {
  const s = src();
  // WorkspacePanel opening tag 内含 isMobile={true}（用 tag-bounded，避免跨标签假阳性）
  const wpTag = s.match(/<WorkspacePanel\b[^>]*>/)?.[0] || "";
  assert.match(wpTag, /isMobile=\{true\}/);
  assert.match(wpTag, /width="100%"/);
  // 审查完成：触发主聊天汇报轮后关右抽屉
  assert.match(s, /handleTriggerSystemTurn = \(t, m\) => \{ chatPanelRef\.current\?\.triggerSystemTurn\(t, m\); closeAll\(\) \}/);
  assert.match(s, /onTriggerSystemTurn=\{handleTriggerSystemTurn\}/);
  assert.match(s, /onDropPendingReviewTriggers=\{handleDropPendingReviewTriggers\}/);
  // 常驻挂载：WorkspacePanel 不被 {drawer===... && } 条件卸载，只用 visibility/off-canvas 隐藏
  assert.doesNotMatch(s, /drawer === DRAWER_RIGHT && <WorkspacePanel/);
  assert.match(s, /visibility: drawer === DRAWER_RIGHT \? 'visible' : 'hidden'/);
  // 继续扩写/回退插入 prompt 后关右抽屉
  assert.match(s, /handleInsertPrompt = \(text\) => \{ onInsertPrompt\(text\); closeAll\(\) \}/);
  assert.match(s, /onInsertPrompt=\{handleInsertPrompt\}/);
});

