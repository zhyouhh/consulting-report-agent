/**
 * Guard tests for checkpoint POST error feedback.
 *
 * Contract: any failed checkpoint POST must produce a user-visible message
 * that includes a non-empty detail string. Silent failure violates CLAUDE.md
 * "反馈引导行动".
 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const controlSource = readFileSync(
  resolve(here, "../src/components/StageAdvanceControl.jsx"),
  "utf-8"
);
const rollbackSource = readFileSync(
  resolve(here, "../src/components/RollbackMenu.jsx"),
  "utf-8"
);

test("StageAdvanceControl wraps checkpoint POST in try/catch", () => {
  assert.ok(controlSource.includes("try {"), "must have try block");
  assert.ok(controlSource.includes("catch (err)"), "must have catch");
});

test("StageAdvanceControl surfaces failure via showError (not alert)", () => {
  assert.ok(controlSource.includes("showError"));
  assert.equal(controlSource.includes("alert("), false, "must not use alert()");
});

test("RollbackMenu wraps checkpoint POST in try/catch", () => {
  assert.ok(rollbackSource.includes("try {"));
  assert.ok(rollbackSource.includes("catch (err)"));
});

test("RollbackMenu surfaces failure via showError (not alert)", () => {
  assert.ok(rollbackSource.includes("showError"));
  assert.equal(rollbackSource.includes("alert("), false);
});

test("Error message falls back to user-friendly Chinese when detail missing", () => {
  // Both components must have a fallback string; the placeholder '请稍后重试'
  // guards against the error surfacing as 'undefined' to the user.
  assert.ok(controlSource.includes("请稍后重试"));
  assert.ok(rollbackSource.includes("请稍后重试"));
});

// Simulate the error-message composition pattern used in both components.
function composeErrorMessage(err) {
  const detail = err?.response?.data?.detail || err?.message || "请稍后重试";
  return `操作失败：${detail}`;
}

test("composeErrorMessage prefers axios response detail", () => {
  const err = { response: { data: { detail: "项目不存在" } }, message: "Network Error" };
  assert.equal(composeErrorMessage(err), "操作失败：项目不存在");
});

test("composeErrorMessage falls back to err.message when no detail", () => {
  const err = { message: "Network Error" };
  assert.equal(composeErrorMessage(err), "操作失败：Network Error");
});

test("composeErrorMessage falls back to placeholder when neither is present", () => {
  assert.equal(composeErrorMessage({}), "操作失败：请稍后重试");
  assert.equal(composeErrorMessage(null), "操作失败：请稍后重试");
});

test("Pending state guards against double-submission in StageAdvanceControl", () => {
  assert.ok(controlSource.includes("pending"));
  assert.ok(controlSource.includes("setPending"));
});
