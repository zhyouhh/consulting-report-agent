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
  if ((settings.mode || "managed") === "managed") {
    return true;
  }

  const modelName = (settings.custom_model || settings.model || "").toLowerCase();
  if (!modelName) {
    return false;
  }

  return MULTIMODAL_MODEL_MARKERS.some((marker) => modelName.includes(marker));
}
