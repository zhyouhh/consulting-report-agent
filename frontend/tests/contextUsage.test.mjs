import test from "node:test";
import assert from "node:assert/strict";

import {
  formatContextUsage,
  getContextUsagePercent,
} from "../src/utils/contextUsage.js";

test("formatContextUsage returns honest labels for estimated usage", () => {
  assert.deepEqual(
    formatContextUsage({
      current_tokens: 132000,
      effective_max_tokens: 500000,
      usage_mode: "estimated",
      compressed: true,
    }),
    {
      label: "上下文估算",
      detail: "132k / 500k",
      modeTag: "估算",
      compressedTag: "已压缩",
    },
  );
});

test("getContextUsagePercent clamps at 100", () => {
  assert.equal(
    getContextUsagePercent({
      current_tokens: 800000,
      effective_max_tokens: 500000,
    }),
    100,
  );
});
