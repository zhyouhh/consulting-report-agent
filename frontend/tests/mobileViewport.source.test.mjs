import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

// 移动端视口/触摸全局约定守护（2026-07-12 修「选项目后滑动失效 + 上滑刷新」批次）。
// 这些声明全部只作用于触摸设备（media query / 移动端浏览器专属 meta 字段），桌面 fine-pointer 零影响。

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const html = () => readFileSync(path.join(__dirname, "../index.html"), "utf-8");
const css = () => readFileSync(path.join(__dirname, "../src/index.css"), "utf-8");

test("index.html viewport：interactive-widget=resizes-content，且不半吊子开 viewport-fit=cover", () => {
  const s = html();
  const viewport = s.match(/<meta name="viewport" content="([^"]*)"/)?.[1] || "";
  // Android Chrome 108+ 键盘弹出收缩布局视口（100dvh 跟着变小、输入框浮在键盘上）
  assert.match(viewport, /interactive-widget=resizes-content/);
  assert.match(viewport, /width=device-width/);
  // viewport-fit=cover 只有在全站做齐四边 env(safe-area-inset-*) 时才允许加——目前只有 MobileShell
  // 底部有 safe-area padding，半吊子 cover 会让顶栏/抽屉/admin 页钻进刘海安全区外（codex BLOCKER）
  assert.doesNotMatch(viewport, /viewport-fit/);
  // 不禁用用户缩放（a11y：maximum-scale/user-scalable=no 都不许出现；iOS 输入聚焦放大由 16px 字号下限解决）
  assert.doesNotMatch(viewport, /maximum-scale|user-scalable/);
});

test("index.css 触摸设备块：overscroll-behavior none + 表单字号 16px 下限", () => {
  const s = css();
  const block = s.match(/@media \(pointer: coarse\)\{([\s\S]*?)\n\}/)?.[1] || "";
  assert.ok(block, "缺 @media (pointer: coarse) 块");
  // 杀下拉刷新 + 水平 overscroll 历史导航（抽屉滑动手势的前提）
  assert.match(block, /html,body\{overscroll-behavior:none;\}/);
  // iOS Safari 聚焦 <16px 输入框会放大整页；max(1em,16px) 保留更大字号，!important 压过 Tailwind 字号类
  assert.match(block, /input,textarea,select\{font-size:max\(1em,16px\) !important;\}/);
});

test("ChatPanel 根必须带 min-h-0（MobileShell flex-col 滚动陷阱）", () => {
  // MobileShell 把 ChatPanel 放进 flex-col 包裹层；flex 子项主轴默认 min-height:auto，
  // 缺 min-h-0 时长对话把根撑破视口 → 消息区永不可滚 → scrollIntoView 去滚 overflow-hidden
  // 壳根 → 顶栏/抽屉/scrim 全滚出屏幕（2026-07-12 试用实报「进项目后整个假死」真因）。
  const chatPanel = readFileSync(path.join(__dirname, "../src/components/ChatPanel.jsx"), "utf-8");
  assert.match(chatPanel, /className="flex-1 min-w-0 min-h-0 bg-chat flex flex-col"/);
});

test("index.css：overscroll-behavior 只许出现在 coarse 块内（桌面零影响铁律）", () => {
  const noComments = css().replace(/\/\*[\s\S]*?\*\//g, "");
  const withoutCoarseBlock = noComments.replace(/@media \(pointer: coarse\)\{[\s\S]*?\n\}/, "");
  assert.doesNotMatch(withoutCoarseBlock, /overscroll-behavior/);
});
