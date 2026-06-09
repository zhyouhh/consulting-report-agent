import test from "node:test";
import assert from "node:assert/strict";

import {
  initialEditState, enterEdit, editDraft, cancelEdit, startSaving,
  saveSucceeded, saveFailed, reloadAfterConflict, guardLeave,
} from "../src/utils/fileEditState.js";

test("initial state is clean preview", () => {
  const s = initialEditState();
  assert.equal(s.mode, "preview");
  assert.equal(s.dirty, false);
  assert.equal(s.saving, false);
});

test("enterEdit loads content + base mtime in edit mode, clean", () => {
  const s = enterEdit(initialEditState(), { content: "正文", mtimeNs: "123" });
  assert.equal(s.mode, "edit");
  assert.equal(s.draft, "正文");
  assert.equal(s.baseMtimeNs, "123");
  assert.equal(s.dirty, false);
});

test("editDraft marks dirty and updates draft", () => {
  let s = enterEdit(initialEditState(), { content: "a", mtimeNs: "1" });
  s = editDraft(s, "a 改");
  assert.equal(s.draft, "a 改");
  assert.equal(s.dirty, true);
});

test("editDraft is a no-op outside edit mode", () => {
  const s = editDraft(initialEditState(), "x");
  assert.equal(s.mode, "preview");
});

test("cancelEdit returns clean preview", () => {
  let s = enterEdit(initialEditState(), { content: "a", mtimeNs: "1" });
  s = editDraft(s, "a 改");
  s = cancelEdit(s);
  assert.equal(s.mode, "preview");
  assert.equal(s.dirty, false);
});

test("save lifecycle: startSaving → saveSucceeded → clean preview", () => {
  let s = enterEdit(initialEditState(), { content: "a", mtimeNs: "1" });
  s = editDraft(s, "a 改");
  s = startSaving(s);
  assert.equal(s.saving, true);
  s = saveSucceeded(s, { mtimeNs: "2" });
  assert.equal(s.mode, "preview");
  assert.equal(s.dirty, false);
  assert.equal(s.saving, false);
});

test("saveFailed stays in edit, keeps draft + dirty, clears saving", () => {
  let s = enterEdit(initialEditState(), { content: "a", mtimeNs: "1" });
  s = editDraft(s, "a 改");
  s = startSaving(s);
  s = saveFailed(s);
  assert.equal(s.mode, "edit");
  assert.equal(s.draft, "a 改");
  assert.equal(s.dirty, true);
  assert.equal(s.saving, false);
});

test("reloadAfterConflict discards local edits, fresh base, clean edit", () => {
  let s = enterEdit(initialEditState(), { content: "a", mtimeNs: "1" });
  s = editDraft(s, "本地改动");
  s = reloadAfterConflict(s, { content: "服务端最新", mtimeNs: "9" });
  assert.equal(s.mode, "edit");
  assert.equal(s.draft, "服务端最新");
  assert.equal(s.baseMtimeNs, "9");
  assert.equal(s.dirty, false);
});

test("guardLeave: allow when preview/clean, confirm when dirty, block when saving", () => {
  assert.equal(guardLeave(initialEditState()), "allow");
  let s = enterEdit(initialEditState(), { content: "a", mtimeNs: "1" });
  assert.equal(guardLeave(s), "allow"); // edit 但未改
  s = editDraft(s, "改");
  assert.equal(guardLeave(s), "confirm");
  s = startSaving(s);
  assert.equal(guardLeave(s), "block");
});
