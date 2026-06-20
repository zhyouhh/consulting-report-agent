import test from "node:test";
import assert from "node:assert/strict";

import { buildFileTree, displayName, GROUP_ORDER } from "../src/utils/fileTree.js";

const files = [
  { path: "plan/project-overview.md", group: "overview", stage: "S0", editable: false, mtime_ns: "1" },
  { path: "plan/notes.md", group: "research", stage: "S1", editable: true, mtime_ns: "2" },
  { path: "plan/data-log.md", group: "research", stage: "S2", editable: true, mtime_ns: "3" },
  { path: "content/report_draft_v1.md", group: "draft", stage: "S4", editable: true, mtime_ns: "4" },
  { path: "plan/presentation-plan.md", group: "delivery", stage: "S6", editable: true, mtime_ns: "5" },
  { path: "plan/stage-gates.md", group: "tracking", stage: null, editable: false, mtime_ns: "6" },
  { path: "weird/unknown.md", group: "other", stage: null, editable: false, mtime_ns: "7" },
];

test("displayName maps known path to Chinese, falls back to basename", () => {
  assert.equal(displayName("content/report_draft_v1.md"), "报告正文");
  assert.equal(displayName("plan/data-log.md"), "资料采集记录");
  assert.equal(displayName("weird/unknown.md"), "unknown");
});

test("buildFileTree floats current-stage group to top, tracking last", () => {
  const tree = buildFileTree(files, "S2");
  const groupKeys = tree.map((g) => g.group);
  // 当前阶段（S2 → research）分组置顶
  assert.equal(groupKeys[0], "research");
  // tracking 仍在最后
  assert.equal(groupKeys[groupKeys.length - 1], "tracking");
  // 置顶组之后，其余分组保持 GROUP_ORDER 子序
  const restIdx = groupKeys.slice(1).map((k) => GROUP_ORDER.indexOf(k));
  assert.deepEqual(restIdx, [...restIdx].sort((a, b) => a - b));
});

test("buildFileTree keeps GROUP_ORDER when current-stage group is already first", () => {
  const tree = buildFileTree(files, "S0"); // overview 当前 → 本就置顶
  const idx = tree.map((g) => GROUP_ORDER.indexOf(g.group));
  assert.deepEqual(idx, [...idx].sort((a, b) => a - b));
});

test("buildFileTree sorts current-stage file to the top of its group", () => {
  // S2 → data-log 应排在 research 组顶部（在 notes/S1 之前）
  const tree = buildFileTree(files, "S2");
  const research = tree.find((g) => g.group === "research");
  assert.equal(research.files[0].path, "plan/data-log.md");
  assert.equal(research.files[0].isCurrentStage, true);
  assert.equal(research.hasCurrentStage, true);
});

test("buildFileTree marks S6 presentation-plan current when stage=S6", () => {
  const tree = buildFileTree(files, "S6");
  const delivery = tree.find((g) => g.group === "delivery");
  assert.equal(delivery.files[0].path, "plan/presentation-plan.md");
  assert.equal(delivery.files[0].isCurrentStage, true);
});

test("buildFileTree attaches label + tracking defaultCollapsed", () => {
  const tree = buildFileTree(files, "S2");
  const tracking = tree.find((g) => g.group === "tracking");
  assert.equal(tracking.defaultCollapsed, true);
  assert.equal(tracking.files[0].label, "阶段门禁（系统）");
});

test("buildFileTree routes unknown group to other", () => {
  const tree = buildFileTree([{ path: "x/y.md", group: "nonsense", stage: null }], null);
  assert.equal(tree[0].group, "other");
});
