import test from "node:test";
import assert from "node:assert/strict";

import { applyAttachmentTranscribed, historyTranscriptIndicators } from "../src/utils/sseEvents.js";

function sampleMessages() {
  return [
    {
      id: "msg-1",
      role: "user",
      content: "看截图",
      transientAttachments: [
        { id: "att-a", displayName: "a.png" },
        { id: "att-b", displayName: "b.png" },
      ],
    },
    { id: "msg-2", role: "assistant", content: "好的" },
  ];
}

test("applyAttachmentTranscribed marks the matched attachment as parsed", () => {
  const before = sampleMessages();
  const after = applyAttachmentTranscribed(before, {
    message_id: "msg-1",
    attachment_id: "att-a",
    status: "parsed",
  });

  const target = after.find((m) => m.id === "msg-1");
  const attA = target.transientAttachments.find((a) => a.id === "att-a");
  assert.equal(attA.transcribed, true);
  assert.equal(attA.transcriptionStatus, "parsed");
  // The other attachment is untouched.
  const attB = target.transientAttachments.find((a) => a.id === "att-b");
  assert.equal(attB.transcribed, undefined);
});

test("applyAttachmentTranscribed marks failed without setting transcribed=true", () => {
  const after = applyAttachmentTranscribed(sampleMessages(), {
    message_id: "msg-1",
    attachment_id: "att-b",
    status: "failed",
  });
  const attB = after.find((m) => m.id === "msg-1").transientAttachments.find((a) => a.id === "att-b");
  assert.equal(attB.transcriptionStatus, "failed");
  assert.equal(attB.transcribed, false);
});

test("applyAttachmentTranscribed only touches the matching message", () => {
  const before = sampleMessages();
  const after = applyAttachmentTranscribed(before, {
    message_id: "msg-1",
    attachment_id: "att-a",
    status: "parsed",
  });
  // assistant message reference is preserved (no needless re-create)
  assert.equal(after.find((m) => m.id === "msg-2"), before.find((m) => m.id === "msg-2"));
});

test("applyAttachmentTranscribed returns same array reference when no message matches", () => {
  const before = sampleMessages();
  const after = applyAttachmentTranscribed(before, {
    message_id: "msg-missing",
    attachment_id: "att-a",
    status: "parsed",
  });
  assert.equal(after, before);
});

test("applyAttachmentTranscribed returns unchanged when attachment id is absent on the message", () => {
  const before = sampleMessages();
  const after = applyAttachmentTranscribed(before, {
    message_id: "msg-1",
    attachment_id: "att-zzz",
    status: "parsed",
  });
  assert.equal(after, before);
});

test("applyAttachmentTranscribed is defensive against missing fields", () => {
  const before = sampleMessages();
  assert.equal(applyAttachmentTranscribed(before, {}), before);
  assert.equal(applyAttachmentTranscribed(before, { message_id: "msg-1" }), before);
  // No messages / non-array input never throws and yields an empty list (default param).
  assert.deepEqual(applyAttachmentTranscribed(undefined, { message_id: "x", attachment_id: "y" }), []);
  assert.deepEqual(applyAttachmentTranscribed(undefined, undefined), []);
});

test("applyAttachmentTranscribed does not mutate the input messages", () => {
  const before = sampleMessages();
  const snapshot = JSON.stringify(before);
  applyAttachmentTranscribed(before, {
    message_id: "msg-1",
    attachment_id: "att-a",
    status: "parsed",
  });
  assert.equal(JSON.stringify(before), snapshot);
});

// N6 Fix2: persisted attachment_transcripts → reloaded-history bubble indicators.
test("historyTranscriptIndicators maps a parsed transcript to a 已转写 indicator", () => {
  const indicators = historyTranscriptIndicators({
    role: "user",
    attachment_transcripts: [
      { id: "t-1", name: "chart.png", status: "parsed", text: "..." },
    ],
  });
  assert.equal(indicators.length, 1);
  assert.equal(indicators[0].status, "parsed");
  assert.equal(indicators[0].name, "chart.png");
  assert.equal(indicators[0].id, "t-1");
});

test("historyTranscriptIndicators maps a failed transcript to a 没读出来 indicator", () => {
  const indicators = historyTranscriptIndicators({
    attachment_transcripts: [{ id: "t-2", name: "bad.png", status: "failed", text: "" }],
  });
  assert.equal(indicators.length, 1);
  assert.equal(indicators[0].status, "failed");
  assert.equal(indicators[0].name, "bad.png");
});

test("historyTranscriptIndicators treats any non-parsed status as failed", () => {
  const indicators = historyTranscriptIndicators({
    attachment_transcripts: [{ id: "t-3", name: "weird.png", status: "pending" }],
  });
  assert.equal(indicators[0].status, "failed");
});

test("historyTranscriptIndicators falls back to a default name and synthesized id", () => {
  const indicators = historyTranscriptIndicators({
    attachment_transcripts: [{ status: "parsed" }],
  });
  assert.equal(indicators[0].name, "图片");
  assert.equal(indicators[0].id, "transcript-0");
});

test("historyTranscriptIndicators returns [] when there are no transcripts", () => {
  assert.deepEqual(historyTranscriptIndicators({ role: "user" }), []);
  assert.deepEqual(historyTranscriptIndicators({ attachment_transcripts: [] }), []);
  assert.deepEqual(historyTranscriptIndicators({}), []);
  assert.deepEqual(historyTranscriptIndicators(), []);
});
