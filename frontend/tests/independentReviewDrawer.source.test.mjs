import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const drawerSrc = () => readFileSync(path.join(__dirname, "../src/components/IndependentReviewDrawer.jsx"), "utf-8");
const workspaceSrc = () => readFileSync(path.join(__dirname, "../src/components/WorkspacePanel.jsx"), "utf-8");
const chatPanelSrc = () => readFileSync(path.join(__dirname, "../src/components/ChatPanel.jsx"), "utf-8");

function sectionBetween(src, start, end) {
  const startIndex = src.indexOf(start);
  assert.notEqual(startIndex, -1, `missing section start: ${start}`);
  const endIndex = src.indexOf(end, startIndex);
  assert.notEqual(endIndex, -1, `missing section end: ${end}`);
  return src.slice(startIndex, endIndex);
}

test("ReviewChatWindow uses AbortController for fetch lifecycle", () => {
  const src = drawerSrc();
  assert.match(src, /new AbortController\(\)/);
  assert.match(src, /\.abort\(\)/);
});

test("ReviewChatWindow listens for ESC keydown", () => {
  const src = drawerSrc();
  assert.match(src, /keydown/);
  assert.match(src, /key === ['"]Escape['"]/);
});

test("ReviewChatWindow has a real close button (not ESC-only)", () => {
  const src = drawerSrc();
  // A clickable close control wired to the active-close handler.
  assert.match(src, /aria-label=\{isMobile \? "停止审查" : "关闭"\}/);
  assert.match(src, /onClick=\{handleActiveClose\}/);
});

test("ReviewChatWindow header is draggable", () => {
  const src = drawerSrc();
  assert.match(src, /onMouseDown=\{isMobile \? undefined : handleDragStart\}/);
  assert.match(src, /cursor-move/);
});

test("ReviewChatWindow shows round/action progress", () => {
  const src = drawerSrc();
  assert.match(src, /第 \{windowState\.round\} 轮/);
});

test("ReviewChatWindow drives the stream over POST (no residual GET to the review stream)", () => {
  const src = drawerSrc();
  assert.match(src, /independent-review\/stream/);
  assert.match(src, /method: 'POST'/);
  // The legacy GET endpoint was deleted in C5 — the window must not call it.
  assert.doesNotMatch(src, /method: 'GET'/);
});

test("ReviewChatWindow generates a stable run_id and sends it in the body", () => {
  const src = drawerSrc();
  assert.match(src, /genRunId\(\)/);
  assert.match(src, /run_id: runId/);
});

test("ReviewChatWindow normalizes non-OK error detail to a string before rendering", () => {
  const src = drawerSrc();
  // windowState.error is rendered directly (错误：{windowState.error}); a FastAPI 422 detail is an
  // array → rendering it would crash the window ("Objects are not valid as a React child"). The
  // non-OK branch must route detail through normalizeApiErrorDetail, never pass raw detail.detail.
  assert.match(src, /import \{ normalizeApiErrorDetail \} from ['"]\.\.\/utils\/authError['"]/);
  assert.match(src, /normalizeApiErrorDetail\(\s*body\?\.detail/);
  assert.doesNotMatch(src, /message: detail\.detail/);
});

test("ReviewChatWindow error state persists (no auto-close setTimeout)", () => {
  const src = drawerSrc();
  // The old behaviour auto-closed the window 3s after an error; that is removed.
  assert.doesNotMatch(src, /setTimeout\([^)]*onClose[^)]*3000/);
  assert.doesNotMatch(src, /setTimeout\([^)]*onClose[^)]*\b3000\b/);
});

test("ReviewChatWindow completed path auto-closes WITHOUT calling /discard", () => {
  const src = drawerSrc();
  const completedBlock = sectionBetween(src, "if (event.type === 'review-completed')", "const runWithBackoff");
  assert.match(completedBlock, /onCompletedRef\.current\?\.\(/);
  assert.match(completedBlock, /onCloseRef\.current\?\.\(\)/);
  // No discard endpoint CALL inside the completion handling / consumeStream tail (comments may
  // mention discard; assert there is no actual fetch to the discard endpoint here).
  assert.doesNotMatch(completedBlock, /independent-review\/discard/);
  assert.doesNotMatch(completedBlock, /fetch\([^)]*discard/);
});

test("ReviewChatWindow active close aborts + calls /discard", () => {
  const src = drawerSrc();
  const closeBlock = sectionBetween(src, "const handleActiveClose", "// 继续审查");
  assert.match(closeBlock, /\.abort\(\)/);
  assert.match(closeBlock, /independent-review\/discard/);
  assert.match(closeBlock, /method: 'POST'/);
  // Guarded so a completed run is never discarded (would clear the done tombstone).
  assert.match(closeBlock, /!completedRef\.current/);
});

test("ReviewChatWindow has exponential 409 backoff with a cap and an exit", () => {
  const src = drawerSrc();
  assert.match(src, /backoffExhausted/);
  assert.match(src, /nextBackoff/);
  assert.match(src, /status === 409/);
});

test("ReviewChatWindow surfaces a RESUMABLE error when the stream ends without completion", () => {
  const src = drawerSrc();
  // A stream that EOFs (disconnect) or [DONE]s WITHOUT review-completed/error must NOT be treated
  // as a silent success — that would strand the window in 'running' with no resume path (the exact
  // R1 failure: "断连后活全丢、无法从断处继续"). consumeStream tracks whether a terminal event was
  // seen and dispatches a resumable error otherwise.
  assert.match(src, /let sawError = false/);
  assert.match(src, /!completedRef\.current && !sawError/);
  assert.match(src, /可继续审查/);
  // [DONE] no longer short-circuits to a bare `return true`; it falls through to the guard.
  assert.doesNotMatch(src, /\[DONE\]'\) return true/);
});

test("ReviewChatWindow errored panel has a supplement input wired into resume", () => {
  const src = drawerSrc();
  // plan Task 5.2: errored unlocks an input box; 继续审查 carries the typed supplement into the
  // resume stream so the agent picks up with accumulated context + the user's correction.
  assert.match(src, /<textarea/);
  assert.match(src, /supplementInput/);
  assert.match(src, /setSupplementInput/);
  assert.match(src, /runWithBackoff\(\{ resume: true, supplement \}\)/);
});

test("ReviewChatWindow only starts a review on the isOpen rising edge (no restart on project switch)", () => {
  const src = drawerSrc();
  // red-team B1: WorkspacePanel closes the window on project switch via an async effect, so for one
  // render the window sees the NEW projectId with isOpen still true. The open-effect must NOT fire a
  // fresh review against the new project — it guards on the rising edge via openedRef.
  assert.match(src, /openedRef/);
  assert.match(src, /const justOpened = isOpen && !openedRef\.current/);
  assert.match(src, /if \(!justOpened\) return/);
});

test("WorkspacePanel completion fires the system turn from the run-bound result, not generic ready", () => {
  const src = workspaceSrc();
  const reviewCompletion = sectionBetween(src, "const onIndependentReviewCompleted", "const handleSaveFile");
  // Fires on the {run_id, report_mtime_ns} returned by the window.
  assert.match(reviewCompletion, /completion\?\.run_id/);
  assert.match(reviewCompletion, /completion\?\.report_mtime_ns/);
  assert.match(reviewCompletion, /onTriggerSystemTurn\?\.\(['"]independent_review_done['"],\s*\{/);
  // Must NOT gate completion on the generic workspace flag (would risk reporting a stale report).
  assert.doesNotMatch(reviewCompletion, /flags\?\.independent_review_ready/);
  // Project-switch guard preserved.
  assert.match(reviewCompletion, /shouldApplyProjectResponse/);
});

test("WorkspacePanel does not strip run-bound metadata before triggering", () => {
  const src = workspaceSrc();
  const reviewCompletion = sectionBetween(src, "const onIndependentReviewCompleted", "const handleSaveFile");
  // The metadata object reaches onTriggerSystemTurn intact (run_id + report_mtime_ns).
  assert.match(reviewCompletion, /run_id:\s*runId/);
  assert.match(reviewCompletion, /report_mtime_ns:\s*reportMtimeNs/);
});

test("WorkspacePanel no longer references the removed lint-report path (N7)", () => {
  const src = workspaceSrc();
  assert.doesNotMatch(src, /runLintReport/);
  assert.doesNotMatch(src, /lint-report/);
  assert.doesNotMatch(src, /lint_report_done|lintReportReady|lint_report_ready/);
});

test("WorkspacePanel drops stale pending review triggers when starting a new run (red-team B2)", () => {
  const src = workspaceSrc();
  const runReview = sectionBetween(src, "const runIndependentReview", "const handleCloseReviewDrawer");
  // Starting a new run must prune the older same-type pending BEFORE claim_first overwrites the
  // store tombstone, else the stale pending flush is run-bound-rejected.
  assert.match(runReview, /onDropPendingReviewTriggers\?\.\(['"]independent_review_done['"]\)/);
});

test("ChatPanel exposes dropPendingReviewTriggers and prunes via dropPendingTriggersByType (B2)", () => {
  const src = chatPanelSrc();
  assert.match(src, /dropPendingTriggersByType\(/);
  // exposed on the imperative handle so WorkspacePanel can prune at run-start.
  assert.match(src, /\{ triggerSystemTurn, dropPendingReviewTriggers \}/);
});

test("ReviewChatWindow isMobile (default false): portal fullscreen, no drag, stop-label", () => {
  const s = readFileSync(path.join(__dirname, "../src/components/IndependentReviewDrawer.jsx"), "utf-8");
  assert.match(s, /import \{ createPortal \} from ['"]react-dom['"]/);
  assert.match(s, /isMobile\s*=\s*false/);
  // 移动端经 createPortal 挂 document.body（脱离抽屉子树）
  assert.match(s, /isMobile\s*\?\s*createPortal\([\s\S]*?document\.body\)/);
  // 移动端外层全屏 class + style（fullscreen 锁）
  assert.match(s, /fixed inset-0 w-full/);
  assert.match(s, /height: '100dvh'/);
  // 锚定按钮子节点（而非 title/aria-label）：改回 × 必须 FAIL
  assert.match(s, />\s*\{isMobile \? '停止审查' : '×'\}\s*<\/button>/);
  // 移动端不绑拖动
  assert.match(s, /isMobile\s*\?\s*undefined\s*:\s*handleDragStart/);
});
