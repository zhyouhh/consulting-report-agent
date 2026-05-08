import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  appendThinkingEventContent,
  splitAssistantMessageBlocks,
} from "../src/utils/chatPresentation.js";

function applyAssistantSseEvents(events) {
  return events.reduce((content, event) => {
    if (event.type === "content") return content + event.data;
    if (event.type === "thinking") return appendThinkingEventContent(content, event.data);
    return content;
  }, "");
}

test("thinking SSE deltas accumulate into one trailing thinking block", () => {
  const content = applyAssistantSseEvents([
    { type: "thinking", data: "先判断" },
    { type: "thinking", data: "，再拆解" },
  ]);

  assert.equal(content, "<thinking-block>先判断，再拆解</thinking-block>");
  assert.deepEqual(
    splitAssistantMessageBlocks(content),
    [{ type: "thinking", content: "先判断，再拆解" }],
  );
});

test("thinking SSE after content creates a separate block", () => {
  const content = applyAssistantSseEvents([
    { type: "thinking", data: "先判断" },
    { type: "content", data: "正文输出" },
    { type: "thinking", data: "继续判断" },
  ]);

  assert.equal(
    content,
    "<thinking-block>先判断</thinking-block>正文输出\n<thinking-block>继续判断</thinking-block>",
  );
  assert.deepEqual(
    splitAssistantMessageBlocks(content),
    [
      { type: "thinking", content: "先判断" },
      { type: "text", content: "正文输出" },
      { type: "thinking", content: "继续判断" },
    ],
  );
});

test("ChatPanel routes thinking SSE events through appendThinkingEventContent", () => {
  const source = readFileSync(
    new URL("../src/components/ChatPanel.jsx", import.meta.url),
    "utf-8",
  );

  assert.match(source, /appendThinkingEventContent/);
  assert.match(source, /parsed\.type === 'thinking'/);
  assert.match(source, /content:\s*appendThinkingEventContent\(m\.content,\s*parsed\.data\)/);
});

test("ChatPanel renders thinking blocks with ThinkingBlock", () => {
  const source = readFileSync(
    new URL("../src/components/ChatPanel.jsx", import.meta.url),
    "utf-8",
  );

  assert.match(source, /ThinkingBlock/);
  assert.match(source, /block\.type === 'thinking'/);
  assert.match(source, /<ThinkingBlock key=\{index\} text=\{block\.content\} \/>/);
});
