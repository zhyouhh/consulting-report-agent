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
  const safeSettings = settings || {};

  if ((safeSettings.mode || "managed") === "managed") {
    return true;
  }

  const modelName = (safeSettings.custom_model || safeSettings.model || "").toLowerCase();
  if (!modelName) {
    return false;
  }

  return MULTIMODAL_MODEL_MARKERS.some((marker) => modelName.includes(marker));
}
