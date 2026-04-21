import test from "node:test";
import assert from "node:assert/strict";

import {
  summarizeWorkspace,
  isS4ReviewButtonVisible,
  isS1ConfirmOutlineEnabled,
  shouldShowPresentationStage,
  getStageName,
  STAGE_NAMES,
  DELIVERY_MODE_REPORT_ONLY,
  DELIVERY_MODE_REPORT_WITH_PRESENTATION,
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

test("summarizeWorkspace maps stage_code to human-readable Chinese label", () => {
  const summary = summarizeWorkspace({
    stage_code: "S4",
    status: "进行中",
    completed_items: ["报告结构确定"],
    next_actions: ["图表制作完成"],
  });
  // stageLabel is the user-facing name, never the raw stage code.
  assert.equal(summary.stageLabel, "撰写报告");
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
    // Real backend schema (backend/skill.py:287-293)
    length_targets: {
      expected_length: 4000,
      data_log_min: 6,
      analysis_refs_min: 4,
      report_word_floor: 2800,
      fallback_used: false,
    },
    length_fallback_used: false,
    quality_progress: { label: "有效来源条目", current: 5, target: 8 },
    stalled_since: null,
    delivery_mode: "仅报告",
  });
  assert.equal(summary.stageCode, "S4");
  assert.equal(summary.nextStageHint, "S7");
  assert.equal(summary.flags.outline_confirmed, true);
  assert.equal(summary.flags.s0InterviewDone, false);
  assert.equal(summary.checkpoints.outline_confirmed_at, "2026-04-21T10:00:00Z");
  assert.equal(summary.wordCount, 2500);
  assert.equal(summary.lengthTargets.expected_length, 4000);
  assert.equal(summary.lengthTargets.report_word_floor, 2800);
  assert.equal(summary.lengthFallbackUsed, false);
  assert.deepEqual(summary.qualityProgress, { label: "有效来源条目", current: 5, target: 8 });
  assert.equal(summary.stalledSince, null);
  assert.equal(summary.deliveryMode, "仅报告");
});

test("summarizeWorkspace uses safe defaults when new fields are absent", () => {
  const summary = summarizeWorkspace({ stage_code: "S2" });
  assert.equal(summary.wordCount, 0);
  assert.equal(summary.lengthTargets, null);
  assert.equal(summary.lengthFallbackUsed, false);
  assert.equal(summary.qualityProgress, null);
  assert.equal(summary.stalledSince, null);
  assert.equal(summary.deliveryMode, "仅报告");
  assert.equal(summary.nextStageHint, null);
  assert.equal(summary.flags.s0InterviewDone, false);
  assert.deepEqual(summary.checkpoints, {});
});

test("isS4ReviewButtonVisible returns false when no lengthTargets", () => {
  assert.equal(isS4ReviewButtonVisible(3000, null), false);
  assert.equal(isS4ReviewButtonVisible(3000, {}), false);
});

test("isS4ReviewButtonVisible returns false when below report_word_floor", () => {
  // floor=2800 (backend budget of expected_length=4000 × 0.7);
  // word_count=2799 → not visible
  assert.equal(isS4ReviewButtonVisible(2799, { report_word_floor: 2800 }), false);
});

test("isS4ReviewButtonVisible returns true at exactly report_word_floor", () => {
  assert.equal(isS4ReviewButtonVisible(2800, { report_word_floor: 2800 }), true);
});

test("isS4ReviewButtonVisible returns true when well above report_word_floor", () => {
  assert.equal(isS4ReviewButtonVisible(3500, { report_word_floor: 2800 }), true);
});

test("isS4ReviewButtonVisible returns false when report_word_floor is not a number", () => {
  // Regression guard: bad field names (e.g. legacy `target`) must not evaluate true
  assert.equal(isS4ReviewButtonVisible(9999, { target: 1000 }), false);
  assert.equal(isS4ReviewButtonVisible(9999, { report_word_floor: null }), false);
  assert.equal(isS4ReviewButtonVisible(9999, { report_word_floor: "2800" }), false);
});

test("isS4ReviewButtonVisible honors zero floor (unusual but valid)", () => {
  // A floor of 0 means "no minimum" — any nonnegative word_count passes.
  assert.equal(isS4ReviewButtonVisible(0, { report_word_floor: 0 }), true);
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

// ── delivery_mode constants must match backend/skill.py:1012-1022 ───────────

test("DELIVERY_MODE constants match backend Chinese literals", () => {
  assert.equal(DELIVERY_MODE_REPORT_ONLY, "仅报告");
  assert.equal(DELIVERY_MODE_REPORT_WITH_PRESENTATION, "报告+演示");
});

test("summarizeWorkspace preserves backend Chinese delivery_mode value", () => {
  const s1 = summarizeWorkspace({ delivery_mode: "报告+演示" });
  assert.equal(s1.deliveryMode, "报告+演示");
  const s2 = summarizeWorkspace({ delivery_mode: "仅报告" });
  assert.equal(s2.deliveryMode, "仅报告");
});

test("summarizeWorkspace defaults deliveryMode to 仅报告 (not english snake_case)", () => {
  const s = summarizeWorkspace({});
  assert.equal(s.deliveryMode, "仅报告");
  // Must NOT default to any english-ish placeholder
  assert.notEqual(s.deliveryMode, "report_only");
  assert.notEqual(s.deliveryMode, "report_and_presentation");
});

test("shouldShowPresentationStage: true only for 报告+演示", () => {
  assert.equal(shouldShowPresentationStage("报告+演示"), true);
  assert.equal(shouldShowPresentationStage("仅报告"), false);
  assert.equal(shouldShowPresentationStage("report_and_presentation"), false);
  assert.equal(shouldShowPresentationStage(""), false);
  assert.equal(shouldShowPresentationStage(null), false);
  assert.equal(shouldShowPresentationStage(undefined), false);
});

test("shouldShowPresentationStage gates S6 segment correctly for RED case", () => {
  // Regression guard: 报告+演示 project MUST show S6 (8-seg bar).
  // Previously compared to 'report_and_presentation' so this returned false,
  // dropping the S6 segment.
  const summary = summarizeWorkspace({
    stage_code: "S4",
    delivery_mode: "报告+演示",
  });
  assert.equal(shouldShowPresentationStage(summary.deliveryMode), true);
});

test("shouldShowPresentationStage gates S6 segment correctly for report-only", () => {
  // 7-seg bar: no S6 ghost.
  const summary = summarizeWorkspace({
    stage_code: "S4",
    delivery_mode: "仅报告",
  });
  assert.equal(shouldShowPresentationStage(summary.deliveryMode), false);
});

// ── Stage-code → human-readable label ──────────────────────────────────────

test("STAGE_NAMES covers every stage the backend can emit", () => {
  // Ensures no missing mapping would leak a raw code to the user
  for (const code of ["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "done"]) {
    assert.ok(STAGE_NAMES[code], `STAGE_NAMES missing entry for ${code}`);
    assert.ok(
      typeof STAGE_NAMES[code] === "string" && STAGE_NAMES[code].length > 0,
      `${code} label must be a non-empty string`
    );
  }
});

test("STAGE_NAMES labels never contain raw stage codes", () => {
  for (const [code, name] of Object.entries(STAGE_NAMES)) {
    assert.equal(name.includes("S0"), false, `${code} label should not contain 'S0'`);
    assert.equal(/\bS\d\b/.test(name), false, `${code} label should not contain raw 'Sx'`);
  }
});

test("getStageName resolves each stage to a human-readable Chinese name", () => {
  assert.equal(getStageName("S0"), "准备阶段");
  assert.equal(getStageName("S1"), "拟定大纲");
  assert.equal(getStageName("S2"), "收集资料");
  assert.equal(getStageName("S3"), "分析论证");
  assert.equal(getStageName("S4"), "撰写报告");
  assert.equal(getStageName("S5"), "质量审查");
  assert.equal(getStageName("S6"), "准备演示");
  assert.equal(getStageName("S7"), "等待归档");
  assert.equal(getStageName("done"), "已完成");
});

test("getStageName falls back to '未开始' for unknown / empty input", () => {
  assert.equal(getStageName(""), "未开始");
  assert.equal(getStageName(null), "未开始");
  assert.equal(getStageName(undefined), "未开始");
  assert.equal(getStageName("S99"), "未开始");
});

test("summarizeWorkspace.stageLabel is never a raw stage code", () => {
  // Hard guard: raw 'S4' must not leak to the UI via stageLabel
  for (const code of ["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "done"]) {
    const s = summarizeWorkspace({ stage_code: code });
    assert.notEqual(s.stageLabel, code, `stageLabel for ${code} must be humanized`);
  }
});

// ── s0InterviewDone camelCase flag ────────────────────────────────────────────

test("summarizeWorkspace surfaces s0InterviewDone from raw flags", () => {
  const raw = {
    stage_code: "S0",
    flags: {
      s0_interview_done: false,
      outline_ready: false,
      project_overview_ready: true,
    },
  };
  const summary = summarizeWorkspace(raw);
  assert.equal(summary.flags.s0InterviewDone, false);
});

test("summarizeWorkspace preserves raw flags (outline_ready, etc.)", () => {
  const raw = {
    stage_code: "S1",
    flags: {
      s0_interview_done: true,
      outline_ready: true,
      project_overview_ready: true,
      other_flag: "value",
    },
  };
  const summary = summarizeWorkspace(raw);
  assert.equal(summary.flags.outline_ready, true, "raw snake_case flag kept");
  assert.equal(summary.flags.other_flag, "value");
  assert.equal(summary.flags.s0InterviewDone, true, "camelCase added");
});

test("summarizeWorkspace with no flags field returns empty-ish flags", () => {
  const summary = summarizeWorkspace({ stage_code: "S0" });
  assert.equal(summary.flags.s0InterviewDone, false);
});
