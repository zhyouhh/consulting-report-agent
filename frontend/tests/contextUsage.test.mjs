import test from "node:test";
import assert from "node:assert/strict";

import {
  formatContextUsage,
  getContextUsagePercent,
} from "../src/utils/contextUsage.js";

test("formatContextUsage shows provider-real usage and compaction status", () => {
  assert.deepEqual(
    formatContextUsage({
      usage_source: "provider",
      context_used_tokens: 180000,
      input_tokens: 180000,
      output_tokens: 1200,
      total_tokens: 181200,
      effective_max_tokens: 200000,
      provider_max_tokens: 1000000,
      preflight_compaction_used: false,
      post_turn_compaction_status: "completed",
      compressed: false,
    }),
    {
      label: "上下文真实用量",
      detail: "180k / 200k",
      modeTag: "Provider真实统计",
      compressedTag: "已自动整理",
      compactedStatus: "已在本轮结束后完成自动整理",
      fields: [
        { label: "输入", value: "180k" },
        { label: "输出", value: "1.2k" },
        { label: "总计", value: "181.2k" },
        { label: "Provider上限", value: "1000k" },
      ],
    },
  );
});

test("getContextUsagePercent clamps at 100", () => {
  assert.equal(
    getContextUsagePercent({
      context_used_tokens: 260000,
      effective_max_tokens: 200000,
    }),
    100,
  );
});

test("formatContextUsage shows unavailable fields honestly", () => {
  assert.deepEqual(
    formatContextUsage({
      usage_source: "unavailable",
      context_used_tokens: null,
      effective_max_tokens: 200000,
      provider_max_tokens: 1000000,
      post_turn_compaction_status: "skipped_unavailable",
      preflight_compaction_used: false,
    }),
    {
      label: "上下文用量",
      detail: "未提供 / 200k",
      modeTag: "Provider未提供",
      compressedTag: "",
      compactedStatus: "本轮未获得真实 usage，未触发自动整理",
      fields: [
        { label: "输入", value: "未提供" },
        { label: "输出", value: "未提供" },
        { label: "总计", value: "未提供" },
        { label: "Provider上限", value: "1000k" },
      ],
    },
  );
});

test("getContextUsagePercent returns null when usage is unavailable", () => {
  assert.equal(
    getContextUsagePercent({
      usage_source: "unavailable",
      context_used_tokens: null,
      effective_max_tokens: 200000,
    }),
    null,
  );
});
