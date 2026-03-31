function newPendingAttachmentId() {
  if (globalThis.crypto?.randomUUID) {
    return globalThis.crypto.randomUUID();
  }
  return `pending-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function inferMimeTypeFromName(name = "") {
  const lowerName = name.toLowerCase();
  if (lowerName.endsWith(".png")) return "image/png";
  if (lowerName.endsWith(".jpg") || lowerName.endsWith(".jpeg")) return "image/jpeg";
  if (lowerName.endsWith(".webp")) return "image/webp";
  if (lowerName.endsWith(".gif")) return "image/gif";
  if (lowerName.endsWith(".bmp")) return "image/bmp";
  return "application/octet-stream";
}

function isImageFile(file) {
  if (!file) {
    return false;
  }
  const mimeType = (file.type || "").toLowerCase();
  if (mimeType.startsWith("image/")) {
    return true;
  }
  const lowerName = (file.name || "").toLowerCase();
  return [".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"].some((suffix) => lowerName.endsWith(suffix));
}

export function buildPendingAttachment(file) {
  const image = isImageFile(file);
  const mimeType = file?.type || inferMimeTypeFromName(file?.name || "");
  return {
    id: newPendingAttachmentId(),
    file,
    displayName: file?.name || "attachment",
    mimeType,
    kind: image ? "image" : "document",
    deliveryMode: image ? "ephemeral" : "persist",
    previewUrl: null,
  };
}

export function mergePendingAttachments(existing = [], incoming = []) {
  const merged = [...existing];
  const seenIds = new Set(existing.map((item) => item.id));

  for (const item of incoming) {
    if (!item?.id || seenIds.has(item.id)) {
      continue;
    }
    merged.push(item);
    seenIds.add(item.id);
  }

  return merged;
}

export function removePendingAttachment(pending = [], attachmentId) {
  return pending.filter((item) => item.id !== attachmentId);
}

function toBase64FromBytes(bytes) {
  if (typeof Buffer !== "undefined") {
    return Buffer.from(bytes).toString("base64");
  }

  let binary = "";
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary);
}

export async function fileToDataUrl(file) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  const base64 = toBase64FromBytes(bytes);
  const mimeType = file.type || inferMimeTypeFromName(file.name || "");
  return `data:${mimeType};base64,${base64}`;
}

export function splitPendingAttachments(pending = []) {
  const transientImages = [];
  const persistentDocuments = [];

  for (const item of pending) {
    if (item?.kind === "image" && item?.deliveryMode === "ephemeral") {
      transientImages.push(item);
      continue;
    }
    if (item?.kind === "document" && item?.deliveryMode === "persist") {
      persistentDocuments.push(item);
    }
  }

  return {
    transientImages,
    persistentDocuments,
  };
}
