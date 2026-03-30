# Context Policy And Compression Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hard-coded `128k` context handling with model-aware context tiers, dynamic compression thresholds, honest usage reporting, and a simple custom override input.

**Architecture:** Keep the existing `FastAPI + React` desktop shape, but extract model/context knowledge into one small backend resolver instead of scattering special cases across `config.py`, `chat.py`, and the frontend. Chat runtime consumes a per-request `ResolvedContextPolicy`, settings persist only one optional custom override, and the frontend renders `estimated` vs `actual` usage honestly without pretending every model has the same limit.

**Tech Stack:** Python 3.12, FastAPI, OpenAI-compatible client SDK, React 18, axios, Python `unittest`, Node 20 built-in test runner

---

## Scope Note

This plan covers one subsystem only: context-window resolution, compression thresholds, and usage display. It does **not** include unrelated UI cleanup, stage sync fixes, or input-box improvements.

Spec reference for this plan:

- `docs/superpowers/specs/2026-03-30-context-policy-and-compression-design.md`

## File Structure

### New files to create

- `backend/context_policy.py`
  Responsibility: normalize model IDs, map models to context tiers, apply manual overrides, and calculate resolved context policies.
- `tests/test_context_policy.py`
  Responsibility: cover exact model matches, family fallbacks, unknown fallback, and manual override clamping.
- `frontend/src/utils/contextUsage.js`
  Responsibility: format usage payloads into UI-friendly labels and progress values.
- `frontend/tests/contextUsage.test.mjs`
  Responsibility: verify estimated/actual labels, progress ratios, and compressed badges.

### Existing files to modify

- `backend/config.py`
  Responsibility: persist the custom context override while keeping old config files readable.
- `backend/chat.py`
  Responsibility: resolve runtime context policy, estimate prompt size correctly, compress against dynamic thresholds, and return richer usage payloads.
- `backend/main.py`
  Responsibility: expose the new settings field through the existing settings API.
- `backend/models.py`
  Responsibility: update API models for richer usage payloads.
- `tests/test_config.py`
  Responsibility: prove the new override field loads, saves, and coexists with old config files.
- `tests/test_settings_api.py`
  Responsibility: prove the settings API round-trips the override field safely.
- `tests/test_chat_runtime.py`
  Responsibility: cover dynamic threshold use, image estimation, and usage-mode semantics.
- `tests/test_main_api.py`
  Responsibility: cover the richer usage payload shape from `/api/chat`.
- `tests/test_stream_api.py`
  Responsibility: cover the richer usage payload shape from `/api/chat/stream`.
- `frontend/src/components/SettingsModal.jsx`
  Responsibility: render one advanced numeric override input for custom API mode.
- `frontend/src/components/ChatPanel.jsx`
  Responsibility: render the new context usage bar and labels.
- `frontend/src/utils/connectionMode.js`
  Responsibility: keep connection-mode copy aligned if wording shifts during implementation.
- `frontend/tests/connectionMode.test.mjs`
  Responsibility: catch user-facing copy regressions tied to connection mode wording.
- `docs/current-worklist.md`
  Responsibility: track that the context/compression work now has a spec and plan.

## Task 1: Add A Dedicated Context Policy Resolver

**Files:**
- Create: `backend/context_policy.py`
- Create: `tests/test_context_policy.py`

- [ ] **Step 1: Write the failing resolver tests**

```python
import unittest

from backend.context_policy import resolve_context_policy


class ContextPolicyTests(unittest.TestCase):
    def test_exact_match_for_managed_gemini_uses_1m_provider_and_500k_effective(self):
        policy = resolve_context_policy("gemini-3-flash")
        self.assertEqual(policy.provider_context_limit, 1_000_000)
        self.assertEqual(policy.effective_context_limit, 500_000)
        self.assertEqual(policy.resolution_source, "exact_match")

    def test_vendor_prefixed_model_is_normalized_before_lookup(self):
        policy = resolve_context_policy("moonshotai/Kimi-K2.5")
        self.assertEqual(policy.normalized_model, "kimi-k2.5")
        self.assertEqual(policy.provider_context_limit, 256_000)

    def test_family_fallback_handles_gpt_5_4(self):
        policy = resolve_context_policy("gpt-5.4")
        self.assertEqual(policy.provider_context_limit, 400_000)
        self.assertEqual(policy.effective_context_limit, 320_000)
        self.assertEqual(policy.resolution_source, "family_fallback")

    def test_unknown_model_falls_back_to_128k_tier(self):
        policy = resolve_context_policy("totally-unknown-model")
        self.assertEqual(policy.provider_context_limit, 128_000)
        self.assertEqual(policy.effective_context_limit, 110_000)
        self.assertEqual(policy.resolution_source, "unknown_fallback")

    def test_manual_override_is_clamped_to_provider_limit(self):
        policy = resolve_context_policy("gpt-5.2", custom_effective_limit=900_000)
        self.assertEqual(policy.provider_context_limit, 400_000)
        self.assertEqual(policy.effective_context_limit, 400_000)
        self.assertEqual(policy.resolution_source, "manual_override")
```

- [ ] **Step 2: Run the resolver tests to verify they fail**

Run: `python -m unittest tests.test_context_policy -v`

Expected: FAIL because `backend.context_policy` does not exist yet.

- [ ] **Step 3: Implement the resolver with tier-based rules**

Create `backend/context_policy.py` with focused data structures:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class ResolvedContextPolicy:
    normalized_model: str
    provider_context_limit: int
    effective_context_limit: int
    reserved_output_tokens: int
    compress_threshold: int
    resolution_source: str


def normalize_model_name(model_name: str) -> str:
    normalized = (model_name or "").strip().lower()
    if "/" in normalized:
        normalized = normalized.split("/", 1)[1]
    return normalized
```

Implementation rules:

1. Keep one small exact-match table for the first batch of known models.
2. Keep one small family fallback table for `gpt-5*`, `gemini-3*`, `claude-*`, `grok-4.1*`.
3. Use fallback `provider=128_000`, `effective=110_000`.
4. If `custom_effective_limit` is present, clamp it to `provider_context_limit`.
5. Centralize threshold math in this file, not in `chat.py`.

- [ ] **Step 4: Add one policy-construction helper for the math**

Keep threshold math in one place:

```python
def build_context_policy(normalized_model: str, provider_limit: int, effective_limit: int, source: str):
    reserved_output_tokens = min(8192, max(2048, int(effective_limit * 0.2)))
    compress_threshold = min(int(effective_limit * 0.9), effective_limit - reserved_output_tokens)
    return ResolvedContextPolicy(
        normalized_model=normalized_model,
        provider_context_limit=provider_limit,
        effective_context_limit=effective_limit,
        reserved_output_tokens=reserved_output_tokens,
        compress_threshold=compress_threshold,
        resolution_source=source,
    )
```

- [ ] **Step 5: Run the resolver tests again**

Run: `python -m unittest tests.test_context_policy -v`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/context_policy.py tests/test_context_policy.py
git commit -m "feat: add model-aware context policy resolver"
```

## Task 2: Persist The Custom Override Without Breaking Old Configs

**Files:**
- Modify: `backend/config.py`
- Modify: `backend/main.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_settings_api.py`

- [ ] **Step 1: Write the failing config and settings API tests**

Add this case to `tests/test_config.py`:

```python
def test_save_and_load_preserves_custom_context_limit_override(self):
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir)
        settings = Settings(
            mode="custom",
            custom_api_base="https://custom.example/v1",
            custom_api_key="secret",
            custom_model="gpt-5.2",
            custom_context_limit_override=200000,
        )
        with mock.patch("backend.config.get_user_config_dir", return_value=config_dir):
            save_settings(settings)
            loaded = load_settings()
    self.assertEqual(loaded.custom_context_limit_override, 200000)
```

Add this case to `tests/test_settings_api.py`:

```python
def test_get_settings_includes_custom_context_limit_override(self):
    main_module.settings.mode = "custom"
    main_module.settings.custom_context_limit_override = 200000
    response = self.client.get("/api/settings")
    self.assertEqual(response.status_code, 200)
    self.assertEqual(response.json()["custom_context_limit_override"], 200000)
```

- [ ] **Step 2: Run the config and settings tests to verify they fail**

Run: `python -m unittest tests.test_config tests.test_settings_api -v`

Expected: FAIL with missing field / unexpected response shape.

- [ ] **Step 3: Add the new persisted field with backward-compatible defaults**

In `backend/config.py`, add only one new persisted field:

```python
custom_context_limit_override: int | None = None
```

Update `normalize_settings_payload()`:

```python
normalized.setdefault("custom_context_limit_override", None)
```

Do not introduce a separate boolean like `custom_context_auto_detect`; empty override already means auto-detect.

- [ ] **Step 4: Expose the field through `/api/settings`**

Extend `SettingsUpdate` in `backend/main.py`:

```python
class SettingsUpdate(BaseModel):
    mode: Literal["managed", "custom"]
    managed_base_url: str
    managed_model: str
    custom_api_base: str = ""
    custom_api_key: str = ""
    custom_model: str = ""
    custom_context_limit_override: int | None = None
```

Persist it during updates:

```python
settings.custom_context_limit_override = update.custom_context_limit_override
```

Keep API key masking behavior unchanged.

- [ ] **Step 5: Run the config and settings tests again**

Run: `python -m unittest tests.test_config tests.test_settings_api -v`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/config.py backend/main.py tests/test_config.py tests/test_settings_api.py
git commit -m "feat: persist custom context override in settings"
```

## Task 3: Switch Chat Runtime To Dynamic Context Budgets

**Files:**
- Modify: `backend/chat.py`
- Modify: `backend/models.py`
- Modify: `tests/test_chat_runtime.py`
- Modify: `tests/test_main_api.py`
- Modify: `tests/test_stream_api.py`

- [ ] **Step 1: Write the failing runtime tests**

Add one usage-shape test to `tests/test_chat_runtime.py`:

```python
@mock.patch("backend.chat.OpenAI")
def test_chat_uses_managed_policy_effective_limit_in_usage_payload(self, mock_openai):
    settings = Settings(
        mode="managed",
        managed_base_url="https://newapi.z0y0h.work/client/v1",
        managed_model="gemini-3-flash",
        model="gemini-3-flash",
        projects_dir=Path(tempfile.gettempdir()) / "dummy-projects",
        skill_dir=self.repo_skill_dir,
    )
    handler = ChatHandler(settings, SkillEngine(settings.projects_dir, self.repo_skill_dir))
    usage = handler._build_usage_payload(current_tokens=120000, compressed=False, usage_mode="estimated")
    self.assertEqual(usage["effective_max_tokens"], 500000)
    self.assertEqual(usage["provider_max_tokens"], 1000000)
```

Add one active-model-source test:

```python
@mock.patch("backend.chat.OpenAI")
def test_get_active_model_name_prefers_mode_specific_model_over_empty_runtime_alias(self, mock_openai):
    settings = Settings(
        mode="managed",
        managed_model="gemini-3-flash",
        custom_model="gpt-5.2",
        model="",
        projects_dir=Path(tempfile.gettempdir()) / "dummy-projects",
        skill_dir=self.repo_skill_dir,
    )
    handler = ChatHandler(settings, SkillEngine(settings.projects_dir, self.repo_skill_dir))
    self.assertEqual(handler._get_active_model_name(), "gemini-3-flash")
```

Add one image-estimation test:

```python
@mock.patch("backend.chat.OpenAI")
def test_estimate_tokens_does_not_count_full_base64_for_images(self, mock_openai):
    settings = Settings(...)
    handler = ChatHandler(settings, SkillEngine(settings.projects_dir, self.repo_skill_dir))
    estimated = handler._estimate_tokens([
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "请阅读这张图"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + ("A" * 50000)}},
            ],
        }
    ])
    self.assertLess(estimated, 5000)
```

Add one re-estimation / hard-stop test:

```python
@mock.patch("backend.chat.OpenAI")
def test_fit_conversation_within_budget_raises_when_current_turn_alone_exceeds_budget(self, mock_openai):
    settings = Settings(
        mode="custom",
        custom_model="unknown-model",
        custom_context_limit_override=4096,
        projects_dir=Path(tempfile.gettempdir()) / "dummy-projects",
        skill_dir=self.repo_skill_dir,
    )
    handler = ChatHandler(settings, SkillEngine(settings.projects_dir, self.repo_skill_dir))
    policy = handler._resolve_context_policy()
    conversation = [
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": "A" * 4000},
    ]
    with self.assertRaises(ConversationBudgetError):
        handler._fit_conversation_within_budget(conversation, policy)
```

Update both `tests/test_main_api.py` and `tests/test_stream_api.py` to assert:

```python
{
    "current_tokens": 1200,
    "effective_max_tokens": 500000,
    "provider_max_tokens": 1000000,
    "compressed": False,
    "usage_mode": "estimated",
}
```

- [ ] **Step 2: Run the runtime and API tests to verify they fail**

Run: `python -m unittest tests.test_chat_runtime tests.test_main_api tests.test_stream_api -v`

Expected: FAIL because the runtime still emits `max_tokens`, still uses global `settings.context_window`, and still estimates image tokens from raw JSON length.

- [ ] **Step 3: Resolve a context policy at the start of each chat turn**

In `backend/chat.py`, add:

```python
from .context_policy import resolve_context_policy


def _get_active_model_name(self) -> str:
    if self.settings.mode == "managed":
        return (self.settings.managed_model or self.settings.model or "").strip()
    return (self.settings.custom_model or self.settings.model or "").strip()


def _resolve_context_policy(self):
    override = self.settings.custom_context_limit_override if self.settings.mode == "custom" else None
    return resolve_context_policy(self._get_active_model_name(), custom_effective_limit=override)
```

Use `_get_active_model_name()` both for context policy resolution and for the outgoing `client.chat.completions.create(... model=...)` call so direct `ChatHandler` construction in tests cannot silently fall back to `unknown_fallback`.

- [ ] **Step 4: Replace the hard-coded threshold and usage fields**

Remove runtime dependence on:

- `self.settings.context_window`
- `self.settings.compress_threshold`

Use:

```python
policy = self._resolve_context_policy()
if estimated > policy.compress_threshold:
    conversation = self._compress_conversation(conversation, policy)
```

and build usage from one helper:

```python
def _build_usage_payload(self, current_tokens: int, compressed: bool, usage_mode: str):
    policy = self._resolve_context_policy()
    return {
        "current_tokens": current_tokens,
        "effective_max_tokens": policy.effective_context_limit,
        "provider_max_tokens": policy.provider_context_limit,
        "compressed": compressed,
        "usage_mode": usage_mode,
    }
```

Then make the over-budget flow explicit:

1. Estimate the full conversation before sending.
2. If over threshold, compress old history.
3. Re-estimate after compression.
4. If still over threshold, continue shrinking old history or summary payload.
5. If the current turn itself already exceeds the effective budget even after old history is minimized, return a clear error telling the user to缩小消息或附件范围 instead of continuing to retry.

- [ ] **Step 5: Make image estimation honest**

Refactor `_estimate_tokens()` so list content is handled item-by-item:

```python
def _estimate_content_tokens(self, content) -> int:
    if isinstance(content, str):
        return self._estimate_text_tokens(content)
    if isinstance(content, list):
        total = 0
        for item in content:
            if item.get("type") == "text":
                total += self._estimate_text_tokens(item.get("text", ""))
            elif item.get("type") == "image_url":
                total += 1200
        return total
    return self._estimate_text_tokens(json.dumps(content, ensure_ascii=False))
```

Do not count the full base64 payload.

- [ ] **Step 6: Add a bounded compression loop instead of one-shot compression**

Refactor the runtime path so compression is not “compress once and hope”:

```python
def _fit_conversation_within_budget(self, conversation, policy):
    current = conversation
    for _ in range(3):
        estimated = self._estimate_tokens(current)
        if estimated <= policy.compress_threshold:
            return current, estimated, False
        current = self._compress_conversation(current, policy)
    raise ConversationBudgetError("当前消息或附件过大，请缩小范围后重试。")
```

Requirements:

1. Re-estimate after every compression pass.
2. Bound the loop so it cannot spin forever.
3. Surface a user-readable error when the current turn itself is too large.

- [ ] **Step 7: Update the API response model**

In `backend/models.py`, change `TokenUsage` to:

```python
class TokenUsage(BaseModel):
    current_tokens: int = 0
    effective_max_tokens: int = 128000
    provider_max_tokens: int = 128000
    compressed: bool = False
    usage_mode: str = "estimated"
```

Do not leave mixed field names behind.

- [ ] **Step 8: Preserve honest `actual` vs `estimated` semantics**

If upstream usage exists on non-streaming calls, emit `usage_mode="actual"`.
If it is absent, or the call is streaming and only local estimation is available, emit `usage_mode="estimated"`.

- [ ] **Step 9: Run the runtime and API tests again**

Run: `python -m unittest tests.test_context_policy tests.test_chat_runtime tests.test_main_api tests.test_stream_api -v`

Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add backend/chat.py backend/models.py tests/test_chat_runtime.py tests/test_main_api.py tests/test_stream_api.py
git commit -m "feat: drive chat compression from runtime context policies"
```

## Task 4: Add The Advanced Override Input And Honest Usage Labels

**Files:**
- Create: `frontend/src/utils/contextUsage.js`
- Create: `frontend/tests/contextUsage.test.mjs`
- Modify: `frontend/src/components/SettingsModal.jsx`
- Modify: `frontend/src/components/ChatPanel.jsx`
- Modify: `frontend/src/utils/connectionMode.js`
- Modify: `frontend/tests/connectionMode.test.mjs`

- [ ] **Step 1: Write the failing frontend helper tests**

Create `frontend/tests/contextUsage.test.mjs`:

```javascript
import test from "node:test";
import assert from "node:assert/strict";

import { formatContextUsage, getContextUsagePercent } from "../src/utils/contextUsage.js";

test("formatContextUsage marks estimated payloads clearly", () => {
  assert.deepEqual(
    formatContextUsage({
      current_tokens: 132000,
      effective_max_tokens: 500000,
      usage_mode: "estimated",
      compressed: true,
    }),
    {
      label: "上下文估算",
      detail: "132k / 500k",
      modeTag: "估算",
      compressedTag: "已压缩",
    },
  );
});

test("getContextUsagePercent clamps at 100", () => {
  assert.equal(
    getContextUsagePercent({ current_tokens: 800000, effective_max_tokens: 500000 }),
    100,
  );
});
```

- [ ] **Step 2: Run the frontend helper tests to verify they fail**

Run: `node --test frontend/tests/contextUsage.test.mjs`

Expected: FAIL because `contextUsage.js` does not exist.

- [ ] **Step 3: Implement one pure formatter for the token bar**

Create `frontend/src/utils/contextUsage.js`:

```javascript
export function getContextUsagePercent(tokenUsage = {}) {
  const max = tokenUsage.effective_max_tokens || 0;
  const current = tokenUsage.current_tokens || 0;
  if (!max) return 0;
  return Math.min(100, (current / max) * 100);
}

export function formatContextUsage(tokenUsage = {}) {
  const mode = tokenUsage.usage_mode === "actual" ? "actual" : "estimated";
  return {
    label: mode === "actual" ? "上下文用量" : "上下文估算",
    detail: `${Math.round((tokenUsage.current_tokens || 0) / 1000)}k / ${Math.round((tokenUsage.effective_max_tokens || 0) / 1000)}k`,
    modeTag: mode === "actual" ? "实际" : "估算",
    compressedTag: tokenUsage.compressed ? "已压缩" : "",
  };
}
```

- [ ] **Step 4: Add one advanced numeric input in `SettingsModal.jsx`**

In custom mode only, add:

```javascript
<label className="block text-sm text-[#8f93c9] mb-1">有效上下文上限（高级）</label>
<input
  type="number"
  min="4096"
  step="1000"
  value={form.custom_context_limit_override ?? ""}
  onChange={e => setForm(prev => ({
    ...prev,
    custom_context_limit_override: e.target.value ? Number(e.target.value) : null,
  }))}
  placeholder="留空按模型自动识别"
  className="w-full bg-[#16163a] border border-[#3a3a5a] text-[#e2e2f0] rounded px-3 py-2 mb-2"
/>
```

Helper copy:

```text
留空表示自动识别；填写后只覆盖本地压缩预算，不改变上游模型真实能力。
```

Do not add a second toggle. Empty is auto-detect.

- [ ] **Step 5: Update `ChatPanel.jsx` to render the richer usage payload**

Use the helper instead of formatting inline:

```javascript
const usageDisplay = formatContextUsage(tokenUsage);
const usagePercent = getContextUsagePercent(tokenUsage);
```

UI requirements:

1. Progress bar width uses `effective_max_tokens`.
2. Text shows `上下文估算` or `上下文用量`.
3. Badges show `估算` / `实际` and `已压缩`.
4. Do not reference the removed `max_tokens` field anywhere.

- [ ] **Step 6: Keep connection-mode copy aligned with product wording**

If `connectionMode.js` text changes during implementation, update `frontend/tests/connectionMode.test.mjs` in the same task. The managed copy should remain neutral, not “推荐”.

- [ ] **Step 7: Run frontend tests and build**

Run: `node --test frontend/tests/contextUsage.test.mjs frontend/tests/connectionMode.test.mjs`

Expected: PASS

Run: `cd frontend && npm run build`

Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add frontend/src/utils/contextUsage.js frontend/tests/contextUsage.test.mjs frontend/src/components/SettingsModal.jsx frontend/src/components/ChatPanel.jsx frontend/src/utils/connectionMode.js frontend/tests/connectionMode.test.mjs
git commit -m "feat: surface context usage and custom override in the UI"
```

## Task 5: Update Project Tracking And Run Cross-Layer Verification

**Files:**
- Modify: `docs/current-worklist.md`

- [ ] **Step 1: Update the worklist entry**

Keep the item marked as `已规划`, and add both document paths:

```markdown
- 状态：`已规划`
- 设计稿：`docs/superpowers/specs/2026-03-30-context-policy-and-compression-design.md`
- 计划：`docs/superpowers/plans/2026-03-30-context-policy-and-compression.md`
```

- [ ] **Step 2: Run the focused backend verification**

Run: `python -m unittest tests.test_context_policy tests.test_config tests.test_settings_api tests.test_chat_runtime tests.test_main_api tests.test_stream_api -v`

Expected: PASS

- [ ] **Step 3: Run the focused frontend verification**

Run: `node --test frontend/tests/contextUsage.test.mjs frontend/tests/connectionMode.test.mjs`

Expected: PASS

Run: `cd frontend && npm run build`

Expected: PASS

- [ ] **Step 4: Manual smoke test**

Run:

```bash
python app.py
```

Manual checks:

1. 默认通道下聊天底部显示 `500k` 有效上限，而不是 `128k`
2. 自定义 API 设置里可以留空高级项，也可以填写数值
3. 未知自定义模型不崩溃，按保守上限工作
4. 图片材料不会导致上下文条瞬间异常爆满
5. `usage_mode` 为估算时 UI 显示 `估算`，而不是假装真实

- [ ] **Step 5: Commit**

```bash
git add docs/current-worklist.md
git commit -m "docs: track context policy rollout"
```

## Final Verification Checklist

- [ ] 默认通道 `gemini-3-flash` 显示为 `500k` 有效上限
- [ ] 动态压缩阈值不再依赖写死 `60000`
- [ ] 未知模型回退到 `128k/110k` 档位
- [ ] 自定义 API 支持手动覆盖有效上下文上限
- [ ] usage 载荷返回 `effective_max_tokens` 和 `provider_max_tokens`
- [ ] UI 明确区分 `估算` 与 `实际`
- [ ] 图片预算不再按 base64 全量估算

## Handoff Notes

1. 不要把这次改造扩展成“重做记忆系统”；这次只修预算和压缩门槛。
2. 前后端都不要再读取旧的 `max_tokens` 字段作为真相，新字段落地后应统一迁移到 `effective_max_tokens`。
3. 如果遇到某个网关模型名频繁识别失败，优先补精确映射或家族规则，不要临时塞 if/else 特判到 `chat.py`。
4. 若上游没有返回 usage，不要造假；保持 `estimated` 并把表达做好。
