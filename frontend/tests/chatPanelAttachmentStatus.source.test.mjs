import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

// No jsdom in this repo: the ChatPanel render/handler wiring for N6 D2 (attachment_transcribed
// SSE + conversion-status chip) is guarded here by asserting the load-bearing strings exist.
// The real logic lives in tested pure functions (sseEvents.js / chatMaterials.js).
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const chatPanelSrc = () =>
  readFileSync(path.join(__dirname, "../src/components/ChatPanel.jsx"), "utf-8");

test("ChatPanel handles the attachment_transcribed SSE event via applyAttachmentTranscribed", () => {
  const src = chatPanelSrc();
  assert.match(src, /parsed\.type === 'attachment_transcribed'/);
  assert.match(src, /applyAttachmentTranscribed\(prev, parsed\.data\)/);
  assert.match(src, /import \{ applyAttachmentTranscribed.*\} from '\.\.\/utils\/sseEvents'/);
});

test("ChatPanel threads clientMessageId into buildChatRequest", () => {
  const src = chatPanelSrc();
  assert.match(src, /const clientMessageId =/);
  assert.match(src, /clientMessageId,/);
});

test("ChatPanel renders the transcribed indicator + transient failure note on the bubble", () => {
  const src = chatPanelSrc();
  assert.match(src, /msg\.transientAttachments\?\.length > 0/);
  assert.match(src, /已转写图片/);
  assert.match(src, /图片没读出来/);
  assert.match(src, /transcriptionStatus === 'failed'/);
});

test("ChatPanel renders a conversion-status chip on the materials list", () => {
  const src = chatPanelSrc();
  assert.match(src, /conversionStatusChip\(material\)/);
  assert.match(src, /statusChip\.label/);
});

test("ChatPanel renders a muted chip for the not_parsed tone", () => {
  // N6 Fix1: not_parsed gets its own muted style branch (informative, not alarming).
  const src = chatPanelSrc();
  assert.match(src, /statusChip\.tone === 'not_parsed'/);
});

test("ChatPanel rebuilds history attachment_transcripts indicators on reload", () => {
  // N6 Fix2: reloaded chats must re-show 已转写图片 / 没读出来 from persisted attachment_transcripts.
  const src = chatPanelSrc();
  assert.match(src, /historyTranscriptIndicators/);
  assert.match(src, /import \{ applyAttachmentTranscribed, historyTranscriptIndicators \} from '\.\.\/utils\/sseEvents'/);
  assert.match(src, /historyTranscripts:/);
  assert.match(src, /msg\.historyTranscripts\?\.length > 0/);
});
