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
  assert.match(src, /import \{ applyAttachmentTranscribed \} from '\.\.\/utils\/sseEvents'/);
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
