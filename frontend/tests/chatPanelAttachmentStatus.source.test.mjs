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

test("ChatPanel no longer renders the always-on project materials list above the composer", () => {
  // Materials + their conversion status now live ONLY in the right-side 材料 tab (WorkspacePanel).
  // The composer must not re-list every project material (the clutter the user reported) nor
  // expose a manual material-attach toggle. Rationale for dropping "re-attach to this turn":
  // every material id is already in the per-turn system manifest, and a read material's text is
  // re-injected via the working-memory bypass (subject to compaction), so manual re-attach added
  // little for the text-only managed model — an accepted product tradeoff, not a memory guarantee.
  const src = chatPanelSrc();
  assert.doesNotMatch(src, /conversionStatusChip/);
  assert.doesNotMatch(src, /toggleMaterialSelection/);
  assert.doesNotMatch(src, /selectedMaterials\.map/);
});

test("ChatPanel still auto-attaches freshly uploaded documents to the turn (invariant preserved)", () => {
  // Removing the manual list must NOT break upload auto-attach: uploaded docs still merge into
  // selectedMaterialIds and ride the turn via attachedMaterialIds.
  const src = chatPanelSrc();
  assert.match(src, /mergeMaterialIds\(selectedMaterialIds, uploadedMaterials\)/);
  assert.match(src, /attachedMaterialIds: requestAttachedMaterialIds/);
});

test("ChatPanel rebuilds history attachment_transcripts indicators on reload", () => {
  // N6 Fix2: reloaded chats must re-show 已转写图片 / 没读出来 from persisted attachment_transcripts.
  const src = chatPanelSrc();
  assert.match(src, /historyTranscriptIndicators/);
  assert.match(src, /import \{ applyAttachmentTranscribed, historyTranscriptIndicators \} from '\.\.\/utils\/sseEvents'/);
  assert.match(src, /historyTranscripts:/);
  assert.match(src, /msg\.historyTranscripts\?\.length > 0/);
});
