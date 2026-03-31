import test from "node:test";
import assert from "node:assert/strict";

import { shouldSubmitComposerKeydown } from "../src/utils/composerInputBehavior.js";

test("Enter submits only when not composing and Shift is not pressed", () => {
  assert.equal(
    shouldSubmitComposerKeydown({ key: "Enter", shiftKey: false, isComposing: false }),
    true,
  );
  assert.equal(
    shouldSubmitComposerKeydown({ key: "Enter", shiftKey: false, isComposing: true }),
    false,
  );
  assert.equal(
    shouldSubmitComposerKeydown({ key: "Enter", shiftKey: true, isComposing: false }),
    false,
  );
  assert.equal(
    shouldSubmitComposerKeydown({ key: "a", shiftKey: false, isComposing: false }),
    false,
  );
});
