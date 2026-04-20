/**
 * Guard tests that raw stage codes (S0–S7, done) never leak into user-visible
 * JSX text nodes.
 *
 * Heuristic: scan StagePanel.jsx and ChatPanel.jsx for any text node that
 * interpolates '{stageCode}' or string-literal 'S\d' adjacent to Chinese text.
 * StageAdvanceControl uses stageCode for flow control (not user-visible), so
 * it's excluded.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));

function readSrc(relative) {
  return readFileSync(resolve(here, relative), "utf-8");
}

const stagePanelSrc = readSrc("../src/components/StagePanel.jsx");
const chatPanelSrc = readSrc("../src/components/ChatPanel.jsx");

test("StagePanel renders stageLabel, not raw stageCode, in the header", () => {
  // The header line must use the humanized label.
  assert.ok(stagePanelSrc.includes("{stageLabel}"));
  // Raw stageCode must NOT appear next to Chinese "当前阶段" text.
  assert.equal(
    /当前阶段\s*\{stageCode\}/.test(stagePanelSrc),
    false,
    "Do not interpolate raw stageCode in the 当前阶段 header"
  );
});

test("ChatPanel header renders stageLabel, not raw stageCode", () => {
  assert.ok(chatPanelSrc.includes("workspaceSummary.stageLabel"));
  assert.equal(
    /当前阶段\s*\{workspaceSummary\.stageCode\}/.test(chatPanelSrc),
    false
  );
});

test("StagePanel progress bar labels come from STAGE_NAMES via getStageName", () => {
  // We dropped the inline Chinese label arrays in favor of STAGE_NAMES.
  assert.ok(stagePanelSrc.includes("getStageName"));
  // Old hardcoded literals should no longer be present as JSX labels.
  assert.equal(stagePanelSrc.includes("'项目初始化'"), false);
  assert.equal(stagePanelSrc.includes("'报告撰写'"), false);
});
