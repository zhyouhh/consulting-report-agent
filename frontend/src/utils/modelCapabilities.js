// 与后端 chat.py MULTIMODAL_MODEL_MARKERS 同步
// Used by the backend to decide raw image_url vs transcription path.
// NOT the same as supportsImageAttachments — see below.
const MULTIMODAL_MODEL_MARKERS = [
  "gemini",
  "gpt-4o",
  "gpt-4.1",
  "vision",
  "vl",
  "claude-3",
  "claude-sonnet-4",
];

// supportsImageAttachments — "can the app ACCEPT image uploads from the user?"
//
// Always returns true since N6 added vision transcription: images attached to a
// text-only model are transcribed to text on the backend before being sent.
// This is DIFFERENT from whether the MAIN model natively supports vision (that
// distinction lives in the backend `_main_model_supports_vision` /
// `MULTIMODAL_MODEL_MARKERS` logic, which decides raw image_url vs transcription).
//
// Closes issue #4: the old per-model gate blocked image uploads on text-only
// custom models even though N6 transcription handles them correctly.
export function supportsImageAttachments(_settings = {}) {
  return true;
}
