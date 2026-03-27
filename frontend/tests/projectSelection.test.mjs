import test from "node:test";
import assert from "node:assert/strict";

import {
  getCurrentProject,
  reconcileCurrentProjectId,
} from "../src/utils/projectSelection.js";

const projects = [
  { id: "proj-1", name: "项目一" },
  { id: "proj-2", name: "项目二" },
];

test("getCurrentProject returns the matching project object", () => {
  assert.deepEqual(getCurrentProject(projects, "proj-2"), projects[1]);
});

test("getCurrentProject returns null for unknown project id", () => {
  assert.equal(getCurrentProject(projects, "missing"), null);
});

test("reconcileCurrentProjectId keeps the current id when it still exists", () => {
  assert.equal(reconcileCurrentProjectId(projects, "proj-1"), "proj-1");
});

test("reconcileCurrentProjectId clears the current id when the project disappears", () => {
  assert.equal(reconcileCurrentProjectId(projects, "missing"), null);
});
