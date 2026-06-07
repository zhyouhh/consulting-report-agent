import test from "node:test";
import assert from "node:assert/strict";

import { describeConnectionMode } from "../src/utils/connectionMode.js";

test("describeConnectionMode returns managed label", () => {
  assert.deepEqual(
    describeConnectionMode({
      mode: "managed",
      managed_model: "deepseek-v4-pro",
    }),
    {
      title: "试用通道",
      subtitle: "仅供试用 · deepseek-v4-pro",
      helper: "有自己的模型/API，可点击下方“连接设置”接入。",
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
      helper: "",
    },
  );
});

test("describeConnectionMode falls back to managed when settings are missing", () => {
  assert.deepEqual(
    describeConnectionMode(),
    {
      title: "试用通道",
      subtitle: "仅供试用 · deepseek-v4-pro",
      helper: "有自己的模型/API，可点击下方“连接设置”接入。",
    },
  );
});

test("describeConnectionMode exposes helper text for managed mode", () => {
  assert.equal(
    describeConnectionMode({
      mode: "managed",
      managed_model: "deepseek-v4-pro",
    }).helper,
    "有自己的模型/API，可点击下方“连接设置”接入。",
  );
});

test("describeConnectionMode managed copy stays neutral", () => {
  const description = describeConnectionMode({
    mode: "managed",
    managed_model: "deepseek-v4-pro",
  });

  assert.equal(description.subtitle.includes("推荐"), false);
});
