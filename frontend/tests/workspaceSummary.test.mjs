import test from "node:test";
import assert from "node:assert/strict";

import { summarizeWorkspace } from "../src/utils/workspaceSummary.js";

test("summarizeWorkspace falls back safely when stage data is missing", () => {
  const summary = summarizeWorkspace({});
  assert.equal(summary.stageLabel, "未开始");
  assert.equal(summary.statusLabel, "待开始");
  assert.deepEqual(summary.completedItems, []);
  assert.deepEqual(summary.nextActions, []);
});

test("summarizeWorkspace preserves api summary values", () => {
  const summary = summarizeWorkspace({
    stage_code: "S4",
    status: "进行中",
    completed_items: ["报告结构确定"],
    next_actions: ["图表制作完成"],
  });
  assert.equal(summary.stageLabel, "S4");
  assert.equal(summary.statusLabel, "进行中");
  assert.deepEqual(summary.completedItems, ["报告结构确定"]);
  assert.deepEqual(summary.nextActions, ["图表制作完成"]);
});
