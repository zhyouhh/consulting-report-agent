import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const drawerSrc = () => readFileSync(path.join(__dirname, "../src/components/IndependentReviewDrawer.jsx"), "utf-8");
const workspaceSrc = () => readFileSync(path.join(__dirname, "../src/components/WorkspacePanel.jsx"), "utf-8");

test("IndependentReviewDrawer.jsx uses AbortController for fetch lifecycle", () => {
  const src = drawerSrc();
  assert.match(src, /new AbortController\(\)/);
  assert.match(src, /controller\.abort\(\)/);
});

test("IndependentReviewDrawer.jsx listens for ESC keydown", () => {
  const src = drawerSrc();
  assert.match(src, /keydown/);
  assert.match(src, /key === ['"]Escape['"]/);
});

test("IndependentReviewDrawer.jsx has no visible close button", () => {
  const src = drawerSrc();
  assert.doesNotMatch(src, />\s*关闭\s*</);
});

test("IndependentReviewDrawer completion path triggers a system turn after ready check", () => {
  const drawer = drawerSrc();
  const workspace = workspaceSrc();
  assert.match(drawer, /review-completed/);
  assert.match(workspace, /independent_review_ready|independentReviewReady/);
  assert.match(workspace, /onTriggerSystemTurn\?\.\(['"]independent_review_done['"]\)/);
});
