import test from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_WORKSPACE_WIDTH,
  MIN_WORKSPACE_WIDTH,
  MIN_CHAT_WIDTH,
  MAX_WORKSPACE_WIDTH,
  clampWorkspaceWidth,
  computeWorkspaceWidth,
  parseStoredWorkspaceWidth,
} from "../src/utils/workspaceResize.js";

test("clampWorkspaceWidth keeps values within [MIN, MAX] when no container given", () => {
  assert.equal(clampWorkspaceWidth(500), 500);
  assert.equal(clampWorkspaceWidth(MIN_WORKSPACE_WIDTH - 100), MIN_WORKSPACE_WIDTH);
  assert.equal(clampWorkspaceWidth(MAX_WORKSPACE_WIDTH + 500), MAX_WORKSPACE_WIDTH);
});

test("clampWorkspaceWidth falls back to default on non-finite width", () => {
  assert.equal(clampWorkspaceWidth(NaN), DEFAULT_WORKSPACE_WIDTH);
  assert.equal(clampWorkspaceWidth(undefined), DEFAULT_WORKSPACE_WIDTH);
});

test("clampWorkspaceWidth reserves MIN_CHAT_WIDTH from the container width", () => {
  // 容器 1000：workspace 上限 = min(MAX, 1000 - MIN_CHAT_WIDTH)
  const containerWidth = 1000;
  const expectedMax = Math.min(MAX_WORKSPACE_WIDTH, containerWidth - MIN_CHAT_WIDTH);
  assert.equal(clampWorkspaceWidth(9999, containerWidth), expectedMax);
});

test("clampWorkspaceWidth on a narrow container never starves the chat below MIN_CHAT_WIDTH", () => {
  // 容器只有 600：max = 600 - 360 = 240（< MIN_WORKSPACE_WIDTH），夹到 240，保聊天区 ≥ MIN_CHAT_WIDTH
  const containerWidth = 600;
  const result = clampWorkspaceWidth(500, containerWidth);
  assert.equal(result, containerWidth - MIN_CHAT_WIDTH);
  assert.ok(containerWidth - result >= MIN_CHAT_WIDTH);
});

test("clampWorkspaceWidth never returns a negative width on a sub-MIN_CHAT container", () => {
  // 容器比聊天区最小宽还窄（300 < 360）：maxAllowed 会算成负，必须夹到 0、绝不返回负宽
  const result = clampWorkspaceWidth(500, 300);
  assert.equal(result, 0);
  assert.ok(result >= 0);
});

test("computeWorkspaceWidth derives width from container right edge minus cursor X", () => {
  const rect = { left: 0, right: 1200, width: 1200, top: 0 };
  // 鼠标在 x=800 → workspace = 1200 - 800 = 400
  assert.equal(computeWorkspaceWidth(800, rect), 400);
  // 鼠标贴右缘 → 夹到下限
  assert.equal(computeWorkspaceWidth(1199, rect), MIN_WORKSPACE_WIDTH);
  // 鼠标拖到最左 → 夹到上限（受容器约束）
  const expectedMax = Math.min(MAX_WORKSPACE_WIDTH, 1200 - MIN_CHAT_WIDTH);
  assert.equal(computeWorkspaceWidth(0, rect), expectedMax);
});

test("computeWorkspaceWidth returns default when rect missing or zero width", () => {
  assert.equal(computeWorkspaceWidth(800, null), DEFAULT_WORKSPACE_WIDTH);
  assert.equal(computeWorkspaceWidth(800, { width: 0 }), DEFAULT_WORKSPACE_WIDTH);
});

test("parseStoredWorkspaceWidth clamps a stored number and defaults on garbage", () => {
  assert.equal(parseStoredWorkspaceWidth("520"), 520);
  assert.equal(parseStoredWorkspaceWidth(null), DEFAULT_WORKSPACE_WIDTH);
  assert.equal(parseStoredWorkspaceWidth("not-a-number"), DEFAULT_WORKSPACE_WIDTH);
  assert.equal(parseStoredWorkspaceWidth(String(MAX_WORKSPACE_WIDTH + 999)), MAX_WORKSPACE_WIDTH);
});
