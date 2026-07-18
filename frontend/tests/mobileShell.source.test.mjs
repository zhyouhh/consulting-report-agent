import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const src = () => readFileSync(path.join(__dirname, "../src/components/MobileShell.jsx"), "utf-8");

test("MobileShell 装配三面板 + App poolRef + 抽屉互斥", () => {
  const s = src();
  assert.match(s, /import ChatPanelPool from ['"]\.\/ChatPanelPool['"]/);
  assert.match(s, /import Sidebar from ['"]\.\/Sidebar['"]/);
  assert.match(s, /import WorkspacePanel from ['"]\.\/WorkspacePanel['"]/);
  assert.match(s, /from ['"]\.\.\/utils\/deviceMode['"]/);
  assert.match(s, /useRef\(null\)/);
  assert.match(s, /ref=\{chatPanelPoolRef\}/);
  assert.doesNotMatch(s, /const chatPanelRef = useRef/);
  assert.match(s, /nextDrawerState/);
});

test("MobileShell: Pool panelProps 把 ChatPanel 顶栏 toggle 接抽屉（不新增顶栏）", () => {
  const s = src();
  assert.match(s, /onToggleSidebar: toggleLeft/);
  assert.match(s, /onToggleWorkspacePanel: toggleRight/);
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
  assert.match(s, /right-0[^"]*h-full w-\[min\(28rem,calc\(100vw-48px\)\)\] flex/);
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
  // 锁定 handler 真的接到 <Sidebar>（防有人把 onSelectProject={handleSelectProject} 改回 ={onSelectProject}）
  assert.match(s, /onSelectProject=\{handleSelectProject\}/);
  assert.match(s, /onCreateProject=\{handleCreateProject\}/);
  assert.match(s, /onLoggedOut=\{handleLoggedOut\}/);
  assert.match(s, /onOpenAdmin=\{handleOpenAdmin\}/);
  // 删除项目直接透传 onDeleteProject（刻意不 closeAll）
  assert.match(s, /onDeleteProject=\{onDeleteProject\}/);
});

test("MobileShell: 抽屉滑动手势接线 + 右抽屉留 scrim 缝隙", () => {
  const s = src();
  assert.match(s, /from ['"]\.\.\/utils\/drawerSwipe['"]/);
  assert.match(s, /resolveSwipeAction\(/);
  // 根 div 绑 touchstart/touchend
  assert.match(s, /onTouchStart=\{onShellTouchStart\}/);
  assert.match(s, /onTouchEnd=\{onShellTouchEnd\}/);
  // 三个动作都接上
  assert.match(s, /action === 'close'[\s\S]{0,40}?closeAll\(\)/);
  assert.match(s, /action === 'openLeft'[\s\S]{0,80}?nextDrawerState\(d, 'openLeft'\)/);
  assert.match(s, /action === 'openRight'[\s\S]{0,80}?nextDrawerState\(d, 'openRight'\)/);
  // 右抽屉不再满屏：48px 缝隙
  assert.match(s, /w-\[min\(28rem,calc\(100vw-48px\)\)\]/);
  // 手势忽略交互/横滚元素 + fixed 覆盖层（弹窗多开在抽屉子树内，滑关抽屉会连带 visibility:hidden 掉弹窗）
  assert.match(s, /closest\(['"]input, textarea, select, button, a, \[contenteditable\], \.overflow-x-auto, \.fixed['"]\)/);
  assert.match(s, /onTouchCancel=\{onShellTouchCancel\}/);
  // touchstart 取本次新增 touch（changedTouches，非 touches[0]）+ touchend 无 list[0] 错配兜底
  // 窗口 800 跨越 closest 交互/横滚过滤块（含 fixed 注释后实测 <800 字符）
  assert.match(s, /onShellTouchStart[\s\S]{0,800}?changedTouches/);
  assert.doesNotMatch(s, /identifier === start\.id\)\s*\|\|\s*list\[0\]/);
});

test("MobileShell: 浏览器手势接管防线（touch-action）+ cancel 纯清理 + 无 preventDefault", () => {
  const s = src();
  // 壳根 touch-action: pan-y pinch-zoom——横滑不被浏览器当滚动/历史导航接管（接管即 touchcancel、手势失效）。
  // 绝不能加 pan-x（会把横滑还给浏览器）。
  assert.match(s, /touchAction: 'pan-y pinch-zoom'/);
  assert.doesNotMatch(s, /touchAction: '[^']*pan-x/);
  // touchcancel 必须保持纯清理——cancel 也代表系统打断（来电/通知栏/多指接管），
  // 拿最后坐标补判会在非用户意图时误开关抽屉（codex review BLOCKER，勿改回补判）
  assert.match(s, /const onShellTouchCancel = \(\) => \{ touchStartRef\.current = null \}/);
  assert.doesNotMatch(s, /onTouchMove/);
  // 壳绝不 preventDefault 触摸事件（纵向滚动必须交还浏览器）；匹配真实调用形态，注释里提到不算
  assert.doesNotMatch(s, /\.preventDefault\(/);
  // h-screen 是 100dvh 的解析兜底（老 WebKit）
  assert.match(s, /className="relative w-screen h-screen bg-bg overflow-hidden"/);
});

test("MobileShell: 右抽屉 WorkspacePanel isMobile + width100% + 审查汇报 closeAll + 常驻挂载", () => {
  const s = src();
  // WorkspacePanel opening tag 内含 isMobile={true}（用 tag-bounded，避免跨标签假阳性）
  const wpTag = s.match(/<WorkspacePanel\b[^>]*>/)?.[0] || "";
  assert.match(wpTag, /isMobile=\{true\}/);
  assert.match(wpTag, /width="100%"/);
  // 审查完成：触发主聊天汇报轮后关右抽屉
  assert.match(s, /handleTriggerSystemTurn = \(t, m\) => \{ chatPanelPoolRef\.current\?\.triggerSystemTurn\(t, m\); closeAll\(\) \}/);
  assert.match(s, /onTriggerSystemTurn=\{handleTriggerSystemTurn\}/);
  assert.match(s, /onDropPendingReviewTriggers=\{handleDropPendingReviewTriggers\}/);
  // handleDropPendingReviewTriggers 真桥接到 App 持有的 poolRef（防 handler 被清空/改名）
  assert.match(s, /handleDropPendingReviewTriggers = \(t\) => chatPanelPoolRef\.current\?\.dropPendingReviewTriggers\(t\)/);
  // 常驻挂载（强 guard）：右抽屉 wrapper 的 > 后必须直接跟 <WorkspacePanel（只空白、无 {cond && / 三元 门控）
  assert.match(s, /visibility: drawer === DRAWER_RIGHT \? 'visible' : 'hidden' \}\}\s*>\s*<WorkspacePanel\b/);
  // 左抽屉 Sidebar 同样 keep-mounted
  assert.match(s, /visibility: drawer === DRAWER_LEFT \? 'visible' : 'hidden' \}\}\s*>\s*<Sidebar\b/);
  // 保留原窄反向断言 + 加宽防御（多行 && ( 与三元 ? 都拦得住）
  assert.doesNotMatch(s, /drawer === DRAWER_RIGHT && <WorkspacePanel/);
  assert.doesNotMatch(s, /\{\s*drawer\s*===\s*DRAWER_RIGHT\s*&&\s*\(?\s*<WorkspacePanel\b/);
  assert.doesNotMatch(s, /drawer[^?\n]*\?\s*<WorkspacePanel\b/);
  assert.match(s, /visibility: drawer === DRAWER_RIGHT \? 'visible' : 'hidden'/);
  // 继续扩写/回退插入 prompt 后关右抽屉
  assert.match(s, /handleInsertPrompt = \(text\) => \{ onInsertPrompt\(text\); closeAll\(\) \}/);
  assert.match(s, /onInsertPrompt=\{handleInsertPrompt\}/);
});
