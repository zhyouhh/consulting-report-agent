// 与后端 chat.py MULTIMODAL_MODEL_MARKERS 同步
const MULTIMODAL_MODEL_MARKERS = [
  "gemini",
  "gpt-4o",
  "gpt-4.1",
  "vision",
  "vl",
  "claude-3",
  "claude-sonnet-4",
];

export function supportsImageAttachments(settings = {}) {
  const normalizedSettings = settings || {};

  if ((normalizedSettings.mode || "managed") === "managed") {
    return true;
  }

  const modelName = (normalizedSettings.custom_model || normalizedSettings.model || "").toLowerCase();
  if (!modelName) {
    return false;
  }

  return MULTIMODAL_MODEL_MARKERS.some((marker) => modelName.includes(marker));
}
