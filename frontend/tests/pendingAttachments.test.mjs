import test from "node:test";
import assert from "node:assert/strict";

import {
  buildPendingAttachment,
  fileToDataUrl,
  mergePendingAttachments,
  MAX_IMAGES_PER_MESSAGE,
  capImageAttachments,
  removePendingAttachment,
  renamePastedFile,
  splitPendingAttachments,
} from "../src/utils/pendingAttachments.js";

test("buildPendingAttachment persists images into materials like documents", () => {
  const file = new File(["img"], "bug.png", { type: "image/png" });
  const item = buildPendingAttachment(file);

  assert.equal(item.displayName, "bug.png");
  assert.equal(item.kind, "image");
  assert.equal(item.deliveryMode, "persist");
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

test("splitPendingAttachments uploads persist images and documents, keeps legacy ephemeral images transient", () => {
  const legacy = { id: "p1", kind: "image", deliveryMode: "ephemeral" };
  const memo = { id: "p2", kind: "document", deliveryMode: "persist" };
  const shot = { id: "p3", kind: "image", deliveryMode: "persist" };

  assert.deepEqual(splitPendingAttachments([legacy, memo, shot]), {
    transientImages: [legacy],
    persistentUploads: [memo, shot],
  });
});

test("renamePastedFile gives generic clipboard images unique timestamped names", () => {
  const now = new Date(2026, 8, 18, 21, 5, 9);
  const first = renamePastedFile(new File(["a"], "image.png", { type: "image/png" }), 0, now);
  const second = renamePastedFile(new File(["b"], "image.png", { type: "image/png" }), 1, now);
  assert.equal(first.name, "粘贴图片-20260918-210509.png");
  assert.equal(second.name, "粘贴图片-20260918-210509-2.png");
  assert.equal(first.type, "image/png");
});

test("renamePastedFile keeps meaningful names and non-images untouched", () => {
  const named = new File(["a"], "市场规模图.png", { type: "image/png" });
  const doc = new File(["d"], "image.png.docx", { type: "application/msword" });
  assert.equal(renamePastedFile(named), named);
  assert.equal(renamePastedFile(doc), doc);
});

test("capImageAttachments limits images per message but not documents", () => {
  const existing = Array.from({ length: MAX_IMAGES_PER_MESSAGE - 1 }, (_, i) => ({ id: `p${i}`, kind: "image" }));
  const files = [
    new File(["a"], "a.png", { type: "image/png" }),
    new File(["b"], "b.png", { type: "image/png" }),
    new File(["d"], "memo.pdf", { type: "application/pdf" }),
  ];
  const { accepted, droppedImages } = capImageAttachments(existing, files);
  assert.deepEqual(accepted.map((f) => f.name), ["a.png", "memo.pdf"]);
  assert.equal(droppedImages, 1);
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
