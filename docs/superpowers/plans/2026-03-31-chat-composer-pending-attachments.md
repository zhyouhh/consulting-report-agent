# Chat Composer Pending Attachments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the bottom chat composer into a multiline input with a pending-attachments queue, where pasted/dropped/picked images stay transient for one turn while documents are uploaded into project materials before the chat request.

**Architecture:** Keep the existing `FastAPI + React` desktop shape and avoid inventing a second attachment storage system. Backend adds one thin `transient_attachments` request field for one-turn images, while frontend keeps a local `pendingAttachments` queue and only reuses the current materials API for document uploads. Testability comes from extracting pure frontend helpers instead of trying to fully component-test the current large `ChatPanel`.

**Tech Stack:** Python 3.12, FastAPI, OpenAI-compatible client SDK, React 18, axios, Python `unittest`, Node built-in test runner, Vite

---

## Scope Note

This plan covers one subsystem only:

1. Multiline composer behavior
2. Pending attachment queue
3. Transient image request plumbing
4. Document pre-upload and retry semantics

This plan does **not** include:

1. New project modal cleanup
2. Draw.io skill evaluation
3. Default model replacement
4. Other unrelated chat UX bugs

**Spec reference:** `docs/superpowers/specs/2026-03-31-chat-composer-pending-attachments-design.md`

## File Structure

### New files to create

- `frontend/src/utils/pendingAttachments.js`
  Responsibility: classify files, assign default delivery modes, merge/remove pending items, and convert image files to transient payloads.
- `frontend/src/utils/composerInputBehavior.js`
  Responsibility: centralize IME-safe `Enter` / `Shift+Enter` behavior into a pure helper.
- `frontend/tests/pendingAttachments.test.mjs`
  Responsibility: cover queue operations, file classification, transient image payload building, and retry-safe document handling helpers.
- `frontend/tests/composerInputBehavior.test.mjs`
  Responsibility: cover IME-safe send behavior and multiline key handling.

### Existing files to modify

- `backend/models.py`
  Responsibility: extend `ChatRequest` with `transient_attachments`.
- `tests/test_models.py`
  Responsibility: prove the new request model accepts transient attachments without breaking old payloads.
- `backend/chat.py`
  Responsibility: pass transient images into provider messages while keeping persisted conversation free of transient attachment state.
- `tests/test_chat_runtime.py`
  Responsibility: cover transient image injection, document retry semantics, and no-persistence behavior.
- `frontend/src/utils/chatMaterials.js`
  Responsibility: extend chat request building to include transient attachments.
- `frontend/tests/chatMaterials.test.mjs`
  Responsibility: verify the richer request payload shape.
- `frontend/src/components/ChatPanel.jsx`
  Responsibility: replace single-line input with a multiline composer, add pending attachment UI, unify file entry points, and implement send-time branching.
- `docs/current-worklist.md`
  Responsibility: mark this work item as planned/in progress/completed once implementation lands.

## Task 1: Extend Chat Request Schema For Transient Attachments

**Files:**
- Modify: `backend/models.py`
- Modify: `tests/test_models.py`

- [ ] **Step 1: Write the failing schema tests**

```python
def test_chat_request_accepts_transient_image_attachments(self):
    payload = ChatRequest(
        project_id="proj-demo",
        message_text="请看这张截图",
        attached_material_ids=["mat-1"],
        transient_attachments=[
            {
                "name": "bug.png",
                "mime_type": "image/png",
                "data_url": "data:image/png;base64,AAAA",
            }
        ],
    )
    self.assertEqual(payload.transient_attachments[0].mime_type, "image/png")

def test_chat_request_defaults_transient_attachments_to_empty_list(self):
    payload = ChatRequest(project_id="proj-demo", message_text="hello")
    self.assertEqual(payload.transient_attachments, [])

def test_chat_request_rejects_non_image_transient_attachments(self):
    with self.assertRaises(Exception):
        ChatRequest(
            project_id="proj-demo",
            message_text="请看附件",
            transient_attachments=[
                {
                    "name": "memo.pdf",
                    "mime_type": "application/pdf",
                    "data_url": "data:application/pdf;base64,AAAA",
                }
            ],
        )
```

- [ ] **Step 2: Run the schema tests to verify they fail**

Run: `python -m unittest tests.test_models -v`

Expected: FAIL because `ChatRequest` has no `transient_attachments` field yet.

- [ ] **Step 3: Add the minimal request models**

Implement in `backend/models.py`:

```python
class TransientAttachment(BaseModel):
    name: str
    mime_type: str
    data_url: str

    @model_validator(mode="after")
    def _ensure_image_only(self):
        if not self.mime_type.startswith("image/"):
            raise ValueError("transient_attachments 只允许图片类型")
        return self


class ChatRequest(BaseModel):
    project_id: str = ...
    message_text: str = ...
    attached_material_ids: List[str] = Field(default_factory=list)
    transient_attachments: List[TransientAttachment] = Field(default_factory=list)
```

Keep the legacy-field validator intact. Only add the new list field.

- [ ] **Step 4: Run the schema tests to verify they pass**

Run: `python -m unittest tests.test_models -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/models.py tests/test_models.py
git commit -m "feat: add transient chat attachment schema"
```

## Task 2: Teach Backend Chat Runtime To Carry One-Turn Images

**Files:**
- Modify: `backend/chat.py`
- Modify: `tests/test_chat_runtime.py`

- [ ] **Step 1: Write the failing backend runtime tests**

Add focused tests covering:

```python
def test_build_user_content_includes_transient_images_without_material_lookup(self):
    message = {
        "role": "user",
        "content": "请看截图",
        "attached_material_ids": [],
        "transient_attachments": [
            {
                "name": "bug.png",
                "mime_type": "image/png",
                "data_url": "data:image/png;base64,AAAA",
            }
        ],
    }
    provider = handler._to_provider_message("demo", message, include_images=True)
    assert provider["content"][1]["type"] == "image_url"

def test_chat_does_not_persist_transient_attachments_into_conversation_history(self):
    result = handler._build_persisted_user_message(
        message_text="请看截图",
        attached_material_ids=["mat-1"],
        transient_attachments=[{"name": "bug.png", "mime_type": "image/png", "data_url": "data:image/png;base64,AAAA"}],
    )
    assert "transient_attachments" not in result
```

If there is no persistence helper yet, write the test for the helper you intend to extract first.

- [ ] **Step 2: Run the targeted backend tests to verify they fail**

Run: `python -m unittest tests.test_chat_runtime -v`

Expected: FAIL because transient attachments are not handled yet.

- [ ] **Step 3: Add one thin transient-image path**

Implement the smallest clean refactor in `backend/chat.py`:

1. Add a helper that builds the persisted user message without transient attachment data.
2. Add a helper that builds the provider-turn user message with both:
   - `attached_material_ids`
   - `transient_attachments`
3. Reuse existing image `data_url` injection shape for transient images.
4. Do **not** touch `materials.json` or `SkillEngine` for transient images.

Shape to aim for:

```python
def _build_persisted_user_message(self, user_message: str, attached_material_ids: list[str]) -> dict:
    return {
        "role": "user",
        "content": user_message,
        "attached_material_ids": attached_material_ids,
    }
```

Then pass a separate provider-only object into `_to_provider_message(...)`.

- [ ] **Step 4: Ensure chat and chat_stream both use the same rules**

Both methods must:

1. Persist only text + material IDs
2. Build provider conversation with transient images included
3. Leave subsequent turns unaffected by old transient images

- [ ] **Step 5: Run the targeted backend tests to verify they pass**

Run: `python -m unittest tests.test_chat_runtime -v`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/chat.py tests/test_chat_runtime.py
git commit -m "feat: support transient images in chat runtime"
```

## Task 3: Add Frontend Helpers For Pending Attachments And IME-Safe Send Rules

**Files:**
- Create: `frontend/src/utils/pendingAttachments.js`
- Create: `frontend/src/utils/composerInputBehavior.js`
- Modify: `frontend/src/utils/chatMaterials.js`
- Create: `frontend/tests/pendingAttachments.test.mjs`
- Create: `frontend/tests/composerInputBehavior.test.mjs`
- Modify: `frontend/tests/chatMaterials.test.mjs`

- [ ] **Step 1: Write failing helper tests for queue classification**

Add tests like:

```javascript
test("buildPendingAttachment marks images as ephemeral", () => {
  const file = new File(["x"], "bug.png", { type: "image/png" });
  const item = buildPendingAttachment(file);
  assert.equal(item.kind, "image");
  assert.equal(item.deliveryMode, "ephemeral");
});

test("buildPendingAttachment marks documents as persist", () => {
  const file = new File(["x"], "纪要.docx", { type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" });
  const item = buildPendingAttachment(file);
  assert.equal(item.kind, "document");
  assert.equal(item.deliveryMode, "persist");
});
```

- [ ] **Step 2: Write failing helper tests for IME-safe key behavior**

```javascript
test("Enter submits only when not composing and Shift is not pressed", () => {
  assert.equal(shouldSubmitComposerKeydown({ key: "Enter", shiftKey: false, isComposing: false }), true);
  assert.equal(shouldSubmitComposerKeydown({ key: "Enter", shiftKey: false, isComposing: true }), false);
  assert.equal(shouldSubmitComposerKeydown({ key: "Enter", shiftKey: true, isComposing: false }), false);
});
```

- [ ] **Step 3: Write the failing request-shape test**

Extend `frontend/tests/chatMaterials.test.mjs`:

```javascript
test("buildChatRequest includes transient attachments when provided", () => {
  assert.deepEqual(
    buildChatRequest({
      projectId: "proj-1",
      messageText: "请看截图",
      attachedMaterialIds: ["mat-1"],
      transientAttachments: [{ name: "bug.png", mime_type: "image/png", data_url: "data:image/png;base64,AAAA" }],
    }),
    {
      project_id: "proj-1",
      message_text: "请看截图",
      attached_material_ids: ["mat-1"],
      transient_attachments: [{ name: "bug.png", mime_type: "image/png", data_url: "data:image/png;base64,AAAA" }],
    },
  );
});
```

- [ ] **Step 4: Run the frontend helper tests to verify they fail**

Run:

```bash
node --test frontend/tests/pendingAttachments.test.mjs frontend/tests/composerInputBehavior.test.mjs frontend/tests/chatMaterials.test.mjs
```

Expected: FAIL because the new helper files and request fields do not exist yet.

- [ ] **Step 5: Implement the minimal pure helper layer**

In `frontend/src/utils/pendingAttachments.js`, add focused helpers:

```javascript
export function buildPendingAttachment(file) { ... }
export function mergePendingAttachments(existing = [], incoming = []) { ... }
export function removePendingAttachment(pending = [], attachmentId) { ... }
export async function fileToDataUrl(file) { ... }
export function splitPendingAttachments(pending = []) { ... } // images vs documents
```

In `frontend/src/utils/composerInputBehavior.js`, add:

```javascript
export function shouldSubmitComposerKeydown({ key, shiftKey, isComposing }) {
  return key === "Enter" && !shiftKey && !isComposing;
}
```

Extend `buildChatRequest(...)` to accept `transientAttachments`.

- [ ] **Step 6: Run the frontend helper tests to verify they pass**

Run:

```bash
node --test frontend/tests/pendingAttachments.test.mjs frontend/tests/composerInputBehavior.test.mjs frontend/tests/chatMaterials.test.mjs
```

Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add frontend/src/utils/pendingAttachments.js frontend/src/utils/composerInputBehavior.js frontend/src/utils/chatMaterials.js frontend/tests/pendingAttachments.test.mjs frontend/tests/composerInputBehavior.test.mjs frontend/tests/chatMaterials.test.mjs
git commit -m "feat: add pending attachment helper layer"
```

## Task 4: Integrate The New Composer Into ChatPanel

**Files:**
- Modify: `frontend/src/components/ChatPanel.jsx`

- [ ] **Step 1: Add the failing UI-facing utility expectations first**

Before wiring the component, extend helper tests to cover retry semantics:

```javascript
test("splitPendingAttachments separates transient images from document uploads", () => {
  const bug = { id: "p1", kind: "image", deliveryMode: "ephemeral" };
  const memo = { id: "p2", kind: "document", deliveryMode: "persist" };
  assert.deepEqual(splitPendingAttachments([bug, memo]), {
    transientImages: [bug],
    persistentDocuments: [memo],
  });
});

test("mixed paste keeps text and still queues image attachments when a project is active", () => {
  // Cover the helper or extracted branch that parses clipboard content so
  // implementation cannot accidentally drop plain text while enqueuing images.
});
```

- [ ] **Step 2: Run the helper tests again**

Run:

```bash
node --test frontend/tests/pendingAttachments.test.mjs frontend/tests/composerInputBehavior.test.mjs frontend/tests/chatMaterials.test.mjs
```

Expected: PASS before touching `ChatPanel`, so the component can be wired against stable helpers.

- [ ] **Step 3: Replace the single-line input with a textarea composer**

In `frontend/src/components/ChatPanel.jsx`:

1. Replace the single-line `<input>` with a `<textarea>`.
2. Add a ref-based auto-resize effect capped at ~6 lines.
3. Use `shouldSubmitComposerKeydown(...)` for `Enter` vs `Shift+Enter`.
4. Preserve current send/stop button behavior.

- [ ] **Step 4: Add pending attachment state and UI**

Still in `ChatPanel.jsx`:

1. Add `pendingAttachments` state.
2. Change `+`, drag/drop, and paste to enqueue pending items instead of uploading immediately.
3. Render image cards above the textarea with thumbnails and a `本轮临时` label.
4. Render compact document cards with icon, name, and a `发送前入库` label.
5. Keep the existing project-material chips below that as a separate concept.
6. With no `projectId`, disable `+` and drag/drop entry entirely.
7. With no `projectId`, allow pasted text through but ignore pasted files/images and show one short toast.
8. With a `projectId`, mixed clipboard paste must preserve plain text in the textarea while enqueueing image/file clipboard items into the pending queue.

- [ ] **Step 5: Implement the send-time branching**

On send:

1. Split pending items into transient images vs persistent docs.
2. If transient images exist but `supportsImageAttachments(settings)` is false, stop immediately, show the existing model-capability error, and keep the full pending queue untouched.
3. Upload docs first through the existing materials endpoint.
4. Immediately merge the returned material objects into the local materials list via `onMaterialsMerged(...)`, not just their IDs.
5. Merge uploaded document IDs into selected material IDs so a retry can reuse them without re-uploading.
6. Convert images to `transient_attachments`.
7. If any `fileToDataUrl(...)` conversion fails, abort send and keep the full pending queue untouched.
8. Build one request payload with both attachment channels.
9. Clear pending queue only after success.

- [ ] **Step 6: Implement the failure semantics exactly as specified**

If document upload fails:

1. Abort send
2. Keep the full pending queue

If transient image conversion fails:

1. Abort send
2. Keep the full pending queue

If chat fails after document upload succeeds:

1. Keep transient image items in the pending queue
2. Remove already-uploaded document items from the pending queue
3. Keep their new material IDs selected for retry
4. Keep their returned material objects in the local materials list so the selected chips still render correctly
5. Do not upload those documents again on the next retry

If paste happens with no project:

1. Let text through
2. Ignore image/file clipboard items
3. Show one short toast

If paste happens with a project and contains both text and image data:

1. Keep the text in the textarea
2. Enqueue the image into the pending queue
3. Do not `preventDefault` in a way that drops the plain-text portion

- [ ] **Step 7: Run frontend tests and build**

Run:

```bash
node --test frontend/tests/*.test.mjs
cd frontend && npm run build
```

Expected:

1. All frontend tests PASS
2. Build PASS
3. Only the existing Vite chunk-size warning is acceptable

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/ChatPanel.jsx
git commit -m "feat: add multiline composer with pending attachments"
```

## Task 5: Full Regression Pass And Docs Sync

**Files:**
- Modify: `docs/current-worklist.md`

- [ ] **Step 1: Update the short-term worklist**

Mark the input-box work item as in progress or completed, and record the settled design decisions:

1. `Enter` / `Shift+Enter`
2. `A` layout
3. images transient
4. documents pre-uploaded

- [ ] **Step 2: Run the full backend suite**

Run:

```bash
python -m unittest discover -s tests -v
```

Expected: PASS

- [ ] **Step 3: Run the full frontend suite**

Run:

```bash
node --test frontend/tests/*.test.mjs
```

Expected: PASS

- [ ] **Step 4: Run the frontend production build**

Run:

```bash
cd frontend && npm run build
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add docs/current-worklist.md
git commit -m "docs: update composer attachment work status"
```

## Final Verification Checklist

- [ ] Multiline input grows to ~6 lines, then scrolls
- [ ] `Enter` sends only when not composing
- [ ] `Shift+Enter` inserts newline
- [ ] IME composing state never triggers accidental send
- [ ] `Ctrl+V`, `+`, and drag/drop all enter the pending queue first
- [ ] With a project, mixed paste keeps text and also enqueues image/file clipboard items
- [ ] With no project, `+` and drag/drop are disabled
- [ ] With no project, pasted text still works while pasted attachments are ignored with a toast
- [ ] Image cards show thumbnails and `本轮临时`
- [ ] Document cards show icon and `发送前入库`
- [ ] Unsupported-image models are blocked before the request is sent
- [ ] Document uploads happen before chat
- [ ] Uploaded document objects are merged into the local materials list immediately
- [ ] Chat failure does not duplicate already-uploaded docs
- [ ] Successful send leaves the composer clean; new documents stay in the materials list but are not preselected for the next turn
- [ ] Transient images never enter project materials
- [ ] Non-image `transient_attachments` are rejected explicitly instead of silently drifting through
- [ ] Full backend tests pass
- [ ] Full frontend tests pass
- [ ] Frontend build passes
