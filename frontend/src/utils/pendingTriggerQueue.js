// Pending system-trigger queue for the main chat.
//
// When an independent-review report finishes while the main chat is busy (streaming another turn or
// uploading), the trigger that would report it must NOT be silently dropped — the user would
// then never hear the finding. These pure helpers hold pending triggers FIFO until the current
// stream ends, then the component flushes them (re-issuing each with its ORIGINAL metadata).
//
// Each item: { triggerType, run_id, report_mtime_ns, projectId }.
// run_id / report_mtime_ns are OPAQUE STRINGS — never coerced to Number anywhere.

export function createPendingTriggerItem({ triggerType, runId = null, reportMtimeNs = null, projectId }) {
  return {
    triggerType,
    run_id: runId,
    report_mtime_ns: reportMtimeNs,
    projectId,
  };
}

// Append a pending trigger. A NEW independent_review_done run supersedes any older pending
// independent_review_done item for the SAME project: once a new run overwrites the store's done
// tombstone, an older pending flush would always be run-bound-rejected (old run white-burned and
// the user sees an error). So drop the stale same-type/same-project entries first.
export function enqueuePendingTrigger(queue, item) {
  const list = Array.isArray(queue) ? queue : [];
  if (!item || !item.triggerType || !item.projectId) return list;

  let pruned = list;
  if (item.triggerType === "independent_review_done") {
    pruned = list.filter(
      existing =>
        !(existing.triggerType === "independent_review_done" && existing.projectId === item.projectId),
    );
  }
  return [...pruned, item];
}

// Take the next item for the given active project (FIFO), dropping any leading items that belong
// to other projects (project switch: a pending trigger for project A must never be re-issued
// while project B is active — the backend would reject it on run-bound and the user would see a
// spurious error, while A's review goes unreported). Returns { item, queue }.
export function dequeuePendingTrigger(queue, activeProjectId) {
  const list = Array.isArray(queue) ? queue : [];
  let index = 0;
  while (index < list.length && list[index].projectId !== activeProjectId) {
    index += 1;
  }
  if (index >= list.length) {
    // No item for the active project. Keep only items still scoped to the active project
    // (discard the cross-project leftovers we skipped — they belong to projects not in view).
    return { item: null, queue: list.filter(entry => entry.projectId === activeProjectId) };
  }
  const item = list[index];
  // Remove the taken item plus the cross-project items we skipped over it.
  const rest = list.slice(index + 1);
  return { item, queue: rest };
}

// Drop every pending trigger that does not belong to the active project (used on project switch).
export function scopePendingQueueToProject(queue, activeProjectId) {
  const list = Array.isArray(queue) ? queue : [];
  return list.filter(entry => entry.projectId === activeProjectId);
}

// Drop pending triggers of a given type for a project — called when the user STARTS a new run of
// that type (plan Task 5.3: a new run supersedes any older same-type pending BEFORE it completes).
// enqueue-time pruning is too late: between "report R1 finished + queued (chat busy)" and "user
// starts R2", R2's claim_first overwrites the store's done tombstone, so the still-queued R1 flush
// is later run-bound-rejected and R1's successful review surfaces as a spurious error. Pruning at
// run-start closes that window.
export function dropPendingTriggersByType(queue, triggerType, projectId) {
  const list = Array.isArray(queue) ? queue : [];
  if (!triggerType || !projectId) return list;
  return list.filter(entry => !(entry.triggerType === triggerType && entry.projectId === projectId));
}
