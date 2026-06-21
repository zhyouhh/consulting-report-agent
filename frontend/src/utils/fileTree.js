export const GROUP_ORDER = [
  "overview", "research", "analysis", "draft", "review", "delivery", "other", "tracking",
];

export const GROUP_LABELS = {
  overview: "项目概览",
  research: "研究与素材",
  analysis: "大纲与分析",
  draft: "报告正文",
  review: "审查报告",
  delivery: "演示与交付",
  tracking: "阶段追踪·系统",
  other: "其他",
};

export const FILE_DISPLAY_NAMES = {
  "plan/project-overview.md": "项目概览",
  "plan/notes.md": "研究笔记",
  "plan/references.md": "资料来源",
  "plan/data-log.md": "资料采集记录",
  "plan/outline.md": "报告大纲",
  "plan/research-plan.md": "研究方案",
  "plan/analysis-notes.md": "分析记录",
  "content/report_draft_v1.md": "报告正文",
  "plan/independent-review.md": "独立审查报告",
  "plan/presentation-plan.md": "演示计划",
  "plan/delivery-log.md": "交付记录",
  "plan/stage-gates.md": "阶段门禁（系统）",
  "plan/progress.md": "项目进度（系统）",
  "plan/tasks.md": "阶段任务（系统）",
  "plan/review.md": "审查记录",
};

export function displayName(path) {
  if (FILE_DISPLAY_NAMES[path]) return FILE_DISPLAY_NAMES[path];
  const base = String(path).split("/").pop() || String(path);
  return base.replace(/\.md$/i, "");
}

export function buildFileTree(files = [], currentStage = null) {
  const byGroup = new Map();
  for (const file of files) {
    const group = file.group && GROUP_LABELS[file.group] ? file.group : "other";
    if (!byGroup.has(group)) byGroup.set(group, []);
    byGroup.get(group).push({
      ...file,
      group,
      label: displayName(file.path),
      isCurrentStage: currentStage != null && file.stage === currentStage,
    });
  }
  const groups = [];
  for (const group of GROUP_ORDER) {
    const groupFiles = byGroup.get(group);
    if (!groupFiles || groupFiles.length === 0) continue;
    groupFiles.sort((a, b) => {
      if (a.isCurrentStage !== b.isCurrentStage) return a.isCurrentStage ? -1 : 1;
      return a.path.localeCompare(b.path);
    });
    groups.push({
      group,
      label: GROUP_LABELS[group],
      files: groupFiles,
      hasCurrentStage: groupFiles.some((f) => f.isCurrentStage),
      defaultCollapsed: group === "tracking",
    });
  }
  // 当前阶段所在分组置顶；其余分组保持 GROUP_ORDER 流水线序（上一阶段自然紧随其后）。
  const currentIdx = groups.findIndex((g) => g.hasCurrentStage);
  if (currentIdx > 0) {
    const [currentGroup] = groups.splice(currentIdx, 1);
    groups.unshift(currentGroup);
  }
  return groups;
}
