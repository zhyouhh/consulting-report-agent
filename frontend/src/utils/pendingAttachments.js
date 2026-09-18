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

// 2026-09-18 起聊天框里的图片与文档一样入项目材料库（deliveryMode=persist）：主模型原生看图，
// 后端再后台转写一份供后续轮次记住图的内容；bmp/超大图由后端发模型前统一转码缩放。
// ephemeral（随消息一次性发送）只留作兼容。
export function buildPendingAttachment(file) {
  const image = isImageFile(file);
  const mimeType = file?.type || inferMimeTypeFromName(file?.name || "");
  return {
    id: newPendingAttachmentId(),
    file,
    displayName: file?.name || "attachment",
    mimeType,
    kind: image ? "image" : "document",
    deliveryMode: "persist",
    previewUrl: null,
  };
}

const GENERIC_PASTED_NAME = /^image\.(png|jpe?g|gif|webp|bmp)$/i;

function pad2(value) {
  return String(value).padStart(2, "0");
}

// 剪贴板图片通常都叫 image.png：入材料库前改成带时间戳的唯一名，否则材料列表一排同名，
// 且同一批上传里同名文件会在服务端暂存目录互相覆盖。
export function renamePastedFile(file, index = 0, now = new Date()) {
  if (!file || !isImageFile(file)) {
    return file;
  }
  const name = file.name || "";
  if (name && !GENERIC_PASTED_NAME.test(name)) {
    return file;
  }
  const ext = (name.split(".").pop() || "").toLowerCase() || (file.type || "image/png").split("/")[1] || "png";
  const stamp = `${now.getFullYear()}${pad2(now.getMonth() + 1)}${pad2(now.getDate())}-`
    + `${pad2(now.getHours())}${pad2(now.getMinutes())}${pad2(now.getSeconds())}`;
  const suffix = index > 0 ? `-${index + 1}` : "";
  return new File([file], `粘贴图片-${stamp}${suffix}.${ext}`, { type: file.type || inferMimeTypeFromName(`x.${ext}`) });
}

// 与后端 MAX_TRANSIENT_ATTACHMENTS 一致：一轮最多原生看这么多张图
export const MAX_IMAGES_PER_MESSAGE = 6;

// 按每条消息的图片上限截断新加入的文件（文档不计数）；返回 { accepted, droppedImages }
export function capImageAttachments(existing = [], files = []) {
  let images = existing.filter((item) => item?.kind === "image").length;
  const accepted = [];
  let droppedImages = 0;
  for (const file of files) {
    if (isImageFile(file)) {
      if (images >= MAX_IMAGES_PER_MESSAGE) {
        droppedImages += 1;
        continue;
      }
      images += 1;
    }
    accepted.push(file);
  }
  return { accepted, droppedImages };
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
  const persistentUploads = [];

  for (const item of pending) {
    if (item?.kind === "image" && item?.deliveryMode === "ephemeral") {
      transientImages.push(item);
      continue;
    }
    if (item?.deliveryMode === "persist") {
      persistentUploads.push(item);
    }
  }

  return {
    transientImages,
    persistentUploads,
  };
}
