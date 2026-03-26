import test from "node:test";
import assert from "node:assert/strict";

import { getNextQualityResult } from "../src/utils/workspacePanelState.js";

test("getNextQualityResult preserves result when refreshing the same project", () => {
  const currentResult = { status: "ok", output: "quality passed" };

  assert.deepEqual(
    getNextQualityResult({
      currentResult,
      previousProject: "demo-project",
      nextProject: "demo-project",
    }),
    currentResult,
  );
});

test("getNextQualityResult clears result when switching projects", () => {
  assert.equal(
    getNextQualityResult({
      currentResult: { status: "ok", output: "quality passed" },
      previousProject: "demo-project",
      nextProject: "another-project",
    }),
    null,
  );
});

test("getNextQualityResult clears result when project is removed", () => {
  assert.equal(
    getNextQualityResult({
      currentResult: { status: "ok", output: "quality passed" },
      previousProject: "demo-project",
      nextProject: null,
    }),
    null,
  );
});
