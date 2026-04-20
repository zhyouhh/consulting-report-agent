/**
 * Guard tests for Spec §6 "前端不自己从 delivery_mode 推 S6 vs S7".
 *
 * The S5 advance-button flow must rely on the backend-computed next_stage_hint
 * (or simply let the next workspace refresh pick up the new stage_code).
 * Frontend code MUST NOT read summary.deliveryMode and synthesize a stage jump.
 *
 * These tests read the source of StageAdvanceControl.jsx as text and assert the
 * invariant. This is intentionally a structural guard — node:test cannot render
 * React, but regressions are cheap enough to detect at the string level.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const controlSource = readFileSync(
  resolve(here, "../src/components/StageAdvanceControl.jsx"),
  "utf-8"
);

test("StageAdvanceControl does not inspect deliveryMode locally", () => {
  // Forbid reading deliveryMode from summary — §6 says backend's next_stage_hint
  // is the single source of truth for S6 vs S7 routing.
  assert.equal(
    controlSource.includes("summary.deliveryMode"),
    false,
    "StageAdvanceControl must not read summary.deliveryMode (Spec §6)"
  );
  // Also guard the deconstruction form.
  assert.equal(
    /const\s*\{[^}]*\bdeliveryMode\b[^}]*\}\s*=\s*summary/.test(controlSource),
    false,
    "StageAdvanceControl must not destructure deliveryMode from summary"
  );
});

test("StageAdvanceControl does not string-match Chinese delivery labels", () => {
  // These would indicate local branching on delivery mode which §6 forbids.
  assert.equal(controlSource.includes("'报告+演示'"), false);
  assert.equal(controlSource.includes("'仅报告'"), false);
  assert.equal(controlSource.includes("DELIVERY_MODE_REPORT_WITH_PRESENTATION"), false);
});

test("S5 review-passed POST does not branch on delivery mode", () => {
  // The S5 primary button must unconditionally POST review-passed and let
  // the backend redirect. The only correct use of nextStageHint would be
  // to display text, not to decide which checkpoint to call.
  const s5Section = controlSource.slice(
    controlSource.indexOf("stageCode === 'S5'"),
    controlSource.indexOf("stageCode === 'S6'")
  );
  assert.ok(s5Section.includes("'review-passed'"));
  assert.equal(s5Section.includes("deliveryMode"), false);
  assert.equal(s5Section.includes("'S6'"), false);
  assert.equal(s5Section.includes("'S7'"), false);
});

test("summarizeWorkspace still exposes nextStageHint for future consumers", async () => {
  // Even though no component currently reads it, the field is preserved in the
  // summary shape so any future UI piece (e.g. "下一步 XX" hint text) gets it
  // from the backend instead of recomputing.
  const { summarizeWorkspace } = await import("../src/utils/workspaceSummary.js");
  const summary = summarizeWorkspace({
    stage_code: "S5",
    next_stage_hint: "S6",
    delivery_mode: "报告+演示",
  });
  assert.equal(summary.nextStageHint, "S6");
});
