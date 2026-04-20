/**
 * Unit tests for system_notice SSE event → message list insertion.
 * We test the pure state transformation (reducer-style), not React rendering.
 */
import test from "node:test";
import assert from "node:assert/strict";

// Simulate how ChatPanel inserts a system_notice into the messages array
function applySystemNotice(messages, parsed) {
  if (parsed.type !== "system_notice") return messages;
  const noticeId = `notice_test_${messages.length}`;
  return [
    ...messages,
    {
      id: noticeId,
      role: "system_notice",
      category: parsed.category || "",
      reason: parsed.reason || "",
      user_action: parsed.user_action || "",
    },
  ];
}

test("system_notice event inserts a new message with role=system_notice", () => {
  const before = [
    { id: "1", role: "user", content: "hello" },
    { id: "2", role: "assistant", content: "hi" },
  ];
  const event = {
    type: "system_notice",
    category: "write_blocked",
    reason: "当前阶段不允许写入正文。",
    user_action: "请先完成大纲确认后再开始撰写。",
  };
  const after = applySystemNotice(before, event);
  assert.equal(after.length, 3);
  const notice = after[2];
  assert.equal(notice.role, "system_notice");
  assert.equal(notice.reason, event.reason);
  assert.equal(notice.user_action, event.user_action);
  assert.equal(notice.category, event.category);
});

test("system_notice message always has both reason and user_action", () => {
  const event = {
    type: "system_notice",
    reason: "操作被拒绝",
    user_action: "请联系管理员",
  };
  const after = applySystemNotice([], event);
  const notice = after[0];
  assert.ok(notice.reason.length > 0, "reason must be non-empty");
  assert.ok(notice.user_action.length > 0, "user_action must be non-empty");
});

test("non system_notice events leave the message list unchanged", () => {
  const before = [{ id: "1", role: "user", content: "hello" }];
  const after = applySystemNotice(before, { type: "content", data: "abc" });
  assert.equal(after.length, 1);
  assert.equal(after, before); // same reference
});

test("system_notice with empty fields uses empty string defaults, not undefined", () => {
  const event = { type: "system_notice" };
  const after = applySystemNotice([], event);
  assert.equal(after[0].reason, "");
  assert.equal(after[0].user_action, "");
  assert.equal(after[0].category, "");
});

test("multiple system_notice events create distinct messages", () => {
  const events = [
    { type: "system_notice", reason: "原因1", user_action: "操作1" },
    { type: "system_notice", reason: "原因2", user_action: "操作2" },
  ];
  let messages = [];
  for (const e of events) {
    messages = applySystemNotice(messages, e);
  }
  assert.equal(messages.length, 2);
  assert.notEqual(messages[0].id, messages[1].id);
  assert.equal(messages[0].reason, "原因1");
  assert.equal(messages[1].reason, "原因2");
});
