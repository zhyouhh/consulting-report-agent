import test from "node:test";
import assert from "node:assert/strict";
import { resolveSwipeAction, SWIPE_THRESHOLD_PX } from "../src/utils/drawerSwipe.js";

test("开着抽屉时任一水平滑动 → close", () => {
  assert.equal(resolveSwipeAction(80, 10, true), "close");   // 右滑
  assert.equal(resolveSwipeAction(-80, 10, true), "close");  // 左滑
});
test("没开抽屉：右滑出左抽屉、左滑出右抽屉", () => {
  assert.equal(resolveSwipeAction(80, 10, false), "openLeft");
  assert.equal(resolveSwipeAction(-80, 10, false), "openRight");
});
test("纵向为主 → null（让它正常滚动）", () => {
  assert.equal(resolveSwipeAction(70, 90, true), null);
  assert.equal(resolveSwipeAction(10, 200, false), null);
});
test("水平位移太小（轻点/抖动） → null", () => {
  assert.equal(resolveSwipeAction(20, 0, true), null);
  assert.equal(resolveSwipeAction(SWIPE_THRESHOLD_PX - 1, 0, false), null);
  assert.equal(resolveSwipeAction(SWIPE_THRESHOLD_PX, 0, false), "openLeft"); // 边界含
});
test("斜向 45° (|dx|==|dy|) → null（要求水平占优 ax>ay 严格）", () => {
  assert.equal(resolveSwipeAction(70, 70, true), null);
});
test("非数字入参 → 安全 null", () => {
  assert.equal(resolveSwipeAction(undefined, undefined, true), null);
  assert.equal(resolveSwipeAction(NaN, NaN, false), null);
});
test("fail-closed：混入非有限值 / 数字字符串 / Infinity / 坏 threshold → null", () => {
  assert.equal(resolveSwipeAction(80, undefined, false), null);
  assert.equal(resolveSwipeAction(80, 'x', false), null);
  assert.equal(resolveSwipeAction('80', 0, false), null);       // 数字字符串不接受
  assert.equal(resolveSwipeAction(Infinity, 0, true), null);
  assert.equal(resolveSwipeAction(80, 10, false, NaN), null);   // 坏 threshold
});
