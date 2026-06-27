import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

// 正文 markdown 渲染已抽到 renderAssistantText（assistantTextRender.jsx）；GFM 表格断言随之迁移。
const assistantTextRenderSource = readFileSync(
  new URL("../src/components/assistantTextRender.jsx", import.meta.url),
  "utf-8",
);

test("assistant prose enables GFM markdown so tables render as tables", () => {
  assert.match(assistantTextRenderSource, /import remarkGfm from 'remark-gfm'/);
  assert.match(assistantTextRenderSource, /remarkPlugins=\{\[remarkGfm\]\}/);
});
