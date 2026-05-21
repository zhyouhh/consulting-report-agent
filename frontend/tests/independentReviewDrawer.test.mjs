import test from "node:test";
import assert from "node:assert/strict";

import { parseDrawerEvent } from "../src/utils/independentReviewDrawer.js";

test("parseDrawerEvent recognizes review-completed event", () => {
  assert.deepEqual(
    parseDrawerEvent('{"type":"review-completed","path":"plan/independent-review.md"}'),
    { type: "review-completed", path: "plan/independent-review.md" },
  );
});

test("parseDrawerEvent recognizes error event", () => {
  assert.deepEqual(
    parseDrawerEvent('{"type":"error","detail":"正文过长"}'),
    { type: "error", detail: "正文过长" },
  );
});

test("parseDrawerEvent ignores malformed payload", () => {
  assert.equal(parseDrawerEvent("{bad json"), null);
  assert.equal(parseDrawerEvent(""), null);
});
