import test from "node:test";
import assert from "node:assert/strict";

import { shouldApplyProjectResponse } from "../src/utils/projectRequestOwnership.js";

test("shouldApplyProjectResponse accepts responses for the active project", () => {
  assert.equal(
    shouldApplyProjectResponse({
      requestProject: "demo-project",
      activeProject: "demo-project",
    }),
    true,
  );
});

test("shouldApplyProjectResponse rejects stale responses after project switch", () => {
  assert.equal(
    shouldApplyProjectResponse({
      requestProject: "demo-project",
      activeProject: "another-project",
    }),
    false,
  );
});

test("shouldApplyProjectResponse rejects responses when no project is active", () => {
  assert.equal(
    shouldApplyProjectResponse({
      requestProject: "demo-project",
      activeProject: null,
    }),
    false,
  );
});
