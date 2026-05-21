import test from "node:test";
import assert from "node:assert/strict";

import { getStageButtonState, shouldShowButton } from "../src/utils/stagePanelButtons.js";

test("shouldShowButton: independent_review hidden in S0-S4", () => {
  for (const stageCode of ["S0", "S1", "S2", "S3", "S4"]) {
    assert.equal(shouldShowButton("independent_review", stageCode), false);
  }
});

test("shouldShowButton: independent_review visible in S5", () => {
  assert.equal(shouldShowButton("independent_review", "S5"), true);
});

test("shouldShowButton: lint_report visible in S5", () => {
  assert.equal(shouldShowButton("lint_report", "S5"), true);
});

test("shouldShowButton: export hidden until S6", () => {
  for (const stageCode of ["S0", "S1", "S2", "S3", "S4", "S5"]) {
    assert.equal(shouldShowButton("export_draft", stageCode, { report_ready: true }), false);
  }
});

test("shouldShowButton: export visible in S6/S7/done", () => {
  for (const stageCode of ["S6", "S7", "done"]) {
    assert.equal(shouldShowButton("export_draft", stageCode, { report_ready: true }), true);
  }
});

test("shouldShowButton: buttons should highlight when not ready", () => {
  assert.equal(getStageButtonState("independent_review", "S5", { independentReviewReady: false }).highlighted, true);
  assert.equal(getStageButtonState("lint_report", "S5", { lintReportReady: false }).highlighted, true);
  assert.equal(getStageButtonState("independent_review", "S5", { independentReviewReady: true }).highlighted, false);
});

test("shouldShowButton: buttons disabled when running", () => {
  assert.equal(getStageButtonState("independent_review", "S5", {}, { reviewRunning: true }).disabled, true);
  assert.equal(getStageButtonState("lint_report", "S5", {}, { lintRunning: true }).disabled, true);
});
