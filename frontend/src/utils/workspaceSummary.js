export function summarizeWorkspace(apiSummary = {}) {
  return {
    stageLabel: apiSummary.stage_code || "未开始",
    statusLabel: apiSummary.status || "待开始",
    completedItems: apiSummary.completed_items || [],
    nextActions: apiSummary.next_actions || [],
  };
}
