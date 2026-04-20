/**
 * A11y guard tests for ConfirmDialog.
 *
 * Source-level assertions: node:test cannot render React, so verify the
 * component template contains the required WAI-ARIA attributes and wiring.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(
  resolve(here, "../src/components/ConfirmDialog.jsx"),
  "utf-8"
);

test("dialog has role=dialog", () => {
  assert.ok(/role="dialog"/.test(source));
});

test("dialog has aria-modal=true", () => {
  assert.ok(/aria-modal="true"/.test(source));
});

test("dialog has aria-labelledby pointing at a title id", () => {
  assert.ok(/aria-labelledby=\{titleId\}/.test(source));
  assert.ok(/id=\{titleId\}/.test(source));
});

test("ESC key closes the dialog", () => {
  assert.ok(source.includes("keydown"));
  assert.ok(source.includes("'Escape'"));
  assert.ok(source.includes("onCancel"));
});

test("focus moves to cancel button on open (safe default)", () => {
  // cancelRef is attached to the cancel button and focused in useEffect
  assert.ok(source.includes("cancelRef"));
  assert.ok(source.includes("cancelRef.current?.focus()"));
});

test("previously focused element is restored on close", () => {
  assert.ok(source.includes("previouslyFocusedRef"));
});

test("useId is used for titleId to avoid collisions", () => {
  // React's useId gives stable unique ids across SSR/CSR
  assert.ok(source.includes("useId"));
});

test("focus-visible ring styles present for keyboard users", () => {
  // Both buttons should have focus ring classes
  const ringOccurrences = (source.match(/focus:ring-2/g) || []).length;
  assert.ok(ringOccurrences >= 2, "both buttons need a focus ring");
});
