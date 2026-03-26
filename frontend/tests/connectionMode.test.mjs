import test from "node:test";
import assert from "node:assert/strict";

import { describeConnectionMode } from "../src/utils/connectionMode.js";

test("describeConnectionMode returns managed label", () => {
  assert.deepEqual(
    describeConnectionMode({
      mode: "managed",
      managed_model: "gemini-3-flash",
    }),
    {
      title: "默认通道",
      subtitle: "推荐，开箱即用 · gemini-3-flash",
    },
  );
});

test("describeConnectionMode returns custom label", () => {
  assert.deepEqual(
    describeConnectionMode({
      mode: "custom",
      custom_model: "gpt-4.1-mini",
    }),
    {
      title: "自定义 API",
      subtitle: "gpt-4.1-mini",
    },
  );
});

test("describeConnectionMode falls back to managed when settings are missing", () => {
  assert.deepEqual(
    describeConnectionMode(),
    {
      title: "默认通道",
      subtitle: "推荐，开箱即用 · gemini-3-flash",
    },
  );
});
