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
