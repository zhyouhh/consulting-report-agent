/**
 * Unit tests for RollbackMenu logic — stage-sensitive first-level option rules (§9.4).
 * No React rendering required.
 */
import test from "node:test";
import assert from "node:assert/strict";

import { getFirstLevelOption } from "../src/utils/rollbackMenuLogic.js";

test("S0 — not shown (caller checks HIDDEN_STAGES)", () => {
  // getFirstLevelOption is not called for S0; test for completeness
  const opt = getFirstLevelOption("S0");
  assert.equal(opt, null);
});

test("S1 — not shown (caller checks HIDDEN_STAGES)", () => {
  const opt = getFirstLevelOption("S1");
  assert.equal(opt, null);
});

test("S2 — first level is '调整大纲' with no checkpoint clear", () => {
  const opt = getFirstLevelOption("S2");
  assert.equal(opt.label, "调整大纲");
  assert.equal(opt.checkpoint, null);
});

test("S3 — first level is '调整大纲' with no checkpoint clear", () => {
  const opt = getFirstLevelOption("S3");
  assert.equal(opt.label, "调整大纲");
  assert.equal(opt.checkpoint, null);
});

test("S4 — first level is informational 'back to writing' with no checkpoint clear", () => {
  const opt = getFirstLevelOption("S4");
  assert.equal(opt.label, "回到继续改的状态");
  assert.equal(opt.checkpoint, null);
});

test("S5 — first level is null (secondary button handles it)", () => {
  const opt = getFirstLevelOption("S5");
  assert.equal(opt, null);
});

test("S6 — first level is '回到审查阶段', clears review-passed checkpoint", () => {
  const opt = getFirstLevelOption("S6");
  assert.equal(opt.label, "回到审查阶段");
  assert.equal(opt.checkpoint, "review-passed");
  assert.equal(opt.action, "clear");
});

test("S7 — first level is '回到审查阶段', clears review-passed checkpoint", () => {
  const opt = getFirstLevelOption("S7");
  assert.equal(opt.label, "回到审查阶段");
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
