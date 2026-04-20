export function summarizeWorkspace(apiSummary = {}) {
  const source = apiSummary || {};
  return {
    stageLabel: source.stage_code || "未开始",
    statusLabel: source.status || "待开始",
    completedItems: source.completed_items || [],
    nextActions: source.next_actions || [],
    // §9.1 stage-advance fields
    stageCode: source.stage_code || null,
    nextStageHint: source.next_stage_hint || null,
    flags: source.flags || {},
    checkpoints: source.checkpoints || {},
    // §9.2 S4 word count / length targets
    wordCount: source.word_count ?? 0,
    lengthTargets: source.length_targets || null,
    lengthFallbackUsed: source.length_fallback_used === true,
    // §9.3 quality progress / stall
    qualityProgress: source.quality_progress || null,
    stalledSince: source.stalled_since || null,
    // §9.6 delivery mode → progress bar segment count
    deliveryMode: source.delivery_mode || "report_only",
  };
}

/**
 * Returns true when the S4 "complete writing, start review" secondary button
 * should be visible: word_count >= length_targets.target * 0.7
 */
export function isS4ReviewButtonVisible(wordCount, lengthTargets) {
  if (!lengthTargets || !lengthTargets.target) return false;
  return wordCount >= lengthTargets.target * 0.7;
}

/**
 * Returns true when the S1 "确认大纲，进入资料采集" button should be enabled.
 * Backend signals outline readiness via `flags.outline_ready`
 * (source: backend/skill.py:1255-1275 — "outline.md exists and is non-empty").
 * `checkpoints.outline_md_exists` is preferred when present for forward
 * compatibility, but current backend does not set it.
 */
export function isS1ConfirmOutlineEnabled(summary = {}) {
  const checkpoints = summary.checkpoints || {};
  const flags = summary.flags || {};
  return !!(checkpoints.outline_md_exists ?? flags.outline_ready);
}
