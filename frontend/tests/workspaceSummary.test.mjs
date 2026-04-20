import test from "node:test";
import assert from "node:assert/strict";

import {
  summarizeWorkspace,
  isS4ReviewButtonVisible,
  isS1ConfirmOutlineEnabled,
} from "../src/utils/workspaceSummary.js";

test("summarizeWorkspace falls back safely when stage data is missing", () => {
  const summary = summarizeWorkspace({});
  assert.equal(summary.stageLabel, "未开始");
  assert.equal(summary.statusLabel, "待开始");
  assert.deepEqual(summary.completedItems, []);
  assert.deepEqual(summary.nextActions, []);
});

test("summarizeWorkspace falls back safely when api summary is null", () => {
  const summary = summarizeWorkspace(null);
  assert.equal(summary.stageLabel, "未开始");
  assert.equal(summary.statusLabel, "待开始");
  assert.deepEqual(summary.completedItems, []);
  assert.deepEqual(summary.nextActions, []);
});

test("summarizeWorkspace preserves api summary values", () => {
  const summary = summarizeWorkspace({
    stage_code: "S4",
    status: "进行中",
    completed_items: ["报告结构确定"],
    next_actions: ["图表制作完成"],
  });
  assert.equal(summary.stageLabel, "S4");
  assert.equal(summary.statusLabel, "进行中");
  assert.deepEqual(summary.completedItems, ["报告结构确定"]);
  assert.deepEqual(summary.nextActions, ["图表制作完成"]);
});

test("summarizeWorkspace maps new Task 7 fields from api response", () => {
  const summary = summarizeWorkspace({
    stage_code: "S4",
    next_stage_hint: "S7",
    flags: { outline_confirmed: true },
    checkpoints: { outline_confirmed_at: "2026-04-21T10:00:00Z" },
    word_count: 2500,
    length_targets: { target: 4000, minimum: 2800 },
    length_fallback_used: false,
    quality_progress: { label: "有效来源条目", current: 5, target: 8 },
    stalled_since: null,
    delivery_mode: "report_only",
  });
  assert.equal(summary.stageCode, "S4");
  assert.equal(summary.nextStageHint, "S7");
  assert.deepEqual(summary.flags, { outline_confirmed: true });
  assert.equal(summary.checkpoints.outline_confirmed_at, "2026-04-21T10:00:00Z");
  assert.equal(summary.wordCount, 2500);
  assert.deepEqual(summary.lengthTargets, { target: 4000, minimum: 2800 });
  assert.equal(summary.lengthFallbackUsed, false);
  assert.deepEqual(summary.qualityProgress, { label: "有效来源条目", current: 5, target: 8 });
  assert.equal(summary.stalledSince, null);
  assert.equal(summary.deliveryMode, "report_only");
});

test("summarizeWorkspace uses safe defaults when new fields are absent", () => {
  const summary = summarizeWorkspace({ stage_code: "S2" });
  assert.equal(summary.wordCount, 0);
  assert.equal(summary.lengthTargets, null);
  assert.equal(summary.lengthFallbackUsed, false);
  assert.equal(summary.qualityProgress, null);
  assert.equal(summary.stalledSince, null);
  assert.equal(summary.deliveryMode, "report_only");
  assert.equal(summary.nextStageHint, null);
  assert.deepEqual(summary.flags, {});
  assert.deepEqual(summary.checkpoints, {});
});

test("isS4ReviewButtonVisible returns false when no lengthTargets", () => {
  assert.equal(isS4ReviewButtonVisible(3000, null), false);
  assert.equal(isS4ReviewButtonVisible(3000, {}), false);
  assert.equal(isS4ReviewButtonVisible(3000, { target: 0 }), false);
});

test("isS4ReviewButtonVisible returns false when below 70% threshold", () => {
  // target=4000, threshold=2800; word_count=2799 → not visible
  assert.equal(isS4ReviewButtonVisible(2799, { target: 4000 }), false);
});

test("isS4ReviewButtonVisible returns true at exactly 70% threshold", () => {
  // target=4000, threshold=2800; word_count=2800 → visible
  assert.equal(isS4ReviewButtonVisible(2800, { target: 4000 }), true);
});

test("isS4ReviewButtonVisible returns true when above threshold", () => {
  assert.equal(isS4ReviewButtonVisible(3500, { target: 4000 }), true);
});

// ── isS1ConfirmOutlineEnabled ──────────────────────────────────────────────

test("isS1ConfirmOutlineEnabled: enabled when flags.outline_ready is true", () => {
  const summary = summarizeWorkspace({
    stage_code: "S1",
    flags: { outline_ready: true },
  });
  assert.equal(isS1ConfirmOutlineEnabled(summary), true);
});

test("isS1ConfirmOutlineEnabled: disabled when flags.outline_ready is false", () => {
  const summary = summarizeWorkspace({
    stage_code: "S1",
    flags: { outline_ready: false },
  });
  assert.equal(isS1ConfirmOutlineEnabled(summary), false);
});

test("isS1ConfirmOutlineEnabled: disabled when flags.outline_ready is absent", () => {
  const summary = summarizeWorkspace({ stage_code: "S1", flags: {} });
  assert.equal(isS1ConfirmOutlineEnabled(summary), false);
});

test("isS1ConfirmOutlineEnabled: the legacy outline_exists field is NOT honored", () => {
  // Guards against regressions — backend field is outline_ready, not outline_exists
  const summary = summarizeWorkspace({
    stage_code: "S1",
    flags: { outline_exists: true },
  });
  assert.equal(isS1ConfirmOutlineEnabled(summary), false);
});

test("isS1ConfirmOutlineEnabled: checkpoints.outline_md_exists overrides when present", () => {
  const summary = summarizeWorkspace({
    stage_code: "S1",
    flags: { outline_ready: false },
    checkpoints: { outline_md_exists: true },
  });
  assert.equal(isS1ConfirmOutlineEnabled(summary), true);
});

test("isS1ConfirmOutlineEnabled: safe for empty summary", () => {
  assert.equal(isS1ConfirmOutlineEnabled({}), false);
  assert.equal(isS1ConfirmOutlineEnabled(), false);
});
