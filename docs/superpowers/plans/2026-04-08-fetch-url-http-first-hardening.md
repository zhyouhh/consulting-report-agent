# Fetch URL HTTP-First Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade `fetch_url` into a more reliable HTTP-first reader with safe redirects, better decoding, clearer failure types, and session-scoped caching while keeping the public tool interface unchanged.

**Architecture:** Keep `ChatHandler._fetch_url` as the only tool entrypoint, but refactor its internals inside `backend/chat.py` into a staged pipeline: URL normalization, redirect-safe requests, byte decoding, page classification, extraction, truncation, and typed result building. Drive the work through end-to-end runtime tests in `tests/test_chat_runtime.py`, using mocked `requests.get` responses so the external `fetch_url(url)` contract stays stable.

**Tech Stack:** Python, `requests`, existing `trafilatura`, stdlib `re`/`html`/`html.parser`/`urllib.parse`, `pytest` for the existing unittest-style test suite.

---

## File Map

- Modify: `backend/chat.py`
  - Keep the tool schema and tool name unchanged.
  - Replace the current one-shot fetch logic with staged private helpers for normalization, safe redirects, decoding, readable extraction, classification, truncation, and cache policy.
  - Add in-memory cache state on `ChatHandler`, scoped by project id plus normalized URL plus request mode (normal HTTPS path vs. controlled HTTP fallback path).
- Modify: `tests/test_chat_runtime.py`
  - Add reusable fake-response helpers for HTML/text payloads, redirects, encoding cases, and cache-policy tests.
  - Add focused regression tests for response contract, redirect rules, decode rules, classification rules, truncation, and negative-cache behavior.
- Do not add new modules unless `backend/chat.py` becomes impossible to keep readable during implementation. If extraction is needed, stop and make one fetch-specific helper module only.
- Do not commit during execution unless the human explicitly asks; the repository is already dirty with unrelated local work.

Implementation note: reuse the existing temp-project setup style already present in `tests/test_chat_runtime.py` (`with tempfile.TemporaryDirectory()`, `SkillEngine.create_project(...)`, `self._make_tool_call(...)`). Do not invent a second fixture system unless the repeated setup becomes unmaintainable.

### Task 1: Lock the External Contract with Failing Tests

**Files:**
- Modify: `tests/test_chat_runtime.py`
- Modify: `backend/chat.py`

- [ ] **Step 1: Add a reusable fake fetch response helper in the runtime tests**

```python
def _make_fetch_response(
    self,
    *,
    url: str,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
    body: bytes = b"",
):
    response = mock.Mock()
    response.url = url
    response.status_code = status_code
    response.headers = headers or {}
    response.encoding = None
    response.apparent_encoding = "utf-8"
    response.iter_content.return_value = [body]
    response.close.return_value = None
    return response

def _allow_public_fetch_host(self, mock_getaddrinfo, host: str = "example.com", ip: str = "93.184.216.34"):
    mock_getaddrinfo.return_value = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)),
    ]
```

- [ ] **Step 2: Write a failing success-contract test**

```python
@mock.patch("backend.chat.OpenAI")
@mock.patch("backend.chat.requests.get")
@mock.patch("backend.chat.socket.getaddrinfo")
def test_fetch_url_success_preserves_url_and_adds_final_url(self, mock_getaddrinfo, mock_get, mock_openai):
    self._allow_public_fetch_host(mock_getaddrinfo)
    mock_get.return_value = self._make_fetch_response(
        url="https://example.com/final",
        headers={"Content-Type": "text/html; charset=utf-8"},
        body=b"<html><head><title>Example</title></head><body><article>Hello world.</article></body></html>",
    )
    handler = ChatHandler(self._make_settings(), SkillEngine(...))
    result = handler._execute_tool(..., self._make_tool_call("fetch_url", '{"url":"https://example.com/start"}'))
    self.assertEqual(result["status"], "success")
    self.assertEqual(result["url"], "https://example.com/final")
    self.assertEqual(result["final_url"], "https://example.com/final")
    self.assertEqual(result["content_type"], "text/html")
    self.assertNotIn("error_type", result)
```

- [ ] **Step 3: Write failing redirect-policy tests**

```python
def test_fetch_url_allows_same_host_redirect(...):
    mock_get.side_effect = [
        self._make_fetch_response(
            url="https://example.com/start",
            status_code=302,
            headers={"Location": "/final", "Content-Type": "text/html"},
        ),
        self._make_fetch_response(
            url="https://example.com/final",
            headers={"Content-Type": "text/html; charset=utf-8"},
            body=b"<html><body><article>Readable body.</article></body></html>",
        ),
    ]
    ...
    self.assertEqual(result["final_url"], "https://example.com/final")

def test_fetch_url_allows_www_bare_domain_redirect(...):
    mock_get.side_effect = [
        self._make_fetch_response(
            url="https://example.com/start",
            status_code=302,
            headers={"Location": "https://www.example.com/final", "Content-Type": "text/html"},
        ),
        self._make_fetch_response(
            url="https://www.example.com/final",
            headers={"Content-Type": "text/html; charset=utf-8"},
            body=b"<html><body><article>Readable body.</article></body></html>",
        ),
    ]
    ...
    self.assertEqual(result["final_url"], "https://www.example.com/final")

def test_fetch_url_blocks_cross_host_redirect(...):
    mock_get.return_value = self._make_fetch_response(
        url="https://example.com/start",
        status_code=302,
        headers={"Location": "https://evil.example.net/final", "Content-Type": "text/html"},
    )
    ...
    self.assertEqual(result["status"], "error")
    self.assertEqual(result["error_type"], "redirect_blocked")

def test_fetch_url_rejects_redirect_loop_or_limit(...):
    mock_get.side_effect = [
        self._make_fetch_response(
            url=f"https://example.com/{index}",
            status_code=302,
            headers={"Location": f"/{index + 1}", "Content-Type": "text/html"},
        )
        for index in range(ChatHandler.FETCH_URL_MAX_REDIRECTS + 1)
    ]
    ...
    self.assertEqual(result["error_type"], "redirect_limit_exceeded")
```

- [ ] **Step 4: Write failing scheme-upgrade and controlled-fallback tests**

```python
@mock.patch("backend.chat.OpenAI")
@mock.patch("backend.chat.requests.get")
@mock.patch("backend.chat.socket.getaddrinfo")
def test_fetch_url_upgrades_http_to_https_first(self, mock_getaddrinfo, mock_get, mock_openai):
    self._allow_public_fetch_host(mock_getaddrinfo, host="example.com")
    mock_get.return_value = self._make_fetch_response(
        url="https://example.com/page",
        headers={"Content-Type": "text/html; charset=utf-8"},
        body=b"<html><body><article>Secure body.</article></body></html>",
    )
    ...
    first_requested_url = mock_get.call_args_list[0].args[0]
    self.assertEqual(first_requested_url, "https://example.com/page")

@mock.patch("backend.chat.OpenAI")
@mock.patch("backend.chat.requests.get")
@mock.patch("backend.chat.socket.getaddrinfo")
def test_fetch_url_falls_back_to_http_only_for_tls_failure(self, mock_getaddrinfo, mock_get, mock_openai):
    self._allow_public_fetch_host(mock_getaddrinfo, host="example.com")
    mock_get.side_effect = [
        requests.exceptions.SSLError("tls failed"),
        self._make_fetch_response(
            url="http://example.com/page",
            headers={"Content-Type": "text/html; charset=utf-8"},
            body=b"<html><body><article>HTTP fallback body.</article></body></html>",
        ),
    ]
    ...
    requested_urls = [call.args[0] for call in mock_get.call_args_list]
    self.assertEqual(requested_urls, ["https://example.com/page", "http://example.com/page"])
```

- [ ] **Step 5: Write failing size-policy tests**

```python
def test_fetch_url_rejects_response_body_over_hard_limit(...):
    mock_get.return_value = self._make_fetch_response(
        url="https://example.com/huge",
        headers={"Content-Type": "text/plain; charset=utf-8"},
        body=b"x" * (ChatHandler.FETCH_URL_MAX_BYTES + 1),
    )
    ...
    self.assertEqual(result["error_type"], "response_too_large")

def test_fetch_url_truncates_extracted_text_but_still_succeeds(...):
    html = ("<html><body><article>" + ("hello " * 4000) + "</article></body></html>").encode()
    mock_get.return_value = self._make_fetch_response(
        url="https://example.com/long",
        headers={"Content-Type": "text/html; charset=utf-8"},
        body=html,
    )
    ...
    self.assertTrue(result["truncated"])
```

- [ ] **Step 6: Run the focused tests to verify RED**

Run: `python -m pytest tests/test_chat_runtime.py -k "fetch_url_success_preserves_url or fetch_url_allows_same_host_redirect or fetch_url_allows_www_bare_domain_redirect or fetch_url_blocks_cross_host_redirect or fetch_url_rejects_redirect_loop_or_limit or fetch_url_upgrades_http_to_https_first or fetch_url_falls_back_to_http_only_for_tls_failure or fetch_url_rejects_response_body_over_hard_limit or fetch_url_truncates_extracted_text_but_still_succeeds" -q`

Expected: multiple FAIL results because `final_url`, `content_type`, typed errors, `http -> https` upgrade, controlled `http` fallback, redirect policies, and hard-limit behavior are not implemented yet.

- [ ] **Step 7: Implement the minimal request/contract scaffolding in `backend/chat.py`**

```python
FETCH_URL_TIMEOUT_SECONDS = 20
FETCH_URL_MAX_REDIRECTS = 5
FETCH_URL_SUCCESS_CACHE_TTL_SECONDS = 900
FETCH_URL_NEGATIVE_CACHE_TTL_SECONDS = 60
FETCH_URL_BROWSER_HEADERS = {
    "User-Agent": "...Chrome...",
    "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1",
    "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate",
}

def _fetch_url(self, project_id: str, url: str) -> Dict[str, str | bool]:
    normalized = self._normalize_fetch_url(url)
    cached = self._get_cached_fetch_result(project_id, normalized, request_mode="https_first")
    if cached:
        return cached
    response_state = self._request_fetch_url(project_id, normalized)
    ...
```

- [ ] **Step 8: Re-run the focused tests to verify the first GREEN**

Run: `python -m pytest tests/test_chat_runtime.py -k "fetch_url_success_preserves_url or fetch_url_allows_same_host_redirect or fetch_url_allows_www_bare_domain_redirect or fetch_url_blocks_cross_host_redirect or fetch_url_rejects_redirect_loop_or_limit or fetch_url_upgrades_http_to_https_first or fetch_url_falls_back_to_http_only_for_tls_failure or fetch_url_rejects_response_body_over_hard_limit or fetch_url_truncates_extracted_text_but_still_succeeds" -q`

Expected: PASS for the new request/redirect/contract tests, even if decode/classification cases are still pending.

### Task 2: Drive Decode and Classification Behavior with Failing Tests

**Files:**
- Modify: `tests/test_chat_runtime.py`
- Modify: `backend/chat.py`

- [ ] **Step 1: Write a failing `meta charset` decode test for a Chinese page**

```python
def test_fetch_url_decodes_meta_charset_gb18030_html(...):
    body = (
        b'<html><head><meta charset="gb18030"><title>\xd5\xfe\xb2\xdf</title></head>'
        b'<body><article>\xd6\xd0\xb9\xfa\xbe\xad\xbc\xc3\xb7\xa2\xd5\xb9</article></body></html>'
    )
    mock_get.return_value = self._make_fetch_response(
        url="https://gov.example.cn/policy",
        headers={"Content-Type": "text/html"},
        body=body,
    )
    ...
    self.assertEqual(result["status"], "success")
    self.assertIn("中国经济发展", result["content"])
```

- [ ] **Step 2: Write failing non-readable-page classification tests**

```python
def test_fetch_url_classifies_challenge_page(...):
    body = b"<html><title>Just a moment...</title><body>cf-mitigated challenge ray id</body></html>"
    mock_get.return_value = self._make_fetch_response(
        url="https://blocked.example.com",
        status_code=403,
        headers={"Content-Type": "text/html", "cf-mitigated": "challenge"},
        body=body,
    )
    ...
    self.assertEqual(result["error_type"], "challenge_page")

def test_fetch_url_classifies_baidu_shell_as_non_readable(...):
    body = b"<html><title>\xe7\x99\xbe\xe5\xba\xa6\xe5\xae\x89\xe5\x85\xa8\xe9\xaa\x8c\xe8\xaf\x81</title><body>访问过于频繁，请稍后再试<script>location.href='/index/'</script></body></html>"
    mock_get.return_value = self._make_fetch_response(
        url="https://baike.baidu.com/item/demo",
        headers={"Content-Type": "text/html; charset=utf-8"},
        body=body,
    )
    ...
    self.assertEqual(result["error_type"], "non_readable_page")
```

- [ ] **Step 3: Write failing cache-policy tests**

```python
def test_fetch_url_caches_success_within_same_project(...):
    mock_get.return_value = self._make_fetch_response(...)
    first = handler._execute_tool(...)
    second = handler._execute_tool(...)
    self.assertEqual(first["status"], "success")
    self.assertEqual(second["status"], "success")
    self.assertEqual(mock_get.call_count, 1)

def test_fetch_url_does_not_negative_cache_403(...):
    mock_get.return_value = self._make_fetch_response(
        url="https://blocked.example.com",
        status_code=403,
        headers={"Content-Type": "text/html"},
        body=b"<html><body>Forbidden</body></html>",
    )
    first = handler._execute_tool(...)
    second = handler._execute_tool(...)
    self.assertEqual(first["error_type"], "http_status_403")
    self.assertEqual(second["error_type"], "http_status_403")
    self.assertEqual(mock_get.call_count, 2)

def test_fetch_url_negative_caches_404(...):
    mock_get.return_value = self._make_fetch_response(
        url="https://example.com/missing",
        status_code=404,
        headers={"Content-Type": "text/html"},
        body=b"<html><body>missing</body></html>",
    )
    first = handler._execute_tool(project["id"], self._make_tool_call("fetch_url", '{"url":"https://example.com/missing"}'))
    second = handler._execute_tool(project["id"], self._make_tool_call("fetch_url", '{"url":"https://example.com/missing"}'))
    self.assertEqual(first["error_type"], "http_status_404")
    self.assertEqual(second["error_type"], "http_status_404")
    self.assertEqual(mock_get.call_count, 1)

def test_fetch_url_negative_caches_redirect_blocked(...):
    mock_get.return_value = self._make_fetch_response(
        url="https://example.com/start",
        status_code=302,
        headers={"Location": "https://other.example.org/final", "Content-Type": "text/html"},
    )
    first = handler._execute_tool(project["id"], self._make_tool_call("fetch_url", '{"url":"https://example.com/start"}'))
    second = handler._execute_tool(project["id"], self._make_tool_call("fetch_url", '{"url":"https://example.com/start"}'))
    self.assertEqual(first["error_type"], "redirect_blocked")
    self.assertEqual(second["error_type"], "redirect_blocked")
    self.assertEqual(mock_get.call_count, 1)

def test_fetch_url_cache_is_scoped_per_project_id(...):
    mock_get.return_value = self._make_fetch_response(...)
    first = handler._execute_tool(project_a["id"], ...)
    second = handler._execute_tool(project_b["id"], ...)
    self.assertEqual(first["status"], "success")
    self.assertEqual(second["status"], "success")
    self.assertEqual(mock_get.call_count, 2)

def test_fetch_url_cache_separates_http_fallback_mode(...):
    mock_get.side_effect = [
        requests.exceptions.SSLError("tls failed"),
        self._make_fetch_response(
            url="http://example.com/page",
            headers={"Content-Type": "text/html; charset=utf-8"},
            body=b"<html><body><article>HTTP fallback body.</article></body></html>",
        ),
        self._make_fetch_response(
            url="https://example.com/page",
            headers={"Content-Type": "text/html; charset=utf-8"},
            body=b"<html><body><article>HTTPS body.</article></body></html>",
        ),
    ]
    first = handler._execute_tool(project["id"], self._make_tool_call("fetch_url", '{"url":"http://example.com/page"}'))
    second = handler._execute_tool(project["id"], self._make_tool_call("fetch_url", '{"url":"https://example.com/page"}'))
    self.assertIn("HTTP fallback", first["content"])
    self.assertIn("HTTPS body", second["content"])
    self.assertEqual(mock_get.call_count, 3)
```

- [ ] **Step 4: Run the second focused test batch to verify RED**

Run: `python -m pytest tests/test_chat_runtime.py -k "meta_charset_gb18030 or challenge_page or baidu_shell_as_non_readable or caches_success_within_same_project or does_not_negative_cache_403 or negative_caches_404 or negative_caches_redirect_blocked or cache_is_scoped_per_project_id or cache_separates_http_fallback_mode" -q`

Expected: FAIL because the current code decodes too early, lacks typed classification, and has no cache policy.

- [ ] **Step 5: Implement byte decoding, classification, and cache policy in `backend/chat.py`**

```python
def _read_response_bytes(self, response) -> tuple[bytes, bool]:
    ...

def _detect_fetch_encoding(self, response, body: bytes) -> str | None:
    # BOM -> header charset -> meta charset -> apparent encoding -> Chinese fallbacks
    ...

def _decode_fetch_body(self, response, body: bytes) -> str:
    ...

def _classify_fetch_response(self, status_code: int, headers: Mapping[str, str], text: str) -> str | None:
    # return "challenge_page", "non_readable_page", "http_status_403", ...
    ...

def _should_cache_fetch_error(self, error_type: str) -> bool:
    return error_type in {"http_status_404", "redirect_blocked", "unsupported_content_type", "response_too_large"}
```

- [ ] **Step 6: Re-run the second focused test batch to verify GREEN**

Run: `python -m pytest tests/test_chat_runtime.py -k "meta_charset_gb18030 or challenge_page or baidu_shell_as_non_readable or caches_success_within_same_project or does_not_negative_cache_403 or negative_caches_404 or negative_caches_redirect_blocked or cache_is_scoped_per_project_id or cache_separates_http_fallback_mode" -q`

Expected: PASS for the new tests, with request counts proving cache policy is correct.

### Task 3: Tighten Extraction and Readability Thresholds

**Files:**
- Modify: `tests/test_chat_runtime.py`
- Modify: `backend/chat.py`

- [ ] **Step 1: Write failing fallback-extraction tests**

```python
def test_fetch_url_falls_back_when_trafilatura_returns_empty(...):
    html = b"<html><body><main><h1>Title</h1><p>Paragraph one.</p><p>Paragraph two.</p></main></body></html>"
    mock_get.return_value = self._make_fetch_response(
        url="https://example.com/fallback",
        headers={"Content-Type": "text/html; charset=utf-8"},
        body=html,
    )
    with mock.patch("trafilatura.extract", return_value=""):
        result = handler._execute_tool(...)
    self.assertEqual(result["status"], "success")
    self.assertIn("Paragraph one.", result["content"])
```

- [ ] **Step 2: Write failing plain-text and unsupported-content tests**

```python
def test_fetch_url_returns_plain_text_verbatim(...):
    mock_get.return_value = self._make_fetch_response(
        url="https://example.com/readme.txt",
        headers={"Content-Type": "text/plain; charset=utf-8"},
        body=b"line one\nline two\n",
    )
    ...
    self.assertEqual(result["content"], "line one\nline two")

def test_fetch_url_rejects_pdf_with_typed_error(...):
    mock_get.return_value = self._make_fetch_response(
        url="https://example.com/file.pdf",
        headers={"Content-Type": "application/pdf"},
        body=b"%PDF-1.7",
    )
    ...
    self.assertEqual(result["error_type"], "unsupported_content_type")
```

- [ ] **Step 3: Run the extraction-focused test batch to verify RED**

Run: `python -m pytest tests/test_chat_runtime.py -k "falls_back_when_trafilatura_returns_empty or returns_plain_text_verbatim or rejects_pdf_with_typed_error" -q`

Expected: FAIL because the current fallback is only naive tag stripping and the current error contract is untyped.

- [ ] **Step 4: Implement the extraction helpers and readability gate**

```python
def _extract_readable_text(self, html_text: str) -> str:
    extracted = self._extract_trafilatura_text(html_text)
    if self._is_readable_content(extracted):
        return extracted
    fallback = self._extract_fallback_html_text(html_text)
    if self._is_readable_content(fallback):
        return fallback
    return ""

def _extract_fallback_html_text(self, html_text: str) -> str:
    # strip script/style/noscript/template, prefer article/main/body, collapse whitespace
    ...
```

- [ ] **Step 5: Re-run the extraction-focused test batch to verify GREEN**

Run: `python -m pytest tests/test_chat_runtime.py -k "falls_back_when_trafilatura_returns_empty or returns_plain_text_verbatim or rejects_pdf_with_typed_error" -q`

Expected: PASS, with fallback extraction only used when `trafilatura` yields empty or clearly low-quality text.

### Task 4: Regress the Chat Runtime and Review the Diff

**Files:**
- Modify: `backend/chat.py`
- Modify: `tests/test_chat_runtime.py`

- [ ] **Step 1: Run the full fetch-related runtime slice**

Run: `python -m pytest tests/test_chat_runtime.py -k "fetch_url or requires_fetch_url" -q`

Expected: PASS, including the earlier gate tests that rely on successful `fetch_url` calls unlocking formal writes only after success.

- [ ] **Step 2: Run the broader regression slice touched by earlier local runtime fixes**

Run: `python -m pytest tests/test_chat_runtime.py tests/test_chat_context.py tests/test_skill_engine.py -q`

Expected: PASS, proving the fetch changes did not break the existing write-gate runtime work already in the dirty tree.

- [ ] **Step 3: Do one manual smoke fetch from the terminal after tests are green**

Run:

```powershell
@'
from pathlib import Path
from backend.chat import ChatHandler
from backend.config import Settings
from backend.skill import SkillEngine

settings = Settings(
    mode="managed",
    managed_base_url="https://example.com/v1",
    managed_model="dummy",
    projects_dir=Path(r"D:\CodexProject\Consult report\consulting-report-agent\tmp-projects"),
    skill_dir=Path(r"D:\CodexProject\Consult report\consulting-report-agent\skill"),
)
handler = ChatHandler(settings, SkillEngine(settings.projects_dir, settings.skill_dir))
print(handler._fetch_url("manual-project", "https://docs.python.org/3/"))
'@ | python -
```

Expected: `status=success`, non-empty `title/content`, and both `url` plus `final_url` present. If environment setup blocks this smoke test, document that explicitly rather than guessing.

- [ ] **Step 4: Review the final diff for scope control**

Checklist:

```text
- only fetch/runtime code changed
- no unrelated write-gate logic was reverted
- success results keep `url` and add `final_url`/`content_type`
- classified failures always include `error_type`
- 403/challenge/decode failures are not negative-cached
```

- [ ] **Step 5: Stop before commit unless the human explicitly asks for it**

```bash
git status --short
```

Expected: only the intended fetch-plan/runtime test changes remain staged or unstaged; do not create a commit proactively in this dirty repository.
