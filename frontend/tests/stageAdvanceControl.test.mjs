/**
 * Unit tests for StageAdvanceControl logic (pure rules, no React rendering).
 * Tests the isS4ReviewButtonVisible function and the stage-to-button mapping rules.
 */
import test from "node:test";
import assert from "node:assert/strict";

import { isS4ReviewButtonVisible } from "../src/utils/workspaceSummary.js";

// ── S4 secondary button visibility ──────────────────────────────────────────
// Uses backend-budgeted report_word_floor (= expected_length × 0.7).
// See backend/skill.py:287-293 for the source-of-truth schema.

test("S4 review button hidden when word_count well below floor", () => {
  assert.equal(isS4ReviewButtonVisible(0, { report_word_floor: 2800 }), false);
});

test("S4 review button hidden just below floor", () => {
  assert.equal(isS4ReviewButtonVisible(2799, { report_word_floor: 2800 }), false);
});

test("S4 review button visible at exactly the floor", () => {
  assert.equal(isS4ReviewButtonVisible(2800, { report_word_floor: 2800 }), true);
});

test("S4 review button visible well above the floor", () => {
  assert.equal(isS4ReviewButtonVisible(5000, { report_word_floor: 2800 }), true);
});

test("S4 review button hidden when length_targets is null", () => {
  assert.equal(isS4ReviewButtonVisible(9999, null), false);
});

test("S4 review button hidden when length_targets has only legacy `target` field", () => {
  // Regression guard against Task 7 field-name mismatch bug: backend returns
  // report_word_floor, not target — a stale .target read must not pass.
  assert.equal(isS4ReviewButtonVisible(9999, { target: 1000 }), false);
});

// ── Stage-to-button mapping rules (encoded as data, not rendering) ──────────

const BUTTON_RULES = {
  S0: "none",
  S1: "single",
  S2: "none",
  S3: "none",
  S4: "dual",
  S5: "dual",
  S6: "single",
  S7: "single",
};

function getButtonType(stageCode) {
  return BUTTON_RULES[stageCode] ?? "none";
}

test("S0 shows no button", () => {
  assert.equal(getButtonType("S0"), "none");
});

test("S1 shows single button", () => {
  assert.equal(getButtonType("S1"), "single");
});

test("S2 shows no button (auto-advance)", () => {
  assert.equal(getButtonType("S2"), "none");
});

test("S3 shows no button (auto-advance)", () => {
  assert.equal(getButtonType("S3"), "none");
});

test("S4 shows dual button", () => {
  assert.equal(getButtonType("S4"), "dual");
});

test("S5 shows dual button", () => {
  assert.equal(getButtonType("S5"), "dual");
});

test("S6 shows single button", () => {
  assert.equal(getButtonType("S6"), "single");
});

test("S7 shows single button", () => {
  assert.equal(getButtonType("S7"), "single");
});
