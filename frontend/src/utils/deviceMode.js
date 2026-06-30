// 设备模式判定 + 抽屉互斥状态机（纯函数，无副作用，可在 node:test 直接测）。
// 移动壳是否启用按「主输入是不是手指」判定，与窗口宽度脱钩（spec §3）。

export function isCoarsePointer() {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false; // fallback = 桌面
  try {
    return window.matchMedia("(pointer: coarse)").matches;
  } catch {
    return false; // matchMedia 抛错（老浏览器/异常查询）→ fallback 桌面
  }
}

// 抽屉互斥：状态是单一枚举，天然保证「同时只开一个」。
export const DRAWER_NONE = "none";
export const DRAWER_LEFT = "left";
export const DRAWER_RIGHT = "right";

export function nextDrawerState(current, action) {
  switch (action) {
    case "toggleLeft": return current === DRAWER_LEFT ? DRAWER_NONE : DRAWER_LEFT;
    case "toggleRight": return current === DRAWER_RIGHT ? DRAWER_NONE : DRAWER_RIGHT;
    case "openLeft": return DRAWER_LEFT;
    case "openRight": return DRAWER_RIGHT;
    case "close": return DRAWER_NONE;
    default: return current;
  }
}
