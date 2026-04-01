import test from "node:test";
import assert from "node:assert/strict";

import {
  getDefaultPreviewFile,
  orderPreviewFiles,
} from "../src/utils/workspaceFiles.js";

test("getDefaultPreviewFile prefers project-overview", () => {
  const paths = [
    "plan/project-info.md",
    "notes/context.md",
    "plan/project-overview.md",
  ];

  assert.equal(
    getDefaultPreviewFile(paths),
    "plan/project-overview.md",
  );
});

test("getDefaultPreviewFile ignores retired project-info when overview is missing", () => {
  const paths = [
    "notes/context.md",
    "plan/project-info.md",
  ];

  assert.equal(
    getDefaultPreviewFile(paths),
    "notes/context.md",
  );
});

test("getDefaultPreviewFile returns empty when only retired project-info exists", () => {
  assert.equal(
    getDefaultPreviewFile(["plan/project-info.md"]),
    "",
  );
});

test("orderPreviewFiles removes retired project-info from the file list", () => {
  const paths = [
    "plan/project-info.md",
    "draft/report.md",
    "plan/project-overview.md",
  ];

  assert.deepEqual(orderPreviewFiles(paths), [
    "plan/project-overview.md",
    "draft/report.md",
  ]);
});

test("orderPreviewFiles keeps ordering deterministic for unsorted backend paths", () => {
  const paths = [
    "notes/z-notes.md",
    "plan/project-info.md",
    "content/report.md",
    "analysis/a-findings.md",
  ];

  assert.deepEqual(orderPreviewFiles(paths), [
    "analysis/a-findings.md",
    "content/report.md",
    "notes/z-notes.md",
  ]);
});
