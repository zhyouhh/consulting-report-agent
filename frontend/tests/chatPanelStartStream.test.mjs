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
  assert.match(src, /onTriggerSystemTurn=\{\(triggerType\) => chatPanelRef\.current\?\.triggerSystemTurn\(triggerType\)\}/);
});

test("WorkspacePanel.runLintReport awaits workspace GET before onTriggerSystemTurn", () => {
  const src = readSrc("../src/components/WorkspacePanel.jsx");
  assert.match(src, /axios\.post\(`\/api\/projects\/\$\{encodeURIComponent\(requestProject\)\}\/lint-report`\)/);
  assert.match(src, /axios\.get\(`\/api\/projects\/\$\{encodeURIComponent\(requestProject\)\}\/workspace`\)/);
  assert.match(src, /lint_report_ready|lintReportReady/);
  assert.match(src, /onTriggerSystemTurn\?\.\(['"]lint_report_done['"]\)/);
});

test("IndependentReviewDrawer onCompleted awaits workspace independent_review_ready", () => {
  const src = readSrc("../src/components/WorkspacePanel.jsx");
  assert.match(src, /onIndependentReviewCompleted/);
  assert.match(src, /independent_review_ready|independentReviewReady/);
  assert.match(src, /onTriggerSystemTurn\?\.\(['"]independent_review_done['"]\)/);
});
