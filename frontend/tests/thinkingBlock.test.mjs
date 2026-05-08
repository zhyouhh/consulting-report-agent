import test from "node:test";
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

async function loadThinkingBlock() {
  const componentUrl = new URL("../src/components/ThinkingBlock.jsx", import.meta.url);
  const componentPath = fileURLToPath(componentUrl);

  if (!existsSync(componentPath)) {
    throw new Error("Cannot find module ThinkingBlock");
  }

  const chatPresentationUrl = new URL("../src/utils/chatPresentation.js", import.meta.url).href;
  const source = (await readFile(componentPath, "utf-8"))
    .replace("../utils/chatPresentation", chatPresentationUrl);
  const tempDir = await mkdtemp(
    join(dirname(fileURLToPath(import.meta.url)), ".thinking-block-"),
  );
  const tempModulePath = join(tempDir, "ThinkingBlock.mjs");

  try {
    await writeFile(tempModulePath, source, "utf-8");
    return await import(pathToFileURL(tempModulePath).href);
  } finally {
    await rm(tempDir, { recursive: true, force: true });
  }
}

test("ThinkingBlock renders collapsed reasoning content", async () => {
  const { ThinkingBlock } = await loadThinkingBlock();

  const html = renderToStaticMarkup(
    React.createElement(ThinkingBlock, { text: "line 1\nline 2" }),
  );

  assert.match(html, /^<details class="thinking-block">/);
  assert.doesNotMatch(html, /\sopen(=|>| )/);
  assert.match(html, /<summary>推理过程<\/summary>/);
  assert.match(html, /<div class="thinking-content">line 1\nline 2<\/div>/);
});

test("ThinkingBlock renders gracefully with empty text", async () => {
  const { default: ThinkingBlock } = await loadThinkingBlock();

  const html = renderToStaticMarkup(
    React.createElement(ThinkingBlock, { text: "" }),
  );

  assert.match(html, /<details class="thinking-block">/);
  assert.match(html, /<summary>推理过程<\/summary>/);
  assert.match(html, /<div class="thinking-content"><\/div>/);
});
