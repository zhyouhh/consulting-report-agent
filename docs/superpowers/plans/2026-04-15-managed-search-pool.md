# Managed Search Pool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current single-backend SearXNG `web_search` path with a bundled managed search pool (`Serper`, `Brave`, `Tavily`, `Exa`) that uses weighted routing, caching, throttling, cooldowns, and a last-resort native provider fallback while keeping the existing `web_search(query)` contract intact.

**Architecture:** Add three focused backend modules: one for provider adapters, one for runtime state/cache persistence, and one for routing/policy. Keep `backend/chat.py` as the only tool entrypoint, but make `_web_search()` a thin wrapper around the new router and a tightly-scoped native fallback helper. Bundle the private search-pool JSON file the same way the managed client token is bundled today, but keep the secret file out of Git and out of user-facing settings.

**Tech Stack:** Python, `requests`, stdlib `json`/`time`/`hashlib`/`pathlib`/`urllib.parse`, existing `openai` SDK for optional native fallback, PyInstaller spec plumbing, `pytest`/`unittest.mock`.

---

## File Map

- Create: `backend/search_state.py`
  - Query normalization, L1 in-memory cache, L2 file cache persistence, provider cooldown state, and lightweight counters.
- Create: `backend/search_providers.py`
  - Thin HTTP adapters for `Serper`, `Brave`, `Tavily`, and `Exa`.
- Create: `backend/search_pool.py`
  - Weighted layered routing, provider selection, fallback policy, and unified result formatting helpers.
- Modify: `backend/chat.py`
  - Keep `web_search` tool schema unchanged.
  - Replace direct SearXNG calls inside `_web_search()` with the new router.
  - Add one native-search helper used only as the last fallback when explicitly supported.
- Modify: `backend/config.py`
  - Add helper paths/constants for `managed_search_pool.json`, `search_runtime_state.json`, and `search_cache.json`.
  - Parse `managed_search_pool.json` into runtime config objects so routing and limits are file-driven, not hardcoded.
  - Support a private file override via `CONSULTING_REPORT_MANAGED_SEARCH_POOL_FILE`.
- Modify: `build_support.py`
  - Validate that `managed_search_pool.json` exists, is non-empty, and has minimally valid JSON structure for enabled providers.
- Modify: `build.bat`
  - Accept `managed_search_pool.json` from the repo root or from `CONSULTING_REPORT_MANAGED_SEARCH_POOL_FILE`.
  - Copy temporary injected files into place before PyInstaller and clean them after the build.
- Modify: `consulting_report.spec`
  - Require `managed_search_pool.json` and include it in bundle data.
- Modify: `.gitignore`
  - Ignore `managed_search_pool.json`.
- Modify: `BUILD.md`
  - Document how to carry and inject the managed search pool file and where runtime state/cache files live.
- Modify: `WINDOWS_BUILD.md`
  - Mirror the build/runtime storage guidance for Windows packaging.
- Modify: `tests/test_build_support.py`
  - Add bundle-validation coverage for the search-pool file.
- Modify: `tests/test_packaging_spec.py`
  - Verify the active PyInstaller spec bundles `managed_search_pool.json`.
- Modify: `tests/test_packaging_docs.py`
  - Verify build docs mention the new file and runtime state/cache storage.
- Modify: `tests/test_config.py`
  - Cover new path helpers and private file override behavior.
- Create: `tests/test_search_state.py`
  - Cover query normalization, cache TTL, project scoping, and cooldown persistence.
- Create: `tests/test_search_providers.py`
  - Cover response parsing and provider-specific error mapping.
- Create: `tests/test_search_pool.py`
  - Cover weighted layered routing, cooldown skip, throttling, cache hit reuse, and native fallback triggering.
- Modify: `tests/test_chat_runtime.py`
  - Preserve the current `web_search` runtime contract while validating structured extras and fallback behavior.

Implementation note: phase 1 native fallback should only support providers we can positively identify as OpenAI `Responses API` + built-in `web_search` capable. Do **not** try to “maybe support” Gemini/newapi native search in this pass. Unsupported paths must fail cleanly and let the router continue to “额度已用尽”.

Implementation note: use `consulting_report.spec` as the single source of truth for packaging because `build.bat` and the packaging tests already target it. Do not widen scope to `build.spec` unless a failing test proves it is still part of the real build path.

### Task 1: Lock Bundle Plumbing and Runtime Search-Pool Config with RED Tests

**Files:**
- Modify: `build_support.py`
- Modify: `backend/config.py`
- Modify: `build.bat`
- Modify: `consulting_report.spec`
- Modify: `.gitignore`
- Modify: `tests/test_build_support.py`
- Modify: `tests/test_packaging_spec.py`
- Modify: `tests/test_packaging_docs.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Add failing bundle-validation tests for `managed_search_pool.json`**

```python
def test_require_non_empty_bundle_json_file_rejects_missing_search_pool(self):
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        with self.assertRaises(FileNotFoundError):
            validate_bundle_managed_search_pool(root, "managed_search_pool.json")

def test_validate_bundle_managed_search_pool_accepts_enabled_provider_with_key(self):
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "managed_search_pool.json").write_text(
            json.dumps({
                "version": 1,
                "providers": {
                    "serper": {"enabled": True, "api_key": "k", "weight": 5}
                },
                "routing": {"primary": ["serper"], "secondary": [], "native_fallback": True},
                "limits": {"per_turn_searches": 2}
            }),
            encoding="utf-8",
        )
        resolved = validate_bundle_managed_search_pool(root, "managed_search_pool.json")
        self.assertTrue(resolved.exists())

def test_validate_bundle_managed_search_pool_rejects_enabled_provider_without_key(self):
    ...
```

- [ ] **Step 2: Add failing config-loader tests for routing, limits, and provider policy fields**

```python
def test_get_managed_search_pool_path_prefers_env_override_file(self):
    with tempfile.TemporaryDirectory() as tmpdir:
        override = Path(tmpdir) / "portable-search-pool.json"
        override.write_text('{"version":1,"providers":{},"routing":{},"limits":{}}', encoding="utf-8")
        with mock.patch.dict("os.environ", {"CONSULTING_REPORT_MANAGED_SEARCH_POOL_FILE": str(override)}):
            self.assertEqual(get_managed_search_pool_path(), override)

def test_load_managed_search_pool_config_reads_routing_and_limits(self):
    with tempfile.TemporaryDirectory() as tmpdir:
        bundle_dir = Path(tmpdir)
        (bundle_dir / "managed_search_pool.json").write_text(
            json.dumps({
                "version": 1,
                "providers": {
                    "serper": {
                        "enabled": True,
                        "api_key": "s",
                        "weight": 5,
                        "minute_limit": 60,
                        "daily_soft_limit": 1200,
                        "cooldown_seconds": 180,
                    },
                    "brave": {
                        "enabled": True,
                        "api_key": "b",
                        "weight": 3,
                        "minute_limit": 30,
                        "daily_soft_limit": 600,
                        "cooldown_seconds": 180,
                    },
                },
                "routing": {
                    "primary": ["serper", "brave"],
                    "secondary": [],
                    "native_fallback": True,
                },
                "limits": {
                    "per_turn_searches": 2,
                    "project_minute_limit": 10,
                    "global_minute_limit": 20,
                    "memory_cache_ttl_seconds": 21600,
                    "project_cache_ttl_seconds": 86400,
                },
            }),
            encoding="utf-8",
        )
        with mock.patch("backend.config.get_base_path", return_value=bundle_dir):
            cfg = load_managed_search_pool_config()
    self.assertEqual(cfg.routing.primary, ["serper", "brave"])
    self.assertEqual(cfg.providers["serper"].minute_limit, 60)
    self.assertEqual(cfg.limits.project_cache_ttl_seconds, 86400)

def test_load_managed_search_pool_config_rejects_unknown_routing_provider(self):
    ...

def test_get_search_runtime_state_path_uses_user_config_dir(self):
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir)
        with mock.patch("backend.config.get_user_config_dir", return_value=config_dir):
            self.assertEqual(get_search_runtime_state_path(), config_dir / "search_runtime_state.json")

def test_get_search_cache_path_uses_user_config_dir(self):
    ...
```

- [ ] **Step 3: Add failing packaging tests for the active spec and build docs**

```python
def test_consulting_report_spec_requires_managed_search_pool_file(self):
    content = (ROOT / "consulting_report.spec").read_text(encoding="utf-8")
    self.assertIn("managed_search_pool.json", content)
    self.assertIn("validate_bundle_managed_search_pool", content)

def test_build_docs_describe_search_pool_and_runtime_storage(self):
    for doc_name in ["BUILD.md", "WINDOWS_BUILD.md"]:
        content = (ROOT / doc_name).read_text(encoding="utf-8")
        self.assertIn("managed_search_pool.json", content)
        self.assertIn("search_runtime_state.json", content)
        self.assertIn("search_cache.json", content)
```

- [ ] **Step 4: Run the packaging/config slice to verify RED**

Run:

```bash
python -m pytest tests/test_build_support.py tests/test_packaging_spec.py tests/test_packaging_docs.py tests/test_config.py -q
```

Expected: FAIL because `managed_search_pool.json` helpers and bundle rules do not exist yet.

- [ ] **Step 5: Implement bundle validation and runtime config loading**

```python
# backend/config.py
MANAGED_SEARCH_POOL_FILENAME = "managed_search_pool.json"
SEARCH_RUNTIME_STATE_FILENAME = "search_runtime_state.json"
SEARCH_CACHE_FILENAME = "search_cache.json"

def get_managed_search_pool_path(base_path: Path | None = None) -> Path:
    override = os.getenv("CONSULTING_REPORT_MANAGED_SEARCH_POOL_FILE", "").strip()
    if override:
        return Path(override)
    runtime_base = base_path or get_base_path()
    return runtime_base / MANAGED_SEARCH_POOL_FILENAME

def get_search_runtime_state_path(config_dir: Path | None = None) -> Path:
    return (config_dir or get_user_config_dir()) / SEARCH_RUNTIME_STATE_FILENAME

def get_search_cache_path(config_dir: Path | None = None) -> Path:
    return (config_dir or get_user_config_dir()) / SEARCH_CACHE_FILENAME

@dataclass(frozen=True)
class ManagedSearchProviderConfig:
    enabled: bool
    api_key: str
    weight: int
    minute_limit: int
    daily_soft_limit: int
    cooldown_seconds: int

@dataclass(frozen=True)
class ManagedSearchPoolConfig:
    providers: dict[str, ManagedSearchProviderConfig]
    routing: ...
    limits: ...

def load_managed_search_pool_config(base_path: Path | None = None) -> ManagedSearchPoolConfig:
    payload = json.loads(get_managed_search_pool_path(base_path).read_text(encoding="utf-8"))
    ...
```

```python
# build_support.py
def validate_bundle_managed_search_pool(root: Path, filename: str) -> Path:
    path = require_non_empty_bundle_text_file(root, filename)
    payload = json.loads(path.read_text(encoding="utf-8"))
    providers = payload.get("providers") or {}
    enabled = [name for name, cfg in providers.items() if cfg.get("enabled")]
    if not enabled:
        raise ValueError("managed_search_pool.json 必须至少启用一个 provider。")
    for name in enabled:
        if not str(providers[name].get("api_key", "")).strip():
            raise ValueError(f"managed_search_pool.json 中 {name} 缺少 api_key。")
    if not (payload.get("routing") or {}).get("primary"):
        raise ValueError("managed_search_pool.json 必须声明 routing.primary。")
    if not (payload.get("limits") or {}).get("per_turn_searches"):
        raise ValueError("managed_search_pool.json 必须声明 limits.per_turn_searches。")
    return path
```

- [ ] **Step 6: Wire the file into the active build path and ignore list**

Implementation checklist:

```text
- add managed_search_pool.json to .gitignore
- in build.bat, support CONSULTING_REPORT_MANAGED_SEARCH_POOL_FILE as a source path
- if env var is set, copy it to managed_search_pool.json before build and delete temp copy after build
- in consulting_report.spec, validate and bundle managed_search_pool.json
- update BUILD.md and WINDOWS_BUILD.md to explain:
  - source file path
  - bundled file path
  - runtime state/cache file paths
```

- [ ] **Step 7: Re-run the packaging/config slice to verify GREEN**

Run:

```bash
python -m pytest tests/test_build_support.py tests/test_packaging_spec.py tests/test_packaging_docs.py tests/test_config.py -q
```

Expected: PASS, proving private bundle plumbing is locked before any search logic is added.

- [ ] **Step 8: Commit**

```bash
git add .gitignore build_support.py build.bat consulting_report.spec backend/config.py BUILD.md WINDOWS_BUILD.md tests/test_build_support.py tests/test_packaging_spec.py tests/test_packaging_docs.py tests/test_config.py
git commit -m "feat: add bundled managed search pool plumbing"
```

### Task 2: Build Search Runtime State and Cache Primitives via TDD

**Files:**
- Create: `backend/search_state.py`
- Create: `tests/test_search_state.py`
- Modify: `backend/config.py`

- [ ] **Step 1: Write failing tests for L1 in-memory cache and L2 file cache behavior**

```python
def test_normalize_query_collapses_spacing_and_case():
    assert normalize_search_query("  猪猪侠   2025！！ ") == "猪猪侠 2025"

def test_l1_memory_cache_hits_before_disk_lookup(tmp_path):
    store = SearchStateStore(runtime_state_path=tmp_path / "state.json", cache_path=tmp_path / "cache.json")
    store.put_cache("猪猪侠 2025", project_id="demo", value={"provider": "serper"}, memory_ttl_seconds=60, project_ttl_seconds=300)
    hit = store.get_cache("猪猪侠 2025", project_id="demo")
    assert hit["provider"] == "serper"

def test_l2_file_cache_backfills_l1_after_reload(tmp_path):
    first = SearchStateStore(runtime_state_path=tmp_path / "state.json", cache_path=tmp_path / "cache.json")
    first.put_cache("猪猪侠 2025", project_id="demo", value={"provider": "serper"}, memory_ttl_seconds=60, project_ttl_seconds=300)
    second = SearchStateStore(runtime_state_path=tmp_path / "state.json", cache_path=tmp_path / "cache.json")
    hit = second.get_cache("猪猪侠 2025", project_id="demo")
    assert hit["provider"] == "serper"
    assert second._memory_cache

def test_l1_cache_miss_after_memory_ttl_but_l2_still_hits(tmp_path):
    ...
```

- [ ] **Step 2: Write failing tests for project scoping and provider cooldowns**

```python
def test_cache_is_scoped_by_project_id(tmp_path):
    store = SearchStateStore(...)
    store.put_cache(
        "猪猪侠",
        project_id="a",
        value={"provider": "serper"},
        memory_ttl_seconds=60,
        project_ttl_seconds=300,
    )
    assert store.get_cache("猪猪侠", project_id="b") is None

def test_provider_cooldown_persists_to_runtime_state(tmp_path):
    store = SearchStateStore(...)
    store.mark_provider_failure("serper", error_type="rate_limited", cooldown_seconds=180)
    reloaded = SearchStateStore(...)
    assert reloaded.get_provider_state("serper").cooldown_until is not None

def test_mark_provider_success_clears_consecutive_failures(tmp_path):
    ...

def test_l2_file_cache_misses_after_project_ttl_expiry(tmp_path):
    ...
```

- [ ] **Step 3: Run the state slice to verify RED**

Run:

```bash
python -m pytest tests/test_search_state.py -q
```

Expected: FAIL because `SearchStateStore` and normalization helpers do not exist yet.

- [ ] **Step 4: Implement the minimal state store**

```python
@dataclass
class ProviderState:
    consecutive_failures: int = 0
    cooldown_until: float | None = None
    last_success_at: float | None = None
    last_error_type: str | None = None

class SearchStateStore:
    def __init__(self, runtime_state_path: Path, cache_path: Path):
        ...

    def get_cache(self, query: str, project_id: str) -> dict | None:
        ...

    def put_cache(
        self,
        query: str,
        project_id: str,
        value: dict,
        *,
        memory_ttl_seconds: int,
        project_ttl_seconds: int,
    ) -> None:
        ...

    def _get_memory_cache(self, query: str, project_id: str) -> dict | None:
        ...

    def _get_project_cache(self, query: str, project_id: str) -> dict | None:
        ...

    def mark_provider_failure(self, provider: str, error_type: str, cooldown_seconds: int) -> None:
        ...

    def mark_provider_success(self, provider: str) -> None:
        ...
```

- [ ] **Step 5: Re-run the state slice to verify GREEN**

Run:

```bash
python -m pytest tests/test_search_state.py -q
```

Expected: PASS, with file-backed state surviving a reload and cache keys normalized consistently.

- [ ] **Step 6: Commit**

```bash
git add backend/search_state.py tests/test_search_state.py backend/config.py
git commit -m "feat: add managed search runtime state store"
```

### Task 3: Add Provider Adapters and Normalize Their Error Semantics

**Files:**
- Create: `backend/search_providers.py`
- Create: `tests/test_search_providers.py`

- [ ] **Step 1: Write failing parsing tests for each provider**

```python
def test_serper_adapter_maps_organic_results():
    session = mock.Mock()
    session.get.return_value = mock.Mock(
        status_code=200,
        json=lambda: {"organic": [{"title": "猪猪侠", "snippet": "动画系列", "link": "https://example.com/a"}]},
    )
    adapter = SerperProvider(api_key="k", session=session)
    result = adapter.search("猪猪侠")
    assert result.items[0].title == "猪猪侠"

def test_tavily_adapter_maps_results():
    ...

def test_exa_adapter_maps_results():
    ...

def test_brave_adapter_maps_web_results():
    ...
```

- [ ] **Step 2: Write failing error-mapping tests**

```python
def test_provider_maps_429_to_rate_limited():
    session = mock.Mock()
    session.get.return_value = mock.Mock(status_code=429, text="too many requests")
    adapter = BraveProvider(api_key="k", session=session)
    with pytest.raises(SearchProviderError) as exc:
        adapter.search("猪猪侠")
    assert exc.value.error_type == "rate_limited"

def test_provider_maps_auth_error():
    ...

def test_provider_maps_empty_result_without_throwing():
    ...
```

- [ ] **Step 3: Run the adapter slice to verify RED**

Run:

```bash
python -m pytest tests/test_search_providers.py -q
```

Expected: FAIL because adapters and normalized result/error classes do not exist yet.

- [ ] **Step 4: Implement thin adapters with unified result objects**

```python
@dataclass
class SearchItem:
    title: str
    snippet: str
    url: str
    domain: str
    score: float

@dataclass
class ProviderSearchResult:
    provider: str
    items: list[SearchItem]

class SearchProviderError(RuntimeError):
    def __init__(self, provider: str, error_type: str, message: str):
        ...
```

Implementation checklist:

```text
- Serper: parse organic/link/snippet style payload
- Brave: parse web/results payload
- Tavily: parse results/title/content/url
- Exa: parse results/title/text/url or equivalent public API payload
- never log raw api_key values
```

- [ ] **Step 5: Re-run the adapter slice to verify GREEN**

Run:

```bash
python -m pytest tests/test_search_providers.py -q
```

Expected: PASS, with every provider yielding the same normalized item shape and error taxonomy.

- [ ] **Step 6: Commit**

```bash
git add backend/search_providers.py tests/test_search_providers.py
git commit -m "feat: add managed search provider adapters"
```

### Task 4: Add the Layered Router, Weighted Selection, and Native Fallback Gate

**Files:**
- Create: `backend/search_pool.py`
- Create: `tests/test_search_pool.py`
- Modify: `backend/search_state.py`
- Modify: `backend/config.py`

- [ ] **Step 1: Write failing tests for layered weighted routing**

```python
def test_router_prefers_primary_layer_when_available():
    router = SearchRouter(...)
    result = router.search("猪猪侠", project_id="demo")
    assert result.provider in {"serper", "brave"}

def test_router_skips_primary_provider_in_cooldown():
    state.mark_provider_failure("serper", "rate_limited", cooldown_seconds=180)
    result = router.search("猪猪侠", project_id="demo")
    assert result.provider == "brave"

def test_router_falls_to_secondary_layer_when_primary_layer_fails():
    ...
```

- [ ] **Step 2: Write failing tests for per-turn/per-project/global throttles**

```python
def test_router_blocks_after_two_searches_in_same_turn():
    ...
    third = router.search("第三次", project_id="demo", turn_search_count=2)
    assert third.error_type == "quota_exhausted"

def test_router_blocks_project_when_project_minute_limit_hit():
    ...

def test_router_blocks_globally_when_global_minute_limit_hit():
    ...
```

- [ ] **Step 3: Write failing tests for native fallback behavior**

```python
def test_router_uses_native_fallback_only_after_all_pool_providers_fail():
    native = mock.Mock(return_value=ProviderSearchResult(provider="native", items=[...]))
    result = router.search("猪猪侠", project_id="demo", native_search=native)
    assert result.provider == "native"
    native.assert_called_once()

def test_router_returns_quota_exhausted_when_native_is_unsupported():
    native = mock.Mock(return_value=None)
    result = router.search("猪猪侠", project_id="demo", native_search=native)
    assert result.error_type == "quota_exhausted"
```

- [ ] **Step 4: Run the router slice to verify RED**

Run:

```bash
python -m pytest tests/test_search_pool.py -q
```

Expected: FAIL because no router exists and no weighted layered policy is implemented yet.

- [ ] **Step 5: Implement the router with cache-first behavior**

```python
class SearchRouter:
    def search(self, query: str, *, project_id: str, turn_search_count: int, native_search: Callable[[str], ProviderSearchResult | None] | None):
        normalized = normalize_search_query(query)
        cached = self.state.get_cache(normalized, project_id=project_id)
        if cached:
            return self._from_cached(cached)
        provider = self._choose_provider(...)
        ...
```

Implementation checklist:

```text
- cache lookup before provider selection
- weighted candidate list within each layer
- cooldown skip before request dispatch
- mark success/failure in state store
- cache normalized successful results
- native fallback only after four managed providers fail
- one native attempt per router call
```

- [ ] **Step 6: Re-run the router slice to verify GREEN**

Run:

```bash
python -m pytest tests/test_search_pool.py -q
```

Expected: PASS, with deterministic behavior under mocked provider availability and state.

- [ ] **Step 7: Commit**

```bash
git add backend/search_pool.py tests/test_search_pool.py backend/search_state.py
git commit -m "feat: add layered managed search router"
```

### Task 5: Integrate the Router into Chat Runtime Without Breaking Gates

**Files:**
- Modify: `backend/chat.py`
- Modify: `tests/test_chat_runtime.py`
- Modify: `backend/config.py`

- [ ] **Step 1: Add failing runtime tests that preserve the old `web_search` contract**

```python
@mock.patch("backend.chat.OpenAI")
def test_web_search_returns_compatibility_text_and_provider_metadata(self, mock_openai):
    handler = ChatHandler(self._make_settings(...), SkillEngine(...))
    fake_router = mock.Mock()
    fake_router.search.return_value = {
        "status": "success",
        "provider": "serper",
        "cached": False,
        "native_fallback_used": False,
        "items": [
            {
                "title": "猪猪侠2025观察",
                "snippet": "授权与票房摘要",
                "url": "https://example.com/a",
                "domain": "example.com",
                "score": 0.9,
            }
        ],
        "results": "搜索结果：\n1. 猪猪侠2025观察\n授权与票房摘要\n链接: https://example.com/a",
    }
    with mock.patch("backend.chat.SearchRouter", return_value=fake_router):
        result = handler._web_search("猪猪侠 2025")
    self.assertEqual(result["status"], "success")
    self.assertEqual(result["provider"], "serper")
    self.assertIn("猪猪侠2025观察", result["results"])

def test_execute_tool_tracks_web_search_count_and_blocks_third_search_in_same_turn(...):
    handler = ChatHandler(self._make_settings(...), SkillEngine(...))
    handler._turn_context = {
        "can_write_non_plan": True,
        "web_search_disabled": False,
        "web_search_performed": False,
        "fetch_url_performed": False,
        "web_search_count": 2,
    }
    result = handler._execute_tool(project_id, self._make_tool_call("web_search", '{"query":"第三次"}'))
    self.assertEqual(result["status"], "error")
    self.assertIn("搜索额度已用尽", result["message"])
```

- [ ] **Step 2: Add failing tests for native fallback support gate**

```python
def test_native_search_helper_returns_none_when_model_is_not_supported(...):
    handler = ChatHandler(self._make_settings(mode="managed", managed_model="gemini-3-flash"), engine)
    self.assertIsNone(handler._search_with_native_provider("猪猪侠"))

def test_native_search_helper_uses_openai_responses_api_when_supported(...):
    mock_client = mock_openai.return_value
    mock_client.responses.create.return_value = mock.Mock(...)
    handler = ChatHandler(self._make_settings(mode="custom", custom_api_base="https://api.openai.com/v1", custom_model="gpt-5"), engine)
    result = handler._search_with_native_provider("OpenAI news")
    self.assertIsNotNone(result)
    mock_client.responses.create.assert_called_once()
```

- [ ] **Step 3: Run the runtime slice to verify RED**

Run:

```bash
python -m pytest tests/test_chat_runtime.py -k "web_search_returns_compatibility_text_and_provider_metadata or native_search_helper or web_search_count" -q
```

Expected: FAIL because `_web_search()` still hardcodes SearXNG and no native helper exists.

- [ ] **Step 4: Replace the direct SearXNG path with the router wrapper**

Implementation checklist:

```text
- keep the tool schema and the public tool name `web_search`
- instantiate SearchStateStore and SearchRouter lazily on the handler
- keep `results` as human-readable text for compatibility
- add `web_search_count` to every `_turn_context` initializer/reset dictionary in `backend/chat.py`
- count every accepted `web_search` tool invocation against the per-turn limit, including cache hits, because the goal is to bound search-tool churn as well as spend
- read the old value first: `current_count = self._turn_context["web_search_count"]`
- pass `current_count` unchanged into the router as `turn_search_count`
- do not pre-increment before the router call
- if the router returns the specific per-turn-limit rejection, leave `web_search_count` unchanged
- otherwise set `self._turn_context["web_search_count"] = current_count + 1`
- preserve `web_search_performed` behavior for downstream `fetch_url` gate
- implement _search_with_native_provider() using OpenAI Responses only when positively supported
- return None instead of throwing when native search is unsupported
```

- [ ] **Step 5: Re-run the runtime slice to verify GREEN**

Run:

```bash
python -m pytest tests/test_chat_runtime.py -k "web_search_returns_compatibility_text_and_provider_metadata or native_search_helper or web_search_count" -q
```

Expected: PASS, with `_web_search()` still returning compatibility text plus structured extras.

- [ ] **Step 6: Commit**

```bash
git add backend/chat.py tests/test_chat_runtime.py backend/config.py
git commit -m "feat: integrate managed search pool into chat runtime"
```

### Task 6: Update Build Docs and Run the Full Regression Slice

**Files:**
- Modify: `BUILD.md`
- Modify: `WINDOWS_BUILD.md`
- Modify: `tests/test_packaging_docs.py`
- Modify: `tests/test_chat_runtime.py`
- Modify: `tests/test_build_support.py`
- Modify: `tests/test_packaging_spec.py`
- Modify: `tests/test_search_state.py`
- Modify: `tests/test_search_providers.py`
- Modify: `tests/test_search_pool.py`

- [ ] **Step 1: Update build docs with the exact files the user must carry**

Doc checklist:

```text
- managed_search_pool.json source file lives in the repo root on the build machine
- bundled copy ships inside dist/咨询报告助手/
- runtime state lives in %USERPROFILE%\.consulting-report\search_runtime_state.json
- cache lives in %USERPROFILE%\.consulting-report\search_cache.json
- managed_search_pool.json must not be committed
```

- [ ] **Step 2: Run the focused new-test suites**

Run:

```bash
python -m pytest tests/test_search_state.py tests/test_search_providers.py tests/test_search_pool.py -q
```

Expected: PASS.

- [ ] **Step 3: Run the existing packaging and config regression slice**

Run:

```bash
python -m pytest tests/test_build_support.py tests/test_packaging_spec.py tests/test_packaging_docs.py tests/test_config.py -q
```

Expected: PASS.

- [ ] **Step 4: Run the existing chat/runtime regression slice most likely to catch breakage**

Run:

```bash
python -m pytest tests/test_chat_runtime.py tests/test_chat_context.py tests/test_settings_api.py tests/test_stream_api.py -q
```

Expected: PASS, proving `web_search` integration did not break tool dispatch, state handling, or SSE behavior.

- [ ] **Step 5: Run one end-to-end search routing smoke test with mocked providers**

Run:

```bash
python -m pytest tests/test_chat_runtime.py -k "web_search or requires_fetch_url" -q
```

Expected: PASS, including the existing gate that requires `fetch_url` before formal writing when web search has occurred.

- [ ] **Step 6: Review scope control before execution handoff**

Checklist:

```text
- only active packaging path (consulting_report.spec) changed
- managed_search_pool.json is ignored by Git
- no secrets are printed or hardcoded in repository files
- native fallback is last-resort only
- no frontend code changed
- current write/fetch gates are untouched except for preserving web_search_performed semantics
```

- [ ] **Step 7: Stop before merge decisions**

Run:

```bash
git status --short
```

Expected: only the intended search-pool, packaging, and doc changes are present.

- [ ] **Step 8: Commit**

```bash
git add backend/search_state.py backend/search_providers.py backend/search_pool.py backend/chat.py backend/config.py build_support.py build.bat consulting_report.spec .gitignore BUILD.md WINDOWS_BUILD.md tests/test_search_state.py tests/test_search_providers.py tests/test_search_pool.py tests/test_chat_runtime.py tests/test_build_support.py tests/test_packaging_spec.py tests/test_packaging_docs.py tests/test_config.py
git commit -m "feat: add managed search pool with layered fallback"
```
