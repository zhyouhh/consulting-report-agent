/**
 * Unit tests for StageAdvanceControl logic (pure rules, no React rendering).
 * Tests the isS4ReviewButtonVisible function and the stage-to-button mapping rules.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

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

// ── R5: StageAdvanceControl S1 hint source-guard ────────────────────────────

test("StageAdvanceControl S1 hint uses s1ConfirmDisabledReason (source guard)", () => {
  const src = readFileSync(
    new URL("../src/components/StageAdvanceControl.jsx", import.meta.url),
    "utf8",
  );
  assert.match(src, /s1ConfirmDisabledReason/);
  // disabledReason 必须被条件渲染用到，不只 import（防止 import 后从不使用）
  assert.match(src, /\{disabledReason\}/);
});

// ── 2026-07-06 反馈①：S1/S7 代发自愈 source-guard ────────────────────────────
// S1「确认大纲」/ S7「归档」不再直连 checkpoint API（无模型在环、撞门禁即 400 死路），
// 改为代用户发确认消息走主模型自愈。S4/S5 保持直连（内容阈值 / 独立审查报告，代发救不了）。

test("S1/S7 代发走主模型，不再直连 checkpoint POST（source guard）", () => {
  const src = readFileSync(
    new URL("../src/components/StageAdvanceControl.jsx", import.meta.url),
    "utf8",
  );
  assert.doesNotMatch(src, /postCheckpoint\('outline-confirmed'\)/);
  assert.doesNotMatch(src, /postCheckpoint\('delivery-archived'\)/);
  assert.match(src, /sendConfirmMessage\('我确认当前大纲/);
  assert.match(src, /sendConfirmMessage\('我确认报告已交付/);
  // 忙时必须给用户反馈，不静默丢弃
  assert.match(src, /const ok = onSendPrompt\?\.\(text\) \?\? false/);
  assert.match(src, /if \(!ok\) showError\(/);
  // S4/S5 保持直连
  assert.match(src, /postCheckpoint\('review-started'\)/);
  assert.match(src, /postCheckpoint\('review-passed'\)/);
});

test("ChatPanel 暴露 sendUserMessage 且忙时返回 false（source guard）", () => {
  const src = readFileSync(
    new URL("../src/components/ChatPanel.jsx", import.meta.url),
    "utf8",
  );
  assert.match(src, /useImperativeHandle\(ref, \(\) => \(\{ triggerSystemTurn, dropPendingReviewTriggers, sendUserMessage \}\)/);
  // 忙态守卫：loading/uploading 时不发、返回 false
  assert.match(src, /if \(!trimmed \|\| loading \|\| uploading\) return false/);
  // 代发渲染用户气泡（与打字确认同路径）
  assert.match(src, /startStream\(\{ messageText: trimmed, renderUserBubble: true \}\)/);
});

test("App/MobileShell 把 onSendPrompt 接到 ChatPanel.sendUserMessage（source guard）", () => {
  const appSrc = readFileSync(new URL("../src/App.jsx", import.meta.url), "utf8");
  assert.match(appSrc, /onSendPrompt=\{\(text\) => chatPanelRef\.current\?\.sendUserMessage\(text\) \?\? false\}/);
  const mobileSrc = readFileSync(new URL("../src/components/MobileShell.jsx", import.meta.url), "utf8");
  assert.match(mobileSrc, /onSendPrompt=\{handleSendPrompt\}/);
  // 移动端代发成功后关右抽屉（动作后关抽屉铁律）
  assert.match(mobileSrc, /const ok = chatPanelRef\.current\?\.sendUserMessage\(text\) \?\? false\s*\n\s*if \(ok\) closeAll\(\)/);
});
