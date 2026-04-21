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

export function appendToolEventContent(prev = "", toolText = "") {
  const separator = prev && !prev.endsWith("\n") ? "\n" : "";
  return `${prev}${separator}${toolText}\n`;
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

export function splitAssistantMessageBlocks(content = "") {
  const lines = content.split("\n");
  const blocks = [];
  let textBuffer = [];

  const flushTextBuffer = () => {
    const merged = textBuffer.join("\n").trim();
    if (merged) {
      blocks.push({ type: "text", content: merged });
    }
    textBuffer = [];
  };

  for (const line of lines) {
    const isToolLine = line.startsWith("🔧 调用工具:") || line.startsWith("✅ 结果:") || line.startsWith("⚠️ 结果:");
    if (isToolLine) {
      flushTextBuffer();
      blocks.push({ type: "tool", content: line });
      continue;
    }
    textBuffer.push(line);
  }

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
