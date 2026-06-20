// 文件预览面板上下分栏（文件树 / 预览编辑框）的高度计算。
// 默认三七分（文件树 30% / 预览 70%），中间分隔条可鼠标拖动调整。
// 纯函数 + 常量，便于在无 jsdom 环境下单测；组件只负责接事件。

export const DEFAULT_TREE_PCT = 30;
export const MIN_TREE_PCT = 15;
export const MAX_TREE_PCT = 70;

export function clampTreePct(pct) {
  if (!Number.isFinite(pct)) return DEFAULT_TREE_PCT;
  return Math.min(MAX_TREE_PCT, Math.max(MIN_TREE_PCT, pct));
}

// 由鼠标 Y 与容器矩形换算文件树高度百分比（拖动分隔条时调用）。
export function computeTreePct(clientY, rect) {
  if (!rect || !rect.height) return DEFAULT_TREE_PCT;
  const pct = ((clientY - rect.top) / rect.height) * 100;
  return clampTreePct(pct);
}
