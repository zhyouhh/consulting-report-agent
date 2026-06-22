export function formatYuan(n) {
  const v = Number.isFinite(n) ? n : 0;
  return `¥${v.toFixed(2)}`;
}

export function quotaLabel(used, cap) {
  return `今日 ${formatYuan(used)} / ${formatYuan(cap)}`;
}

export function quotaRatio(used, cap) {
  // 先把 used/cap 归一到 finite number，保证结果恒在 [0, 1]（绝不返回 NaN——
  // 否则接进度条/宽度/aria 数值会污染 UI）。非 finite 或 cap<=0 → 0。
  const u = Number.isFinite(used) ? used : 0;
  const c = Number.isFinite(cap) ? cap : 0;
  if (c <= 0) return 0;
  return Math.max(0, Math.min(1, u / c));
}
