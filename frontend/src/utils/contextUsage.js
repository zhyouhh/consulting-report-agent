function resolveEffectiveMaxTokens(tokenUsage = {}) {
  return tokenUsage.effective_max_tokens ?? tokenUsage.max_tokens ?? 0;
}

function resolveContextUsedTokens(tokenUsage = {}) {
  return tokenUsage.context_used_tokens ?? tokenUsage.current_tokens ?? 0;
}

function formatTokenValue(value) {
  if (value === null || value === undefined) {
    return '未提供';
  }

  if (Math.abs(value) >= 1000) {
    const compact = (value / 1000).toFixed(1).replace(/\.0$/, '');
    return `${compact}k`;
  }

  return String(value);
}

function resolveUsageModeTag(tokenUsage = {}) {
  switch (tokenUsage.usage_source) {
    case 'provider':
      return 'Provider真实统计';
    case 'provider_partial':
      return 'Provider部分提供';
    default:
      return 'Provider未提供';
  }
}

function resolveCompactionPresentation(tokenUsage = {}) {
  switch (tokenUsage.post_turn_compaction_status) {
    case 'completed':
      return {
        compressedTag: '已自动整理',
        compactedStatus: '已在本轮结束后完成自动整理',
      };
    case 'failed':
      return {
        compressedTag: '整理失败',
        compactedStatus: '已触发自动整理，但本轮整理失败',
      };
    case 'skipped_unavailable':
      return {
        compressedTag: '',
        compactedStatus: '本轮未获得真实 usage，未触发自动整理',
      };
    default:
      return {
        compressedTag: tokenUsage.preflight_compaction_used ? '已预压缩' : '',
        compactedStatus: tokenUsage.preflight_compaction_used ? '发送前已做安全压缩' : '',
      };
  }
}

export function getContextUsagePercent(tokenUsage = {}) {
  const max = resolveEffectiveMaxTokens(tokenUsage);

  if (tokenUsage.usage_source === 'unavailable' || tokenUsage.context_used_tokens === null) {
    return null;
  }

  const current = resolveContextUsedTokens(tokenUsage);

  if (!max) {
    return 0;
  }

  return Math.min(100, (current / max) * 100);
}

export function formatContextUsage(tokenUsage = {}) {
  const current = tokenUsage.context_used_tokens ?? (
    tokenUsage.usage_source === 'unavailable' ? null : resolveContextUsedTokens(tokenUsage)
  );
  const max = resolveEffectiveMaxTokens(tokenUsage);
  const { compressedTag, compactedStatus } = resolveCompactionPresentation(tokenUsage);

  return {
    label: tokenUsage.usage_source === 'provider' ? '上下文真实用量' : '上下文用量',
    detail: `${formatTokenValue(current)} / ${formatTokenValue(max)}`,
    modeTag: resolveUsageModeTag(tokenUsage),
    compressedTag,
    compactedStatus,
    fields: [
      { label: '输入', value: formatTokenValue(tokenUsage.input_tokens) },
      { label: '输出', value: formatTokenValue(tokenUsage.output_tokens) },
      { label: '总计', value: formatTokenValue(tokenUsage.total_tokens) },
      { label: 'Provider上限', value: formatTokenValue(tokenUsage.provider_max_tokens) },
    ],
  };
}
