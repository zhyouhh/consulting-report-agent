import test from "node:test";
import assert from "node:assert/strict";

import { parseDrawerEvent } from "../src/utils/independentReviewDrawer.js";

test("parseDrawerEvent recognizes review-completed with run_id + report_mtime_ns", () => {
  assert.deepEqual(
    parseDrawerEvent('{"type":"review-completed","run_id":"run-1","report_mtime_ns":"1760000000123456789"}'),
    { type: "review-completed", run_id: "run-1", report_mtime_ns: "1760000000123456789" },
  );
});

test("parseDrawerEvent keeps report_mtime_ns an opaque string (never Number)", () => {
  const big = "1760000000123456789";
  const parsed = parseDrawerEvent(`{"type":"review-completed","run_id":"r","report_mtime_ns":"${big}"}`);
  assert.equal(typeof parsed.report_mtime_ns, "string");
  assert.equal(parsed.report_mtime_ns, big);
  // A round-trip through Number() would lose precision; the parser must not do that.
  assert.notEqual(String(Number(parsed.report_mtime_ns)), big);
});

test("parseDrawerEvent recognizes content_delta events", () => {
  assert.deepEqual(
    parseDrawerEvent('{"type":"content_delta","text":"第一段"}'),
    { type: "content_delta", text: "第一段" },
  );
});

test("parseDrawerEvent recognizes error event", () => {
  assert.deepEqual(
    parseDrawerEvent('{"type":"error","detail":"正文过长"}'),
    { type: "error", detail: "正文过长", message: "正文过长" },
  );
});

test("parseDrawerEvent normalizes backend string data error event", () => {
  assert.deepEqual(
    parseDrawerEvent('{"type":"error","data":"模型调用失败"}'),
    { type: "error", data: "模型调用失败", message: "模型调用失败" },
  );
});

test("parseDrawerEvent ignores malformed payload", () => {
  assert.equal(parseDrawerEvent("{bad json"), null);
  assert.equal(parseDrawerEvent(""), null);
  assert.equal(parseDrawerEvent("[DONE]"), null);
});
