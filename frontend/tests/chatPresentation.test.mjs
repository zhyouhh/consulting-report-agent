import test from "node:test";
import assert from "node:assert/strict";

import {
  buildProjectWelcomeMessage,
  splitAssistantMessageBlocks,
  shouldFlushStreamingQueueImmediately,
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
