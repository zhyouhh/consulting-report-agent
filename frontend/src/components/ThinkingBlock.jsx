import React from "react";
import { unescapeThinkingContent } from "../utils/chatPresentation";

export function ThinkingBlock({ text }) {
  return React.createElement(
    "details",
    { className: "thinking-block" },
    React.createElement("summary", null, "推理过程"),
    React.createElement("div", { className: "thinking-content" }, unescapeThinkingContent(text)),
  );
}

export default ThinkingBlock;
