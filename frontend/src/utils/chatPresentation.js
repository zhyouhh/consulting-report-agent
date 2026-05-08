import { stripToolLogComments } from "./toolLogStrip.mjs";

export function takeStreamingTextSlice(text = "", size = 12) {
  return {
    emitted: text.slice(0, size),
    remaining: text.slice(size),
  };
}

export function extractSseDataPayload(line = "") {
  if (!line.startsWith("data:")) {
    return null;
  }

  return line.slice(5).trim();
}

export function shouldContinueSseStream({
  readerDone = false,
  streamCompleted = false,
} = {}) {
  return !readerDone && !streamCompleted;
}

export function shouldFlushStreamingQueueImmediately(reason = "") {
  return reason === "tool" || reason === "error" || reason === "abort";
}

export function shouldRenderSystemNoticeMessage(message = {}) {
  return message.role === "system_notice" && message.surface_to_user !== false;
}

export function appendToolEventContent(prev = "", toolText = "") {
  const separator = prev && !prev.endsWith("\n") ? "\n" : "";
  return `${prev}${separator}${toolText}\n`;
}

const THINKING_OPEN_TAG = "<thinking-block>";
const THINKING_CLOSE_TAG = "</thinking-block>";
const THINKING_OPEN_ESCAPE = "⟨THINKING_OPEN⟩";
const THINKING_CLOSE_ESCAPE = "⟨THINKING_CLOSE⟩";
const THINKING_BLOCK_RE = /<thinking-block>([\s\S]*?)<\/thinking-block>/g;
const THINKING_BLOCK_WITH_ADJACENT_NEWLINES_RE = /(\r?\n)?<thinking-block>[\s\S]*?<\/thinking-block>(\r?\n)?/g;

function escapeThinkingContent(content = "") {
  return content
    .replaceAll(THINKING_OPEN_TAG, THINKING_OPEN_ESCAPE)
    .replaceAll(THINKING_CLOSE_TAG, THINKING_CLOSE_ESCAPE);
}

export function unescapeThinkingContent(content = "") {
  return content
    .replaceAll(THINKING_OPEN_ESCAPE, THINKING_OPEN_TAG)
    .replaceAll(THINKING_CLOSE_ESCAPE, THINKING_CLOSE_TAG);
}

export function stripThinkingBlocks(content = "") {
  if (!content.includes(THINKING_OPEN_TAG)) {
    return content;
  }
  return content
    .replace(THINKING_BLOCK_WITH_ADJACENT_NEWLINES_RE, (_match, leading, trailing) => (
      leading && trailing ? leading : ""
    ))
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export function getCopyableAssistantMessageText(content = "") {
  return stripThinkingBlocks(stripToolLogComments(content || ""));
}

export function appendThinkingEventContent(prev = "", delta = "") {
  if (!delta) return prev;

  const escapedDelta = escapeThinkingContent(delta);
  if (prev.endsWith(THINKING_CLOSE_TAG)) {
    const openIndex = prev.lastIndexOf(THINKING_OPEN_TAG);
    const closeIndex = prev.length - THINKING_CLOSE_TAG.length;
    if (openIndex >= 0 && openIndex < closeIndex) {
      const existingContent = prev.slice(openIndex + THINKING_OPEN_TAG.length, closeIndex);
      return `${prev.slice(0, openIndex)}${THINKING_OPEN_TAG}${existingContent}${escapedDelta}${THINKING_CLOSE_TAG}`;
    }
  }

  const separator = prev && !prev.endsWith("\n") ? "\n" : "";
  return `${prev}${separator}${THINKING_OPEN_TAG}${escapedDelta}${THINKING_CLOSE_TAG}`;
}

export async function getStreamResponseError(response) {
  const contentType = response.headers.get("content-type") || "";
  if (response.ok && contentType.includes("text/event-stream")) {
    return null;
  }

  if (contentType.includes("application/json")) {
    try {
      const payload = await response.json();
      return payload?.detail || payload?.data || payload?.message || `HTTP ${response.status}`;
    } catch {
      return `HTTP ${response.status}`;
    }
  }

  try {
    const text = (await response.text()).trim();
    return text || `HTTP ${response.status}`;
  } catch {
    return `HTTP ${response.status}`;
  }
}

const STAGE_ACK_TAG_RE = /<stage-ack(?:\s+action="(?:set|clear)")?>[a-z_0-9]+<\/stage-ack>/gi;

export function stripStageAckTags(content = "") {
  if (!content.toLowerCase().includes("<stage-ack")) {
    return content;
  }
  return content.replace(STAGE_ACK_TAG_RE, "").replace(/\n{3,}/g, "\n\n").trim();
}

export function splitAssistantMessageBlocks(content = "") {
  const safeContent = stripStageAckTags(content);
  const blocks = [];
  let textBuffer = [];

  const flushTextBuffer = () => {
    const merged = textBuffer.join("\n").trim();
    if (merged) {
      blocks.push({ type: "text", content: merged });
    }
    textBuffer = [];
  };

  const appendNonThinkingSegment = (segment = "") => {
    const lines = segment.split("\n");
    for (const line of lines) {
      const isToolLine = line.startsWith("🔧 调用工具:") || line.startsWith("✅ 结果:") || line.startsWith("⚠️ 结果:");
      if (isToolLine) {
        flushTextBuffer();
        blocks.push({ type: "tool", content: line });
        continue;
      }
      textBuffer.push(line);
    }
  };

  let cursor = 0;
  for (const match of safeContent.matchAll(THINKING_BLOCK_RE)) {
    appendNonThinkingSegment(safeContent.slice(cursor, match.index));
    flushTextBuffer();
    blocks.push({ type: "thinking", content: match[1] });
    cursor = match.index + match[0].length;
  }
  appendNonThinkingSegment(safeContent.slice(cursor));

  flushTextBuffer();
  return blocks;
}

export function buildProjectWelcomeMessage(project = {}) {
  const lines = [
    `你好，我们现在在推进「${project.name || "咨询项目"}」这个项目。`,
  ];

  if (project.theme) {
    lines.push(`当前记录的报告主题是：${project.theme}。`);
  }

  const detailParts = [];
  if (project.project_type) {
    detailParts.push(`类型：${project.project_type}`);
  }
  if (project.target_audience) {
    detailParts.push(`目标读者：${project.target_audience}`);
  }
  if (project.deadline) {
    detailParts.push(`截止日期：${project.deadline}`);
  }
  if (project.expected_length) {
    detailParts.push(`预期篇幅：${project.expected_length}`);
  }
  if (detailParts.length > 0) {
    lines.push(detailParts.join("；") + "。");
  }

  if (project.notes) {
    lines.push(`已有备注：${project.notes}`);
  }

  lines.push("如果这些信息没问题，请直接补充你现在最想让我先做的那一步；如果有偏差，也可以直接纠正。");
  return lines.join("\n");
}

export const LEGACY_EMPTY_ASSISTANT_FALLBACKS = new Set([
  "（本轮无回复）",
  "（这一轮我没有产出可见回复，可能是处理过程中断了。请把刚才的需求换个说法再发一次。）",
]);

export function sanitizeAssistantMessage(message) {
  if (message.role !== "assistant") return message;
  const trimmed = (message.content || "").trim();
  if (LEGACY_EMPTY_ASSISTANT_FALLBACKS.has(trimmed)) return null;
  return message;
}
