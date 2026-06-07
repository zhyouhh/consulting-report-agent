import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

import { buildChatRequest } from "../src/utils/chatMaterials.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const readSrc = (relativePath) => readFileSync(path.join(__dirname, relativePath), "utf-8");

test("buildChatStreamRequest passes system_trigger in body", () => {
  const req = buildChatRequest({
    projectId: "demo",
    messageText: "",
    systemTrigger: "lint_report_done",
  });

  assert.equal(req.system_trigger, "lint_report_done");
});

test("buildChatStreamRequest empty messageText when system_trigger set", () => {
  const req = buildChatRequest({
    projectId: "demo",
    messageText: "",
    systemTrigger: "independent_review_done",
  });

  assert.equal(req.message_text, "");
});

test("ChatPanel.jsx uses forwardRef and exposes triggerSystemTurn", () => {
  const src = readSrc("../src/components/ChatPanel.jsx");
  assert.match(src, /forwardRef/);
  assert.match(src, /useImperativeHandle/);
  assert.match(src, /triggerSystemTurn/);
});

test("App.jsx wires chatPanelRef.triggerSystemTurn into WorkspacePanel via onTriggerSystemTurn prop", () => {
  const src = readSrc("../src/App.jsx");
  assert.match(src, /chatPanelRef/);
  assert.match(src, /ref=\{chatPanelRef\}/);
  // C5: the wiring forwards run-bound metadata as a second argument.
  assert.match(
    src,
    /onTriggerSystemTurn=\{\(triggerType,\s*metadata\) => chatPanelRef\.current\?\.triggerSystemTurn\(triggerType,\s*metadata\)\}/,
  );
});

test("ChatPanel.triggerSystemTurn queues triggers when busy and forwards metadata", () => {
  const src = readSrc("../src/components/ChatPanel.jsx");
  // triggerSystemTurn takes metadata and enqueues when loading/uploading instead of dropping.
  assert.match(src, /const triggerSystemTurn = useCallback\(\(triggerType, metadata = null\)/);
  assert.match(src, /enqueuePendingTrigger/);
  assert.match(src, /flushNextPendingTrigger/);
  // The flushed trigger re-sends the original run-bound metadata.
  assert.match(src, /triggerMetadata: \{ run_id: item\.run_id, report_mtime_ns: item\.report_mtime_ns \}/);
});

test("WorkspacePanel.runLintReport awaits workspace GET before onTriggerSystemTurn", () => {
  const src = readSrc("../src/components/WorkspacePanel.jsx");
  assert.match(src, /axios\.post\(`\/api\/projects\/\$\{encodeURIComponent\(requestProject\)\}\/lint-report`\)/);
  assert.match(src, /axios\.get\(`\/api\/projects\/\$\{encodeURIComponent\(requestProject\)\}\/workspace`\)/);
  assert.match(src, /lint_report_ready|lintReportReady/);
  assert.match(src, /onTriggerSystemTurn\?\.\(['"]lint_report_done['"]\)/);
});

test("ReviewChatWindow onCompleted fires the system turn from the run-bound result", () => {
  const src = readSrc("../src/components/WorkspacePanel.jsx");
  assert.match(src, /onIndependentReviewCompleted/);
  // C5: completion is judged by the {run_id, report_mtime_ns} the window returns, not by a
  // generic workspace independent_review_ready flag (which could reflect a stale report).
  assert.match(src, /onTriggerSystemTurn\?\.\(['"]independent_review_done['"],\s*\{ run_id: runId, report_mtime_ns: reportMtimeNs \}\)/);
});
