import test from "node:test";
import assert from "node:assert/strict";

import { supportsImageAttachments } from "../src/utils/modelCapabilities.js";

test("supportsImageAttachments always allows managed mode", () => {
  assert.equal(
    supportsImageAttachments({
      mode: "managed",
      managed_model: "gemini-3-flash",
    }),
    true,
  );
});

test("supportsImageAttachments falls back safely while settings are loading", () => {
  assert.equal(supportsImageAttachments(null), true);
});

test("supportsImageAttachments recognizes common multimodal custom models", () => {
  assert.equal(
    supportsImageAttachments({
      mode: "custom",
      custom_model: "gpt-4.1-mini",
    }),
    true,
  );
  assert.equal(
    supportsImageAttachments({
      mode: "custom",
      custom_model: "qwen2.5-vl-72b-instruct",
    }),
    true,
  );
});

// N6: image upload is always accepted; the backend transcription path handles text-only models.
// supportsImageAttachments means "the app CAN accept image uploads" (always true),
// NOT "the main model natively supports vision" (that's isMultimodalModel / backend MULTIMODAL_MODEL_MARKERS).
test("images always uploadable regardless of model — N6 transcription covers text-only models", () => {
  // custom text-only model: previously blocked, now always allowed
  assert.equal(
    supportsImageAttachments({
      mode: "custom",
      custom_model: "deepseek-chat",
    }),
    true,
  );
  // managed deepseek (text-only): always allowed
  assert.equal(
    supportsImageAttachments({ mode: "managed", managed_model: "deepseek-v4-pro" }),
    true,
  );
  // null / loading state: always allowed
  assert.equal(supportsImageAttachments(null), true);
});
