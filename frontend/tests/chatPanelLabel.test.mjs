import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const chatPanelSource = readFileSync(
  new URL("../src/components/ChatPanel.jsx", import.meta.url),
  "utf-8",
);
const appSource = readFileSync(
  new URL("../src/App.jsx", import.meta.url),
  "utf-8",
);

test("ChatPanel uses workspace toggle label instead of preview label", () => {
  assert.match(chatPanelSource, /切换工作区/);
  assert.doesNotMatch(chatPanelSource, /切换预览/);
});

test("workspace panel toggle naming no longer uses preview terminology", () => {
  assert.doesNotMatch(chatPanelSource, /onTogglePreview/);
  assert.doesNotMatch(appSource, /showPreview/);
  assert.doesNotMatch(appSource, /onTogglePreview/);
});
