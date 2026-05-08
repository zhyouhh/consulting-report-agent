import test from "node:test";
import assert from "node:assert/strict";

import {
  appendThinkingEventContent,
  getCopyableAssistantMessageText,
  splitAssistantMessageBlocks,
  unescapeThinkingContent,
} from "../src/utils/chatPresentation.js";

test("appendThinkingEventContent wraps first delta in thinking block", () => {
  assert.equal(
    appendThinkingEventContent("", "正在拆解问题"),
    "<thinking-block>正在拆解问题</thinking-block>",
  );
});

test("appendThinkingEventContent merges trailing thinking deltas into one block", () => {
  const content = appendThinkingEventContent(
    appendThinkingEventContent("", "第一步"),
    "，第二步",
  );

  assert.equal(content, "<thinking-block>第一步，第二步</thinking-block>");
});

test("appendThinkingEventContent starts a new block after assistant content", () => {
  const content = appendThinkingEventContent(
    "<thinking-block>先想</thinking-block>\n正文已经输出",
    "继续想",
  );

  assert.equal(
    content,
    "<thinking-block>先想</thinking-block>\n正文已经输出\n<thinking-block>继续想</thinking-block>",
  );
});

test("splitAssistantMessageBlocks separates thinking blocks from text", () => {
  assert.deepEqual(
    splitAssistantMessageBlocks([
      "正文一",
      "<thinking-block>内部推理</thinking-block>",
      "正文二",
    ].join("\n")),
    [
      { type: "text", content: "正文一" },
      { type: "thinking", content: "内部推理" },
      { type: "text", content: "正文二" },
    ],
  );
});

test("splitAssistantMessageBlocks preserves multiline thinking content", () => {
  assert.deepEqual(
    splitAssistantMessageBlocks([
      "<thinking-block>第一行",
      "第二行",
      "第三行</thinking-block>",
    ].join("\n")),
    [
      { type: "thinking", content: "第一行\n第二行\n第三行" },
    ],
  );
});

test("literal thinking tags inside delta are escaped and do not create nested blocks", () => {
  const content = appendThinkingEventContent(
    "",
    "字面 <thinking-block> 标签和 </thinking-block> 标签",
  );

  assert.equal(
    content,
    "<thinking-block>字面 ⟨THINKING_OPEN⟩ 标签和 ⟨THINKING_CLOSE⟩ 标签</thinking-block>",
  );
  assert.deepEqual(
    splitAssistantMessageBlocks(content),
    [
      {
        type: "thinking",
        content: "字面 ⟨THINKING_OPEN⟩ 标签和 ⟨THINKING_CLOSE⟩ 标签",
      },
    ],
  );
});

test("unescapeThinkingContent restores escaped literal thinking tags", () => {
  assert.equal(
    unescapeThinkingContent("字面 ⟨THINKING_OPEN⟩ 和 ⟨THINKING_CLOSE⟩"),
    "字面 <thinking-block> 和 </thinking-block>",
  );
});

test("appendThinkingEventContent ignores empty deltas", () => {
  assert.equal(appendThinkingEventContent("正文", ""), "正文");
});

test("copy text strips thinking blocks and keeps visible reply", () => {
  const content = [
    "可见开头",
    "<thinking-block>内部推理不要复制</thinking-block>",
    "可见结尾",
    "<!-- tool-log",
    "- read_file ✓",
    "-->",
  ].join("\n");

  const copyText = getCopyableAssistantMessageText(content);

  assert.equal(copyText, "可见开头\n可见结尾");
  assert.equal(copyText.includes("thinking-block"), false);
  assert.equal(copyText.includes("内部推理"), false);
});
