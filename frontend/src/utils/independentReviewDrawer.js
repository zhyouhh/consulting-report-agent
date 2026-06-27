// Pure helpers for the S5 independent-review mini-chat window (ReviewChatWindow).
// These are framework-agnostic so they can be unit-tested with node:test (the project has no
// jsdom / testing-library — DOM behaviour is covered by source-level guards instead).

import { closePendingToolEvents } from "./toolEvents.js";

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
//   { kind: 'assistant', text }                          — streamed model prose (markdown)
//   { kind: 'tool', id, tool, arg, status, summary }     — one tool call+result pill (id-paired)
//
// Consecutive content_delta events append to the CURRENT assistant bubble (not one bubble per
// delta — that would fragment the stream). A tool_call appends a pending tool bubble (which closes
// the current assistant bubble so the next content_delta starts a fresh one). A tool_result is
// paired BY id to its pending tool bubble and updates status/summary in place — mirroring the main
// chat's reduceToolEvent so the shared ToolCallPill renders identically in both surfaces. An error
// event flips any still-pending tool bubble to error (a review can break between a tool_call frame
// and its result over the wire — mirrors the main chat's closePendingToolEvents so no pill spins
// forever).
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
    const id = event.id;
    const idx = id != null ? list.findIndex(b => b && b.kind === "tool" && b.id === id) : -1;
    if (idx === -1) {
      return [
        ...list,
        { kind: "tool", id, tool: event.tool || "", arg: event.arg || "", status: "pending", summary: "" },
      ];
    }
    // Late chunk carrying the fully-formed tool/arg for an already-announced pending pill.
    const next = list.slice();
    next[idx] = { ...next[idx], tool: event.tool ?? next[idx].tool, arg: event.arg ?? next[idx].arg };
    return next;
  }

  if (event.type === "tool_result") {
    const id = event.id;
    const idx = id != null ? list.findIndex(b => b && b.kind === "tool" && b.id === id) : -1;
    if (idx === -1) {
      // Result before its call, or a synthetic-id malformed-batch error: append a completed bubble
      // (without an id we'd swallow the card; the backend always sends a real or synthetic id).
      return [
        ...list,
        { kind: "tool", id, tool: event.tool || "", arg: "", status: event.status || "error", summary: event.summary || "" },
      ];
    }
    const next = list.slice();
    next[idx] = { ...next[idx], status: event.status || "error", summary: event.summary || "" };
    return next;
  }

  if (event.type === "error") {
    // The review broke before some tool_call got its result frame — close orphan pending pills.
    return closePendingToolEvents(list, "已中断");
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
