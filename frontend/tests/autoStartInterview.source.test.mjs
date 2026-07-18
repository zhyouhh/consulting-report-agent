import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

// 新建项目自动开场（project_created 系统轮）wiring 守护。
// 链路：App.createProject 置 autoStartProjectId → ChatPanel 会话加载确认为空 →
// maybeAutoStart（fired ref 防重）→ triggerSystemTurn('project_created')。

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const read = (rel) => readFileSync(path.join(__dirname, rel), "utf-8");
const chatSrc = () => read("../src/components/ChatPanel.jsx");
const appSrc = () => read("../src/App.jsx");
const mobileSrc = () => read("../src/components/MobileShell.jsx");

// 从 openTag 起裁到自闭合 `/>`（props 里含箭头函数 `>`，不能用 [^>]* 匹配整标签）。
function sliceJsxTag(source, openTag) {
  const start = source.indexOf(openTag);
  assert.notEqual(start, -1, `tag not found: ${openTag}`);
  const end = source.indexOf("/>", start);
  assert.notEqual(end, -1, `self-closing end not found for: ${openTag}`);
  return source.slice(start, end);
}

test("ChatPanel accepts auto-start props and fires trigger once per project", () => {
  const s = chatSrc();
  assert.match(s, /autoStartProjectId,\s*\n\s*onAutoStartConsumed,/);
  // fired ref 同步置位防 StrictMode 双执行 / 竞态重复触发
  assert.match(s, /autoStartFiredRef\.current === loadedProjectId\) return/);
  assert.match(s, /autoStartFiredRef\.current = loadedProjectId/);
  assert.match(s, /onAutoStartConsumed\?\.\(loadedProjectId\)/);
  assert.match(s, /triggerSystemTurn\('project_created'\)/);
});

test("ChatPanel auto-start fires only from the confirmed-empty history branch", () => {
  const s = chatSrc();
  // 空历史分支（欢迎语之后）触发；load 失败的 catch 分支不触发（历史状态未知）。
  const thenBranch = s.slice(s.indexOf("// 没有历史，显示欢迎消息"), s.indexOf(".catch(() => {"));
  assert.match(thenBranch, /maybeAutoStartRef\.current\?\.\(requestProjectId\)/);
  const catchBranch = s.slice(s.indexOf(".catch(() => {"), s.indexOf("setTokenUsage(null)"));
  assert.doesNotMatch(catchBranch, /maybeAutoStart/);
});

test("ChatPanel removes a fully-empty assistant placeholder after flushing the stream queue", () => {
  const s = chatSrc();
  // 空轮清理（幂等 no-op 触发零事件收尾）：必须先 flush 残留队列再过滤，防 EOF-without-DONE 误删。
  const cleanupIdx = s.indexOf("m.id !== assistantId || m.content || m.parts?.length || m.toolEvents?.length");
  assert.notEqual(cleanupIdx, -1);
  const before = s.slice(cleanupIdx - 600, cleanupIdx);
  assert.match(before, /flushStreamingQueueImmediately\(assistantId, requestProjectId\)/);
});

test("App marks the freshly created project for auto-start and threads props to both shells", () => {
  const s = appSrc();
  assert.match(s, /pendingAutoStartProjectIds, setPendingAutoStartProjectIds/);
  assert.match(s, /useState\(\(\) => new Set\(\)\)/);
  // createProject 的 proceed 里函数式追加标记（不能覆盖其它项目）
  const proceedBlock = s.match(/const proceed = async \(\) => \{[\s\S]*?\}/)?.[0] || "";
  assert.match(proceedBlock, /setPendingAutoStartProjectIds\(prev => new Set\(prev\)\.add\(createdProject\.id\)\)/);
  // 两个壳都接线（桌面 Pool + MobileShell）
  const chatTag = sliceJsxTag(s, "<ChatPanelPool");
  assert.match(chatTag, /pendingAutoStartProjectIds=\{pendingAutoStartProjectIds\}/);
  assert.match(chatTag, /onAutoStartConsumed=/);
  const mobileTag = sliceJsxTag(s, "<MobileShell");
  assert.match(mobileTag, /pendingAutoStartProjectIds=\{pendingAutoStartProjectIds\}/);
  assert.match(mobileTag, /onAutoStartConsumed=/);
});

test("MobileShell threads auto-start props down to its ChatPanel", () => {
  const s = mobileSrc();
  assert.match(s, /pendingAutoStartProjectIds, onAutoStartConsumed,/);
  const chatTag = sliceJsxTag(s, "<ChatPanelPool");
  assert.match(chatTag, /pendingAutoStartProjectIds=\{pendingAutoStartProjectIds\}/);
  assert.match(chatTag, /onAutoStartConsumed=\{onAutoStartConsumed\}/);
});

test("welcome message no longer invites free-form first instructions", () => {
  const s = read("../src/utils/chatPresentation.js");
  // 旧文案「请直接补充你现在最想让我先做的那一步」引导用户首条消息甩长需求 →
  // 模型误以为可跳过 S0 访谈（试用反馈截图实锤），不得回归。
  assert.doesNotMatch(s, /最想让我先做/);
  assert.match(s, /接下来我会先和你确认几个需求要点/);
});
