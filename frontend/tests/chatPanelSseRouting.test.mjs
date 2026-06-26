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

test("ChatPanel keeps the legacy diagnostic type:'tool' SSE branch (warnings)", () => {
  const source = readFileSync(
    new URL("../src/components/ChatPanel.jsx", import.meta.url),
    "utf-8",
  );
  // 诊断类工具文本事件（禁工具/畸形/自修正/漏写重试/过多调用）仍走旧分支，appendToolEventContent。
  assert.match(source, /parsed\.type === 'tool'/);
  assert.match(source, /appendToolEventContent\(m\.content, parsed\.data\)/);
});

test("ChatPanel routes structured tool_call/tool_result into msg.toolEvents via reduceToolEvent", () => {
  const source = readFileSync(
    new URL("../src/components/ChatPanel.jsx", import.meta.url),
    "utf-8",
  );
  assert.match(source, /import \{ closePendingToolEvents, reduceToolEvent \} from '\.\.\/utils\/toolEvents'/);
  assert.match(source, /parsed\.type === 'tool_call' \|\| parsed\.type === 'tool_result'/);
  assert.match(source, /toolEvents:\s*reduceToolEvent\(m\.toolEvents \|\| \[\], parsed\)/);
  // tool_call 到来时先冲刷已排队的流式文本，让 pill 落在正文之上的正确位置。
  assert.match(source, /parsed\.type === 'tool_call' && shouldFlushStreamingQueueImmediately\('tool'\)/);
});

test("ChatPanel maps reload tool_events sibling onto msg.toolEvents", () => {
  const source = readFileSync(
    new URL("../src/components/ChatPanel.jsx", import.meta.url),
    "utf-8",
  );
  assert.match(source, /toolEvents:\s*m\.tool_events \|\| \[\]/);
});

test("ChatPanel renders ToolCallList above assistant prose (no inline emoji tool pill)", () => {
  const source = readFileSync(
    new URL("../src/components/ChatPanel.jsx", import.meta.url),
    "utf-8",
  );
  assert.match(source, /import ToolCallList from '\.\/ToolCallList'/);
  assert.match(source, /<ToolCallList toolEvents=\{msg\.toolEvents\} \/>/);
  // 旧的「从 content 文本认 emoji 工具行 → 内联 pill」整段移除。
  assert.doesNotMatch(source, /block\.type === 'tool'/);
});

test("ChatPanel closes pending tool pills on abort and on network failure", () => {
  const source = readFileSync(
    new URL("../src/components/ChatPanel.jsx", import.meta.url),
    "utf-8",
  );
  // 后端在 GeneratorExit（中止 / 断连）上不发收尾 tool_result，前端必须自己收尾仍 pending 的 pill，
  // 否则留永久转圈。两条 catch 路径都调 closePendingToolEvents（中止→已停止生成、失败→连接中断）。
  assert.match(source, /import \{ closePendingToolEvents, reduceToolEvent \} from '\.\.\/utils\/toolEvents'/);
  assert.match(source, /toolEvents:\s*closePendingToolEvents\(m\.toolEvents,\s*'已停止生成'\)/);
  assert.match(source, /toolEvents:\s*closePendingToolEvents\(m\.toolEvents,\s*'连接中断'\)/);
});
