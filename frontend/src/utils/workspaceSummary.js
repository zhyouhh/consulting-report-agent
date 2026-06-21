// §9.6 Delivery mode literals — these MUST match backend/skill.py:1012-1022
// which returns the Chinese string directly. Any mismatch breaks progress bar.
export const DELIVERY_MODE_REPORT_ONLY = "仅报告";
export const DELIVERY_MODE_REPORT_WITH_PRESENTATION = "报告+演示";

// Human-readable stage names — user-visible UI MUST use these instead of the
// raw stage codes. Keeps CLAUDE.md + Spec §9.5 "不暴露后台术语" invariant.
export const STAGE_NAMES = Object.freeze({
  S0: "准备阶段",
  S1: "拟定大纲",
  S2: "收集资料",
  S3: "分析论证",
  S4: "撰写报告",
  S5: "质量审查",
  S6: "准备演示",
  S7: "等待归档",
  done: "已完成",
});

/** Resolve a stage code to a human-readable Chinese name. */
export function getStageName(stageCode) {
  if (!stageCode) return "未开始";
  return STAGE_NAMES[stageCode] || "未开始";
}

export function summarizeWorkspace(apiSummary = {}) {
  const source = apiSummary || {};
  return {
    stageLabel: getStageName(source.stage_code),
    statusLabel: source.status || "待开始",
    completedItems: source.completed_items || [],
    nextActions: source.next_actions || [],
    // §9.1 stage-advance fields
    stageCode: source.stage_code || null,
    nextStageHint: source.next_stage_hint || null,
    flags: {
      ...(source.flags || {}),
      s0InterviewDone: Boolean((source.flags || {}).s0_interview_done),
      independentReviewReady: (source.flags || {}).independent_review_ready ?? false,
    },
    checkpoints: source.checkpoints || {},
    // §9.2 S4 word count / length targets
    wordCount: source.word_count ?? 0,
    lengthTargets: source.length_targets || null,
    lengthFallbackUsed: source.length_fallback_used === true,
    // §9.3 quality progress / stall
    qualityProgress: source.quality_progress || null,
    stalledSince: source.stalled_since || null,
    // §9.6 delivery mode → progress bar segment count
    deliveryMode: source.delivery_mode || DELIVERY_MODE_REPORT_ONLY,
  };
}

/**
 * Returns true when the progress bar should include the S6 "准备演示" segment.
 * Only true when backend explicitly says 报告+演示.
 */
export function shouldShowPresentationStage(deliveryMode) {
  return deliveryMode === DELIVERY_MODE_REPORT_WITH_PRESENTATION;
}

/**
 * Returns true when the S4 "complete writing, start review" secondary button
 * should be visible: word_count >= length_targets.report_word_floor.
 *
 * The backend (backend/skill.py:287-293) already budgets the 70% floor as
 * `report_word_floor = int(expected_length * 0.7)`, so the frontend must NOT
 * recompute it — the server owns that calculation.
 */
export function isS4ReviewButtonVisible(wordCount, lengthTargets) {
  if (!lengthTargets || typeof lengthTargets.report_word_floor !== "number") return false;
  return wordCount >= lengthTargets.report_word_floor;
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
  const outlineReady = checkpoints.outline_md_exists ?? flags.outline_ready;
  // R5: 后端透出 methodology_declared 时要求声明就绪；未透出（旧 schema / unknown type）→ 不阻塞。
  const methodologyDeclared = flags.methodology_declared ?? true;
  return !!(outlineReady && methodologyDeclared);
}

/**
 * R5: S1 确认按钮禁用时的原因文案（null = 不禁用）。区分「缺大纲」与「缺方法论声明」。
 */
export function s1ConfirmDisabledReason(summary = {}) {
  const checkpoints = summary.checkpoints || {};
  const flags = summary.flags || {};
  const outlineReady = checkpoints.outline_md_exists ?? flags.outline_ready;
  if (!outlineReady) return "需要先生成大纲才能继续";
  if (flags.methodology_declared === false) {
    return "请在大纲顶部补一行方法论声明（如「方法论框架：SWOT、波特五力」）";
  }
  return null;
}
