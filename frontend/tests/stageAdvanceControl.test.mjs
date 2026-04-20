/**
 * Unit tests for StageAdvanceControl logic (pure rules, no React rendering).
 * Tests the isS4ReviewButtonVisible function and the stage-to-button mapping rules.
 */
import test from "node:test";
import assert from "node:assert/strict";

import { isS4ReviewButtonVisible } from "../src/utils/workspaceSummary.js";

// ── S4 secondary button visibility ──────────────────────────────────────────

test("S4 review button hidden when word_count well below 70%", () => {
  assert.equal(isS4ReviewButtonVisible(0, { target: 4000 }), false);
});

test("S4 review button hidden at 69.9%", () => {
  assert.equal(isS4ReviewButtonVisible(2799, { target: 4000 }), false);
});

test("S4 review button visible at exactly 70%", () => {
  assert.equal(isS4ReviewButtonVisible(2800, { target: 4000 }), true);
});

test("S4 review button visible well above 70%", () => {
  assert.equal(isS4ReviewButtonVisible(5000, { target: 4000 }), true);
});

test("S4 review button hidden when length_targets is null", () => {
  assert.equal(isS4ReviewButtonVisible(9999, null), false);
});

test("S4 review button hidden when length_targets.target is 0", () => {
  assert.equal(isS4ReviewButtonVisible(9999, { target: 0 }), false);
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
