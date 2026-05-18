import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const chatPanelSource = readFileSync(
  new URL("../src/components/ChatPanel.jsx", import.meta.url),
  "utf-8",
);

test("ChatPanel enables GFM markdown so assistant tables render as tables", () => {
  assert.match(chatPanelSource, /import remarkGfm from 'remark-gfm'/);
  assert.match(chatPanelSource, /remarkPlugins=\{\[remarkGfm\]\}/);
});
