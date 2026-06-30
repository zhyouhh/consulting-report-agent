// 抽屉滑动手势判定（纯函数，无副作用，可在 node:test 直接测）。
// 开着抽屉时任一明确水平滑动 = 关闭（用户对「往哪滑算关」直觉不一，任一向都接受最不卡）；
// 没开抽屉时：右滑(dx>0)出左抽屉、左滑(dx<0)出右抽屉（标准抽屉方向）。
// 阈值 + 主轴(ax>ay 严格)判定，避开纵向滚动与轻点误触。绝不在此做 DOM/动画——只回动作。
export const SWIPE_THRESHOLD_PX = 60;

export function resolveSwipeAction(dx, dy, anyDrawerOpen, threshold = SWIPE_THRESHOLD_PX) {
  if (!Number.isFinite(dx) || !Number.isFinite(dy) || !Number.isFinite(threshold)) return null; // fail-closed：非有限值/数字字符串/Infinity/坏阈值一律不接管
  const ax = Math.abs(dx);
  const ay = Math.abs(dy);
  if (ax < threshold || ax <= ay) return null; // 非明确水平滑动
  if (anyDrawerOpen) return "close";            // 开着 → 任一水平向关闭
  return dx > 0 ? "openLeft" : "openRight";     // 没开 → 右滑出左、左滑出右
}
