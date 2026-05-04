import test from "node:test";
import assert from "node:assert/strict";

import {
  appendToolEventContent,
  buildProjectWelcomeMessage,
  extractSseDataPayload,
  getStreamResponseError,
  shouldRenderSystemNoticeMessage,
  shouldContinueSseStream,
  sanitizeAssistantMessage,
  splitAssistantMessageBlocks,
  shouldFlushStreamingQueueImmediately,
  stripStageAckTags,
  takeStreamingTextSlice,
} from "../src/utils/chatPresentation.js";

test("takeStreamingTextSlice consumes a fixed number of characters", () => {
  assert.deepEqual(
    takeStreamingTextSlice("猪猪侠研究报告", 4),
    {
      emitted: "猪猪侠研",
      remaining: "究报告",
    },
  );
});

test("buildProjectWelcomeMessage reflects seeded project metadata", () => {
  const message = buildProjectWelcomeMessage({
    name: "猪猪侠研究项目",
    project_type: "strategy-consulting",
    theme: "猪猪侠IP研究",
    target_audience: "高层决策者",
    deadline: "2026-04-01",
    expected_length: "3000字",
  });

  assert.match(message, /猪猪侠研究项目/);
  assert.match(message, /猪猪侠IP研究/);
  assert.match(message, /高层决策者/);
  assert.match(message, /2026-04-01/);
});

test("shouldFlushStreamingQueueImmediately only flushes for disruptive events", () => {
  assert.equal(shouldFlushStreamingQueueImmediately("tool"), true);
  assert.equal(shouldFlushStreamingQueueImmediately("error"), true);
  assert.equal(shouldFlushStreamingQueueImmediately("abort"), true);
  assert.equal(shouldFlushStreamingQueueImmediately("usage"), false);
  assert.equal(shouldFlushStreamingQueueImmediately("complete"), false);
});

test("extractSseDataPayload tolerates optional spaces and CRLF", () => {
  assert.equal(extractSseDataPayload("data:[DONE]\r"), "[DONE]");
  assert.equal(
    extractSseDataPayload("data:   {\"type\":\"usage\"}\r"),
    "{\"type\":\"usage\"}",
  );
  assert.equal(extractSseDataPayload("event: done"), null);
});

test("shouldContinueSseStream stops immediately for explicit done or reader completion", () => {
  assert.equal(
    shouldContinueSseStream({ readerDone: false, streamCompleted: false }),
    true,
  );
  assert.equal(
    shouldContinueSseStream({ readerDone: false, streamCompleted: true }),
    false,
  );
  assert.equal(
    shouldContinueSseStream({ readerDone: true, streamCompleted: false }),
    false,
  );
});

test("splitAssistantMessageBlocks preserves tool and text ordering", () => {
  assert.deepEqual(
    splitAssistantMessageBlocks([
      "先给一句正文",
      "🔧 调用工具: web_search({\"query\":\"猪猪侠\"})",
      "✅ 结果: {'status':'success'}",
      "再继续第二句正文",
    ].join("\n")),
    [
      { type: "text", content: "先给一句正文" },
      { type: "tool", content: "🔧 调用工具: web_search({\"query\":\"猪猪侠\"})" },
      { type: "tool", content: "✅ 结果: {'status':'success'}" },
      { type: "text", content: "再继续第二句正文" },
    ],
  );
});

test("appendToolEventContent terminates tool events before following assistant text", () => {
  const content = appendToolEventContent(
    appendToolEventContent("", "✅ 结果: xxx"),
    "✅ 结果: yyy",
  ) + "正文段";

  assert.deepEqual(
    splitAssistantMessageBlocks(content),
    [
      { type: "tool", content: "✅ 结果: xxx" },
      { type: "tool", content: "✅ 结果: yyy" },
      { type: "text", content: "正文段" },
    ],
  );
});

test("getStreamResponseError returns null for successful SSE responses", async () => {
  const response = new Response("data: [DONE]\n\n", {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });

  assert.equal(await getStreamResponseError(response), null);
});

test("getStreamResponseError extracts json error detail for non-ok responses", async () => {
  const response = new Response(JSON.stringify({ detail: "transient_attachments 只允许图片类型" }), {
    status: 422,
    headers: { "content-type": "application/json" },
  });

  assert.equal(
    await getStreamResponseError(response),
    "transient_attachments 只允许图片类型",
  );
});

test("stripStageAckTags removes valid-key assistant tag", () => {
  assert.equal(
    stripStageAckTags('<stage-ack action="set">interview_done</stage-ack>\n正文继续'),
    "正文继续",
  );
});

test("stripStageAckTags removes unknown-key tag too", () => {
  assert.equal(
    stripStageAckTags('<stage-ack action="set">unknown_key_xyz</stage-ack>\n正文继续'),
    "正文继续",
  );
});

test("stripStageAckTags removes clear-action tag", () => {
  assert.equal(
    stripStageAckTags('<stage-ack action="clear">interview_done</stage-ack>'),
    "",
  );
});

test("stripStageAckTags preserves no-tag content", () => {
  assert.equal(
    stripStageAckTags("这是普通正文，没有任何 tag。"),
    "这是普通正文，没有任何 tag。",
  );
});

test("splitAssistantMessageBlocks applies stripStageAckTags first", () => {
  assert.deepEqual(
    splitAssistantMessageBlocks(
      '<stage-ack action="set">interview_done</stage-ack>\n正文段落',
    ),
    [{ type: "text", content: "正文段落" }],
  );
});

test("system_notice with surface_to_user=false is not renderable", () => {
  assert.equal(
    shouldRenderSystemNoticeMessage({
      role: "system_notice",
      surface_to_user: false,
      reason: "hide",
    }),
    false,
  );
  assert.equal(
    shouldRenderSystemNoticeMessage({
      role: "system_notice",
      surface_to_user: true,
      reason: "show",
    }),
    true,
  );
  assert.equal(
    shouldRenderSystemNoticeMessage({
      role: "system_notice",
      reason: "legacy default show",
    }),
    true,
  );
});

test("sanitizeAssistantMessage drops legacy fallback assistant", () => {
  const msg = { role: "assistant", content: "（本轮无回复）" };
  assert.equal(sanitizeAssistantMessage(msg), null);
});

test("sanitizeAssistantMessage drops user_visible_fallback assistant", () => {
  const msg = {
    role: "assistant",
    content: "（这一轮我没有产出可见回复，可能是处理过程中断了。请把刚才的需求换个说法再发一次。）",
  };
  assert.equal(sanitizeAssistantMessage(msg), null);
});

test("sanitizeAssistantMessage keeps user role with same text", () => {
  const msg = { role: "user", content: "（本轮无回复）" };
  assert.deepEqual(sanitizeAssistantMessage(msg), msg);
});

test("sanitizeAssistantMessage keeps normal assistant", () => {
  const msg = { role: "assistant", content: "real reply" };
  assert.deepEqual(sanitizeAssistantMessage(msg), msg);
});
