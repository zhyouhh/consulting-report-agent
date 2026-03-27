export function summarizeWorkspace(apiSummary = {}) {
  const source = apiSummary || {};
  return {
    stageLabel: source.stage_code || "未开始",
    statusLabel: source.status || "待开始",
    completedItems: source.completed_items || [],
    nextActions: source.next_actions || [],
  };
}
