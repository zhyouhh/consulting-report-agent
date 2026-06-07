// Pure helpers for the S5 independent-review mini-chat window (ReviewChatWindow).
// These are framework-agnostic so they can be unit-tested with node:test (the project has no
// jsdom / testing-library — DOM behaviour is covered by source-level guards instead).

export function parseDrawerEvent(data) {
  if (!data || data === "[DONE]") return null;
  try {
    const parsed = JSON.parse(data);
    if (!parsed || typeof parsed !== "object" || typeof parsed.type !== "string") {
      return null;
    }
    if (parsed.type === "error") {
      const message = [parsed.detail, parsed.data, parsed.message]
        .find(value => typeof value === "string" && value.trim().length > 0) || "审查失败";
      return { ...parsed, message };
    }
    // review-completed carries run_id + report_mtime_ns. report_mtime_ns is an OPAQUE STRING
    // (a nanosecond mtime > 2^53). It is passed through verbatim — NEVER Number()/parseInt,
    // which would silently round it and break the backend run-bound injection check.
    return parsed;
  } catch {
    return null;
  }
}

// Bubble model for the window stream:
//   { kind: 'assistant', text }            — streamed model prose (markdown)
//   { kind: 'tool_call', tool, args }      — a tool invocation card
//   { kind: 'tool_result', tool, status, summary } — a tool result card
//
// Consecutive content_delta events append to the CURRENT assistant bubble (not one bubble per
// delta — that would fragment the stream). A tool_call / tool_result event closes the current
// assistant bubble so the next content_delta starts a fresh one.
export function aggregateContentDelta(messages, event) {
  const list = Array.isArray(messages) ? messages : [];
  if (!event || typeof event.type !== "string") return list;

  if (event.type === "content_delta") {
    const text = typeof event.text === "string" ? event.text : "";
    if (!text) return list;
    const last = list[list.length - 1];
    if (last && last.kind === "assistant") {
      const next = list.slice(0, -1);
      next.push({ ...last, text: last.text + text });
      return next;
    }
    return [...list, { kind: "assistant", text }];
  }

  if (event.type === "tool_call") {
    return [...list, { kind: "tool_call", tool: event.tool || "", args: event.args }];
  }

  if (event.type === "tool_result") {
    return [
      ...list,
      {
        kind: "tool_result",
        tool: event.tool || "",
        status: event.status || "",
        summary: event.summary || "",
      },
    ];
  }

  return list;
}

// Extract the "第 N 轮" round number from a progress event, falling back to the previous round.
function readRound(event, prevRound) {
  const detail = typeof event.detail === "string" ? event.detail : "";
  const match = detail.match(/第\s*(\d+)\s*轮/);
  if (match) return Number(match[1]);
  return prevRound;
}

export function initialReviewWindowState() {
  return {
    status: "running", // running | errored | completed
    error: null,
    runId: null,
    reportMtimeNs: null,
    round: 1,
    action: "正在准备审查…",
  };
}

// Pure state machine driving the window. Consumes backend events (and a synthetic
// 'resume-start' the component dispatches when the user clicks 继续审查).
//   running   : streaming; input locked.
//   errored   : error persists (does NOT auto-close); input unlocked + "继续审查".
//   completed : "审查完成" -> the component auto-closes (without calling /discard).
export function reviewWindowReducer(state, event) {
  if (!event || typeof event.type !== "string") return state;

  switch (event.type) {
    case "resume-start":
      return { ...state, status: "running", error: null, action: "正在续审…" };
    case "progress":
      // Errored is sticky until an explicit resume — don't let a stray event resurrect it.
      if (state.status === "errored") return state;
      return {
        ...state,
        status: "running",
        round: readRound(event, state.round),
        action: typeof event.detail === "string" && event.detail ? event.detail : state.action,
      };
    case "content_delta":
      if (state.status === "errored" || state.status === "completed") return state;
      return state.status === "running" ? state : { ...state, status: "running" };
    case "tool_call":
      if (state.status === "errored") return state;
      return { ...state, status: "running", action: `调用工具：${event.tool || ""}` };
    case "tool_result":
      if (state.status === "errored") return state;
      return { ...state, status: "running", action: `工具完成：${event.tool || ""}` };
    case "error":
      return { ...state, status: "errored", error: event.message || "审查失败" };
    case "review-completed":
      // report_mtime_ns kept as the opaque string it arrived as.
      return {
        ...state,
        status: "completed",
        error: null,
        runId: event.run_id ?? state.runId,
        reportMtimeNs: event.report_mtime_ns ?? state.reportMtimeNs,
      };
    default:
      return state;
  }
}

export function genRunId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  // Fallback for environments without crypto.randomUUID (kept opaque; never parsed as a number).
  return `run-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

// 409 backoff: the previous run is still finishing. Retry with exponential backoff capped at a
// max number of attempts, then stop and offer the user an explicit way out.
export const MAX_BACKOFF_ATTEMPTS = 5;

export function nextBackoff(attempt, { baseMs = 1000, capMs = 16000 } = {}) {
  const safeAttempt = Math.max(0, Number(attempt) || 0);
  return Math.min(baseMs * 2 ** safeAttempt, capMs);
}

export function backoffExhausted(attempt, max = MAX_BACKOFF_ATTEMPTS) {
  return (Number(attempt) || 0) >= max;
}
