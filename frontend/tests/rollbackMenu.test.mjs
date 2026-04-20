/**
 * Unit tests for RollbackMenu logic — stage-sensitive first-level option rules (§9.4).
 * No React rendering required.
 */
import test from "node:test";
import assert from "node:assert/strict";

import {
  getFirstLevelOption,
  OPTION_KIND_INSERT_PROMPT,
  OPTION_KIND_CLEAR_CHECKPOINT,
  OPTION_KIND_NOOP,
} from "../src/utils/rollbackMenuLogic.js";

test("S0 — not shown (caller checks HIDDEN_STAGES)", () => {
  // getFirstLevelOption is not called for S0; test for completeness
  const opt = getFirstLevelOption("S0");
  assert.equal(opt, null);
});

test("S1 — not shown (caller checks HIDDEN_STAGES)", () => {
  const opt = getFirstLevelOption("S1");
  assert.equal(opt, null);
});

test("S2 — '调整大纲' is an insertPrompt action, not a no-op", () => {
  const opt = getFirstLevelOption("S2");
  assert.equal(opt.label, "调整大纲");
  assert.equal(opt.kind, OPTION_KIND_INSERT_PROMPT);
  // Prompt must be a non-empty string — the click MUST do something visible.
  assert.ok(typeof opt.prompt === "string" && opt.prompt.length > 0);
  // No checkpoint clear is involved.
  assert.equal(opt.checkpoint, undefined);
});

test("S3 — '调整大纲' is an insertPrompt action, not a no-op", () => {
  const opt = getFirstLevelOption("S3");
  assert.equal(opt.label, "调整大纲");
  assert.equal(opt.kind, OPTION_KIND_INSERT_PROMPT);
  assert.ok(typeof opt.prompt === "string" && opt.prompt.length > 0);
});

test("S2/S3 '调整大纲' prompt is user-voice, not system-voice", () => {
  // Guards against AI-jargon or backstage terms leaking into the prompt
  const s2 = getFirstLevelOption("S2");
  const s3 = getFirstLevelOption("S3");
  for (const opt of [s2, s3]) {
    // Prompt is what the user will send — should NOT contain technical terms
    assert.ok(!opt.prompt.includes("outline.md"));
    assert.ok(!opt.prompt.includes("checkpoint"));
    assert.ok(!opt.prompt.includes("S2"));
    assert.ok(!opt.prompt.includes("S3"));
  }
});

test("S4 — first level is a no-op kind (informational only)", () => {
  const opt = getFirstLevelOption("S4");
  assert.equal(opt.label, "回到继续改的状态");
  assert.equal(opt.kind, OPTION_KIND_NOOP);
});

test("S5 — first level is null (secondary button handles it)", () => {
  const opt = getFirstLevelOption("S5");
  assert.equal(opt, null);
});

test("S6 — first level is clearCheckpoint kind with review-passed", () => {
  const opt = getFirstLevelOption("S6");
  assert.equal(opt.label, "回到审查阶段");
  assert.equal(opt.kind, OPTION_KIND_CLEAR_CHECKPOINT);
  assert.equal(opt.checkpoint, "review-passed");
  assert.equal(opt.action, "clear");
});

test("S7 — first level is clearCheckpoint kind with review-passed", () => {
  const opt = getFirstLevelOption("S7");
  assert.equal(opt.label, "回到审查阶段");
  assert.equal(opt.kind, OPTION_KIND_CLEAR_CHECKPOINT);
  assert.equal(opt.checkpoint, "review-passed");
  assert.equal(opt.action, "clear");
});

test("S6 confirm dialog uses non-technical wording", () => {
  const opt = getFirstLevelOption("S6");
  // Must not mention S4/S5/checkpoint/review_passed_at
  assert.ok(!opt.confirmTitle.includes("S4"));
  assert.ok(!opt.confirmTitle.includes("checkpoint"));
  assert.ok(!opt.confirmBody.includes("checkpoint"));
  assert.ok(!opt.confirmBody.includes("plan 文件"));
});

test("Option kinds are mutually distinct", () => {
  assert.notEqual(OPTION_KIND_INSERT_PROMPT, OPTION_KIND_CLEAR_CHECKPOINT);
  assert.notEqual(OPTION_KIND_INSERT_PROMPT, OPTION_KIND_NOOP);
  assert.notEqual(OPTION_KIND_CLEAR_CHECKPOINT, OPTION_KIND_NOOP);
});
