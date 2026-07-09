import test from "node:test";
import assert from "node:assert/strict";
import {
  resolveWorkspaceFileLink,
  pathFromToolEvent,
} from "../src/utils/workspaceFileLinks.js";

// 文件内链纯函数：白名单精确匹配（完整路径 / 唯一 basename），不做模糊匹配。

test("resolveWorkspaceFileLink resolves full canonical paths", () => {
  assert.equal(resolveWorkspaceFileLink("plan/outline.md"), "plan/outline.md");
  assert.equal(
    resolveWorkspaceFileLink("content/report_draft_v1.md"),
    "content/report_draft_v1.md",
  );
  assert.equal(
    resolveWorkspaceFileLink("plan/independent-review.md"),
    "plan/independent-review.md",
  );
});

test("resolveWorkspaceFileLink resolves bare basenames to canonical paths", () => {
  assert.equal(resolveWorkspaceFileLink("outline.md"), "plan/outline.md");
  assert.equal(
    resolveWorkspaceFileLink("report_draft_v1.md"),
    "content/report_draft_v1.md",
  );
  // trim 容忍两侧空白（模型反引号里偶带空格）
  assert.equal(resolveWorkspaceFileLink("  notes.md "), "plan/notes.md");
});

test("resolveWorkspaceFileLink rejects unknown / truncated / non-string input", () => {
  assert.equal(resolveWorkspaceFileLink("materials/imported/foo.md"), null);
  assert.equal(resolveWorkspaceFileLink("plan/outline"), null);
  assert.equal(resolveWorkspaceFileLink("随便一段文字"), null);
  // _sse_tool_arg 40 字符截断带省略号 → 匹配不上，不给链接
  assert.equal(resolveWorkspaceFileLink("plan/very-long-file-name-that-was-trunc..."), null);
  assert.equal(resolveWorkspaceFileLink(""), null);
  assert.equal(resolveWorkspaceFileLink(null), null);
  assert.equal(resolveWorkspaceFileLink(undefined), null);
  assert.equal(resolveWorkspaceFileLink(42), null);
});

test("pathFromToolEvent maps append_report_draft to the canonical draft (arg is empty)", () => {
  assert.equal(
    pathFromToolEvent({ tool: "append_report_draft", arg: "" }),
    "content/report_draft_v1.md",
  );
});

test("pathFromToolEvent resolves path-arg tools via the whitelist", () => {
  assert.equal(
    pathFromToolEvent({ tool: "write_file", arg: "plan/outline.md" }),
    "plan/outline.md",
  );
  assert.equal(
    pathFromToolEvent({ tool: "edit_file", arg: "content/report_draft_v1.md" }),
    "content/report_draft_v1.md",
  );
  assert.equal(
    pathFromToolEvent({ tool: "read_file", arg: "plan/notes.md" }),
    "plan/notes.md",
  );
});

test("pathFromToolEvent returns null for non-file tools and unknown paths", () => {
  assert.equal(pathFromToolEvent({ tool: "web_search", arg: "关键词" }), null);
  assert.equal(pathFromToolEvent({ tool: "fetch_url", arg: "https://x.y" }), null);
  assert.equal(pathFromToolEvent({ tool: "advance_stage", arg: "s0_interview_done_at" }), null);
  assert.equal(pathFromToolEvent({ tool: "read_file", arg: "materials/imported/a.docx" }), null);
  assert.equal(pathFromToolEvent(null), null);
  assert.equal(pathFromToolEvent(undefined), null);
  assert.equal(pathFromToolEvent({}), null);
});
