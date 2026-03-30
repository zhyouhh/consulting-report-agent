function resolveEffectiveMaxTokens(tokenUsage = {}) {
  return tokenUsage.effective_max_tokens ?? tokenUsage.max_tokens ?? 0;
}

export function getContextUsagePercent(tokenUsage = {}) {
  const max = resolveEffectiveMaxTokens(tokenUsage);
  const current = tokenUsage.current_tokens || 0;

  if (!max) {
    return 0;
  }

  return Math.min(100, (current / max) * 100);
}

export function formatContextUsage(tokenUsage = {}) {
  const mode = tokenUsage.usage_mode === 'actual' ? 'actual' : 'estimated';
  const current = tokenUsage.current_tokens || 0;
  const max = resolveEffectiveMaxTokens(tokenUsage);

  return {
    label: mode === 'actual' ? '上下文用量' : '上下文估算',
    detail: `${Math.round(current / 1000)}k / ${Math.round(max / 1000)}k`,
    modeTag: mode === 'actual' ? '实际' : '估算',
    compressedTag: tokenUsage.compressed ? '已压缩' : '',
  };
}
