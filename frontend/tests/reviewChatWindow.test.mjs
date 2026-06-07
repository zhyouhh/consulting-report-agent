import test from "node:test";
import assert from "node:assert/strict";

import {
  aggregateContentDelta,
  backoffExhausted,
  genRunId,
  initialReviewWindowState,
  MAX_BACKOFF_ATTEMPTS,
  nextBackoff,
  parseDrawerEvent,
  reviewWindowReducer,
} from "../src/utils/independentReviewDrawer.js";
import {
  createPendingTriggerItem,
  dequeuePendingTrigger,
  dropPendingTriggersByType,
  enqueuePendingTrigger,
  scopePendingQueueToProject,
} from "../src/utils/pendingTriggerQueue.js";

// ---------------------------------------------------------------------------
// aggregateContentDelta
// ---------------------------------------------------------------------------

test("aggregateContentDelta merges consecutive content_delta into one assistant bubble", () => {
  let messages = [];
  messages = aggregateContentDelta(messages, { type: "content_delta", text: "第一" });
  messages = aggregateContentDelta(messages, { type: "content_delta", text: "段话" });
  assert.deepEqual(messages, [{ kind: "assistant", text: "第一段话" }]);
});

test("aggregateContentDelta closes the bubble on tool_call and starts a new one after", () => {
  let messages = [];
  messages = aggregateContentDelta(messages, { type: "content_delta", text: "分析中" });
  messages = aggregateContentDelta(messages, { type: "tool_call", tool: "read_file", args: { p: 1 } });
  messages = aggregateContentDelta(messages, { type: "content_delta", text: "继续" });
  assert.deepEqual(messages, [
    { kind: "assistant", text: "分析中" },
    { kind: "tool_call", tool: "read_file", args: { p: 1 } },
    { kind: "assistant", text: "继续" },
  ]);
});

test("aggregateContentDelta records tool_result cards", () => {
  const messages = aggregateContentDelta([], {
    type: "tool_result",
    tool: "read_file",
    status: "success",
    summary: "读取成功",
  });
  assert.deepEqual(messages, [
    { kind: "tool_result", tool: "read_file", status: "success", summary: "读取成功" },
  ]);
});

test("aggregateContentDelta ignores empty/unknown events", () => {
  assert.deepEqual(aggregateContentDelta([{ kind: "assistant", text: "x" }], { type: "content_delta", text: "" }), [
    { kind: "assistant", text: "x" },
  ]);
  assert.deepEqual(aggregateContentDelta([], { type: "usage" }), []);
});

// ---------------------------------------------------------------------------
// reviewWindowReducer
// ---------------------------------------------------------------------------

test("reviewWindowReducer running -> errored -> resume back to running", () => {
  let state = initialReviewWindowState();
  assert.equal(state.status, "running");
  state = reviewWindowReducer(state, { type: "error", message: "网络断开" });
  assert.equal(state.status, "errored");
  assert.equal(state.error, "网络断开");
  // resume-start clears the error and unlocks (running again).
  state = reviewWindowReducer(state, { type: "resume-start" });
  assert.equal(state.status, "running");
  assert.equal(state.error, null);
});

test("reviewWindowReducer errored state is sticky until an explicit resume", () => {
  let state = reviewWindowReducer(initialReviewWindowState(), { type: "error", message: "boom" });
  // Errored does NOT auto-dismiss: stray late events keep the window in errored with the error
  // visible (the user must click 继续审查 / 关闭). Only resume-start clears it.
  state = reviewWindowReducer(state, { type: "content_delta", text: "x" });
  assert.equal(state.status, "errored");
  assert.equal(state.error, "boom");
  state = reviewWindowReducer(state, { type: "progress", detail: "第 2 轮" });
  assert.equal(state.status, "errored");
});

test("reviewWindowReducer completed carries run_id + report_mtime_ns verbatim", () => {
  const big = "1760000000123456789";
  let state = initialReviewWindowState();
  state = reviewWindowReducer(state, { type: "review-completed", run_id: "run-9", report_mtime_ns: big });
  assert.equal(state.status, "completed");
  assert.equal(state.runId, "run-9");
  assert.equal(state.reportMtimeNs, big);
  assert.equal(typeof state.reportMtimeNs, "string");
});

test("reviewWindowReducer progress updates the round from 第 N 轮", () => {
  let state = initialReviewWindowState();
  state = reviewWindowReducer(state, { type: "progress", step: "thinking", detail: "第 3 轮" });
  assert.equal(state.round, 3);
  assert.equal(state.status, "running");
});

// ---------------------------------------------------------------------------
// genRunId
// ---------------------------------------------------------------------------

test("genRunId returns a non-empty unique string", () => {
  const a = genRunId();
  const b = genRunId();
  assert.equal(typeof a, "string");
  assert.ok(a.length > 0);
  assert.notEqual(a, b);
});

// ---------------------------------------------------------------------------
// 409 backoff
// ---------------------------------------------------------------------------

test("nextBackoff grows exponentially and is capped", () => {
  assert.equal(nextBackoff(0, { baseMs: 1000, capMs: 16000 }), 1000);
  assert.equal(nextBackoff(1, { baseMs: 1000, capMs: 16000 }), 2000);
  assert.equal(nextBackoff(2, { baseMs: 1000, capMs: 16000 }), 4000);
  // capped
  assert.equal(nextBackoff(10, { baseMs: 1000, capMs: 16000 }), 16000);
});

test("backoffExhausted stops after the cap", () => {
  assert.equal(backoffExhausted(0), false);
  assert.equal(backoffExhausted(MAX_BACKOFF_ATTEMPTS - 1), false);
  assert.equal(backoffExhausted(MAX_BACKOFF_ATTEMPTS), true);
  assert.equal(backoffExhausted(MAX_BACKOFF_ATTEMPTS + 1), true);
});

// ---------------------------------------------------------------------------
// parseDrawerEvent (content_delta + opaque mtime) — covered here too for cohesion
// ---------------------------------------------------------------------------

test("parseDrawerEvent passes report_mtime_ns through as a string", () => {
  const big = "1760000000123456789";
  const ev = parseDrawerEvent(`{"type":"review-completed","run_id":"r","report_mtime_ns":"${big}"}`);
  assert.equal(ev.report_mtime_ns, big);
  assert.equal(typeof ev.report_mtime_ns, "string");
});

// ---------------------------------------------------------------------------
// pendingTriggerQueue
// ---------------------------------------------------------------------------

test("pending queue holds multiple items FIFO", () => {
  let q = [];
  q = enqueuePendingTrigger(q, createPendingTriggerItem({ triggerType: "independent_review_done", runId: "r1", reportMtimeNs: "10", projectId: "p" }));
  q = enqueuePendingTrigger(q, createPendingTriggerItem({ triggerType: "lint_report_done", projectId: "p" }));
  assert.equal(q.length, 2);
  const first = dequeuePendingTrigger(q, "p");
  assert.equal(first.item.triggerType, "independent_review_done");
  assert.equal(first.item.run_id, "r1");
  const second = dequeuePendingTrigger(first.queue, "p");
  assert.equal(second.item.triggerType, "lint_report_done");
  const third = dequeuePendingTrigger(second.queue, "p");
  assert.equal(third.item, null);
});

test("pending queue preserves run-bound metadata as strings", () => {
  const big = "1760000000123456789";
  let q = enqueuePendingTrigger([], createPendingTriggerItem({
    triggerType: "independent_review_done",
    runId: "run-x",
    reportMtimeNs: big,
    projectId: "p",
  }));
  const { item } = dequeuePendingTrigger(q, "p");
  assert.equal(item.run_id, "run-x");
  assert.equal(item.report_mtime_ns, big);
  assert.equal(typeof item.report_mtime_ns, "string");
});

test("a new independent_review_done run discards an older pending one for the same project", () => {
  let q = [];
  q = enqueuePendingTrigger(q, createPendingTriggerItem({ triggerType: "independent_review_done", runId: "old", reportMtimeNs: "1", projectId: "p" }));
  q = enqueuePendingTrigger(q, createPendingTriggerItem({ triggerType: "independent_review_done", runId: "new", reportMtimeNs: "2", projectId: "p" }));
  assert.equal(q.length, 1);
  assert.equal(q[0].run_id, "new");
});

test("a new run for a DIFFERENT project does not discard the other project's pending", () => {
  let q = [];
  q = enqueuePendingTrigger(q, createPendingTriggerItem({ triggerType: "independent_review_done", runId: "a", reportMtimeNs: "1", projectId: "pa" }));
  q = enqueuePendingTrigger(q, createPendingTriggerItem({ triggerType: "independent_review_done", runId: "b", reportMtimeNs: "2", projectId: "pb" }));
  assert.equal(q.length, 2);
});

test("dequeue scopes to the active project (project switch discards cross-project items)", () => {
  let q = [];
  q = enqueuePendingTrigger(q, createPendingTriggerItem({ triggerType: "independent_review_done", runId: "a", reportMtimeNs: "1", projectId: "pa" }));
  q = enqueuePendingTrigger(q, createPendingTriggerItem({ triggerType: "lint_report_done", projectId: "pb" }));
  // Active project is pb: the pa item is skipped/discarded, the pb item is returned.
  const { item, queue } = dequeuePendingTrigger(q, "pb");
  assert.equal(item.triggerType, "lint_report_done");
  assert.equal(item.projectId, "pb");
  // The skipped pa item is not left lingering ahead of pb.
  assert.equal(queue.length, 0);
});

test("scopePendingQueueToProject drops items from other projects", () => {
  const q = [
    createPendingTriggerItem({ triggerType: "independent_review_done", runId: "a", reportMtimeNs: "1", projectId: "pa" }),
    createPendingTriggerItem({ triggerType: "lint_report_done", projectId: "pb" }),
  ];
  assert.deepEqual(scopePendingQueueToProject(q, "pa").map(i => i.projectId), ["pa"]);
});

test("dropPendingTriggersByType removes same-type same-project pending (new run supersedes; B2)", () => {
  let q = [];
  q = enqueuePendingTrigger(q, createPendingTriggerItem({ triggerType: "independent_review_done", runId: "old", reportMtimeNs: "1", projectId: "p" }));
  q = enqueuePendingTrigger(q, createPendingTriggerItem({ triggerType: "lint_report_done", projectId: "p" }));
  // Starting a new independent review drops the stale independent_review_done pending, keeps lint.
  const after = dropPendingTriggersByType(q, "independent_review_done", "p");
  assert.equal(after.length, 1);
  assert.equal(after[0].triggerType, "lint_report_done");
});

test("dropPendingTriggersByType only affects the given project (B2)", () => {
  const q = [
    createPendingTriggerItem({ triggerType: "independent_review_done", runId: "a", reportMtimeNs: "1", projectId: "pa" }),
    createPendingTriggerItem({ triggerType: "independent_review_done", runId: "b", reportMtimeNs: "2", projectId: "pb" }),
  ];
  // Starting a new review in pa must not touch pb's pending.
  const after = dropPendingTriggersByType(q, "independent_review_done", "pa");
  assert.deepEqual(after.map(i => i.projectId), ["pb"]);
});
