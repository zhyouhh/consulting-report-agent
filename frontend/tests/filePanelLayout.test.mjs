import test from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_TREE_PCT,
  MIN_TREE_PCT,
  MAX_TREE_PCT,
  clampTreePct,
  computeTreePct,
} from "../src/utils/filePanelLayout.js";

test("clampTreePct keeps values within [MIN, MAX]", () => {
  assert.equal(clampTreePct(30), 30);
  assert.equal(clampTreePct(5), MIN_TREE_PCT);
  assert.equal(clampTreePct(95), MAX_TREE_PCT);
});

test("clampTreePct falls back to default on non-finite input", () => {
  assert.equal(clampTreePct(NaN), DEFAULT_TREE_PCT);
  assert.equal(clampTreePct(undefined), DEFAULT_TREE_PCT);
});

test("computeTreePct converts mouse Y within container to a clamped percentage", () => {
  const rect = { top: 100, height: 400 }; // 容器从 y=100 到 y=500
  // 拖到 y=200 → (200-100)/400 = 25%
  assert.equal(computeTreePct(200, rect), 25);
  // 拖到容器顶部以上 → 夹到下限
  assert.equal(computeTreePct(0, rect), MIN_TREE_PCT);
  // 拖到容器底部以下 → 夹到上限
  assert.equal(computeTreePct(1000, rect), MAX_TREE_PCT);
});

test("computeTreePct returns default when rect missing", () => {
  assert.equal(computeTreePct(200, null), DEFAULT_TREE_PCT);
  assert.equal(computeTreePct(200, { top: 0, height: 0 }), DEFAULT_TREE_PCT);
});
