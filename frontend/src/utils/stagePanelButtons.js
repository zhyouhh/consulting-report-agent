const EXPORT_STAGES = new Set(["S6", "S7", "done"]);

function flagValue(flags, camelKey, snakeKey) {
  return flags?.[camelKey] ?? flags?.[snakeKey] ?? false;
}

export function shouldShowButton(buttonKey, stageCode, flags = {}) {
  if (buttonKey === "independent_review" || buttonKey === "lint_report") {
    return stageCode === "S5";
  }
  if (buttonKey === "export_draft") {
    return EXPORT_STAGES.has(stageCode) && Boolean(flagValue(flags, "reportReady", "report_ready"));
  }
  return false;
}

export function getStageButtonState(buttonKey, stageCode, flags = {}, running = {}) {
  const visible = shouldShowButton(buttonKey, stageCode, flags);
  if (!visible) {
    return { visible: false, highlighted: false, disabled: false };
  }

  const anyS5ToolRunning = Boolean(running.reviewRunning || running.lintRunning);
  if (buttonKey === "independent_review") {
    return {
      visible,
      highlighted: !flagValue(flags, "independentReviewReady", "independent_review_ready"),
      disabled: anyS5ToolRunning,
    };
  }
  if (buttonKey === "lint_report") {
    return {
      visible,
      highlighted: !flagValue(flags, "lintReportReady", "lint_report_ready"),
      disabled: anyS5ToolRunning,
    };
  }
  return { visible, highlighted: false, disabled: false };
}
