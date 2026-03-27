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

test("supportsImageAttachments blocks unknown custom text-only models by default", () => {
  assert.equal(
    supportsImageAttachments({
      mode: "custom",
      custom_model: "deepseek-chat",
    }),
    false,
  );
});
