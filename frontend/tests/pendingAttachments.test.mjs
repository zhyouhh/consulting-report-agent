import test from "node:test";
import assert from "node:assert/strict";

import {
  buildPendingAttachment,
  fileToDataUrl,
  mergePendingAttachments,
  removePendingAttachment,
  splitPendingAttachments,
} from "../src/utils/pendingAttachments.js";

test("buildPendingAttachment marks images as ephemeral", () => {
  const file = new File(["img"], "bug.png", { type: "image/png" });
  const item = buildPendingAttachment(file);

  assert.equal(item.displayName, "bug.png");
  assert.equal(item.kind, "image");
  assert.equal(item.deliveryMode, "ephemeral");
  assert.equal(item.previewUrl, null);
});

test("buildPendingAttachment infers image mime type from file extension when type is empty", () => {
  const file = new File(["img"], "bug.png", { type: "" });
  const item = buildPendingAttachment(file);

  assert.equal(item.kind, "image");
  assert.equal(item.mimeType, "image/png");
});

test("buildPendingAttachment marks documents as persist", () => {
  const file = new File(["doc"], "memo.docx", {
    type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  });
  const item = buildPendingAttachment(file);

  assert.equal(item.kind, "document");
  assert.equal(item.deliveryMode, "persist");
});

test("mergePendingAttachments appends only new ids", () => {
  const existing = [{ id: "p1", displayName: "bug.png" }];
  const incoming = [
    { id: "p1", displayName: "bug.png" },
    { id: "p2", displayName: "memo.docx" },
  ];

  assert.deepEqual(mergePendingAttachments(existing, incoming), [
    { id: "p1", displayName: "bug.png" },
    { id: "p2", displayName: "memo.docx" },
  ]);
});

test("removePendingAttachment removes the matching item", () => {
  assert.deepEqual(
    removePendingAttachment(
      [
        { id: "p1", displayName: "bug.png" },
        { id: "p2", displayName: "memo.docx" },
      ],
      "p1",
    ),
    [{ id: "p2", displayName: "memo.docx" }],
  );
});

test("splitPendingAttachments separates transient images from persistent documents", () => {
  const bug = { id: "p1", kind: "image", deliveryMode: "ephemeral" };
  const memo = { id: "p2", kind: "document", deliveryMode: "persist" };

  assert.deepEqual(splitPendingAttachments([bug, memo]), {
    transientImages: [bug],
    persistentDocuments: [memo],
  });
});

test("fileToDataUrl converts image file into a data URL", async () => {
  const file = new File(["hi"], "bug.png", { type: "image/png" });

  const dataUrl = await fileToDataUrl(file);

  assert.equal(dataUrl, "data:image/png;base64,aGk=");
});

test("fileToDataUrl uses inferred image mime type when file.type is empty", async () => {
  const file = new File(["hi"], "bug.png", { type: "" });

  const dataUrl = await fileToDataUrl(file);

  assert.equal(dataUrl, "data:image/png;base64,aGk=");
});
