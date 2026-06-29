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

test("AdminPanel 窄屏：宽收缩 + 限高滚动 + 用户表横向滚动 + 内层 min-w", () => {
  const s = read("AdminPanel.jsx");
  assert.match(s, /w-\[min\(680px,calc\(100vw-32px\)\)\]/);
  assert.match(s, /max-h-\[calc\(100dvh-32px\)\]/);
  assert.match(s, /overflow-x-auto/);
  assert.match(s, /min-w-\[/);
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
