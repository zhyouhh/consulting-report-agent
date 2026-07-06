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

test("AdminPage（独立页）窄屏：表格横向滚动 + 内层 min-w + 响应式栅格", () => {
  // 2026-07-06：AdminPanel 弹窗升级为 /admin 独立页面，窄屏约束改到页内表格容器。
  const s = read("AdminPage.jsx");
  assert.match(s, /overflow-x-auto/);
  assert.match(s, /min-w-\[600px\]/);   // 用户表内层
  assert.match(s, /min-w-\[560px\]/);   // 用量明细内层
  assert.match(s, /min-\[640px\]:grid-cols-4/);   // 概览卡片窄屏回退 2 列
  assert.match(s, /min-\[720px\]:grid-cols-2/);   // 邀请码/域名窄屏回退单列
});

test("ProjectCreate/Settings 双列窄屏回退单列", () => {
  assert.match(read("ProjectCreateModal.jsx"), /grid-cols-1 min-\[480px\]:grid-cols-2/);
  assert.match(read("SettingsModal.jsx"), /grid-cols-1 min-\[480px\]:grid-cols-2/);
});

test("Sidebar 删除确认弹窗窄屏不溢出", () => {
  const s = read("Sidebar.jsx");
  assert.match(s, /w-\[min\(384px,calc\(100vw-32px\)\)\]/);
  assert.doesNotMatch(s, /className="[^"]*\bw-96\b/); // 旧固定 384px 已去
});
