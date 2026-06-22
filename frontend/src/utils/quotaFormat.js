export function formatYuan(n) {
  const v = Number.isFinite(n) ? n : 0;
  return `¥${v.toFixed(2)}`;
}

export function quotaLabel(used, cap) {
  return `今日 ${formatYuan(used)} / ${formatYuan(cap)}`;
}

export function quotaRatio(used, cap) {
  if (!cap || cap <= 0) return 0;
  return Math.max(0, Math.min(1, used / cap));
}
