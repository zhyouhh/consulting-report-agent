import test from "node:test";
import assert from "node:assert/strict";
import {
  isCoarsePointer,
  nextDrawerState,
  DRAWER_NONE, DRAWER_LEFT, DRAWER_RIGHT,
} from "../src/utils/deviceMode.js";

test("isCoarsePointer: matchMedia 缺失 → false（fallback 桌面）", () => {
  const prev = globalThis.window;
  globalThis.window = {}; // 无 matchMedia
  assert.equal(isCoarsePointer(), false);
  globalThis.window = prev;
});

test("isCoarsePointer: matchMedia 命中 coarse → true", () => {
  const prev = globalThis.window;
  globalThis.window = { matchMedia: (q) => ({ matches: q === "(pointer: coarse)" }) };
  assert.equal(isCoarsePointer(), true);
  globalThis.window = prev;
});

test("isCoarsePointer: matchMedia 抛错 → false（fail-safe 桌面）", () => {
  const prev = globalThis.window;
  globalThis.window = { matchMedia: () => { throw new Error("boom"); } };
  assert.equal(isCoarsePointer(), false);
  globalThis.window = prev;
});

test("nextDrawerState: 互斥——开右关左、开左关右", () => {
  assert.equal(nextDrawerState(DRAWER_LEFT, "openRight"), DRAWER_RIGHT);
  assert.equal(nextDrawerState(DRAWER_RIGHT, "openLeft"), DRAWER_LEFT);
});

test("nextDrawerState: toggle 同侧→关、close→none", () => {
  assert.equal(nextDrawerState(DRAWER_LEFT, "toggleLeft"), DRAWER_NONE);
  assert.equal(nextDrawerState(DRAWER_NONE, "toggleLeft"), DRAWER_LEFT);
  assert.equal(nextDrawerState(DRAWER_RIGHT, "toggleRight"), DRAWER_NONE);
  assert.equal(nextDrawerState(DRAWER_LEFT, "close"), DRAWER_NONE);
});

test("nextDrawerState: 未知 action 原样返回", () => {
  assert.equal(nextDrawerState(DRAWER_RIGHT, "wat"), DRAWER_RIGHT);
});
