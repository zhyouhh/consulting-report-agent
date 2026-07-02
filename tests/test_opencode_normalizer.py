import json
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from opencode_proxy.app import NormalizerSettings, create_app
from opencode_proxy.normalizer import normalize_sse


# 实测 opencode 当前畸形流（usage 挂在 finish_reason 块 + 私有块 + [DONE] 后多发块）
BROKEN_LINES = [
    'data: {"id":"x","object":"chat.completion.chunk","created":1,"model":"deepseek-v4-pro","choices":[{"index":0,"delta":{"role":"assistant","content":"你"}}]}',
    'data: {"id":"x","object":"chat.completion.chunk","created":1,"model":"deepseek-v4-pro","choices":[{"index":0,"delta":{"content":"好"}}]}',
    'data: {"id":"x","object":"chat.completion.chunk","created":1,"model":"deepseek-v4-pro","choices":[{"index":0,"finish_reason":"stop","delta":{"content":""}}],"usage":{"prompt_tokens":45196,"completion_tokens":100,"total_tokens":45296,"prompt_cache_hit_tokens":43648,"prompt_cache_miss_tokens":1548,"prompt_tokens_details":{"cached_tokens":43648}}}',
    'data: {"choices":[],"x-opencode-type":"inference-cost","cost":"0.001","normalizedUsage":{"inputTokens":45196,"cacheReadTokens":43648}}',
    'data: [DONE]',
    'data: {"choices":[],"cost":"0"}',
]


def _parse_frames(text):
    """把规范化后的 SSE 文本拆成 (data_objects, saw_done)。"""
    objs, saw_done = [], False
    for chunk in text.split("\n\n"):
        line = chunk.strip()
        if not line:
            continue
        assert line.startswith("data: "), line
        payload = line[len("data: "):]
        if payload == "[DONE]":
            saw_done = True
            continue
        objs.append(json.loads(payload))
    return objs, saw_done


class NormalizerCoreTests(unittest.TestCase):
    def test_broken_stream_usage_moved_to_empty_choices_chunk(self):
        text = "".join(normalize_sse(BROKEN_LINES))
        objs, done = _parse_frames(text)
        self.assertTrue(done)
        usage_chunks = [o for o in objs if o.get("usage")]
        self.assertEqual(len(usage_chunks), 1)
        self.assertEqual(usage_chunks[0]["choices"], [])
        self.assertEqual(usage_chunks[0]["usage"]["prompt_cache_hit_tokens"], 43648)
        self.assertEqual(usage_chunks[0]["usage"]["prompt_cache_miss_tokens"], 1548)

    def test_content_deltas_preserved_and_finish_usage_stripped(self):
        text = "".join(normalize_sse(BROKEN_LINES))
        objs, _ = _parse_frames(text)
        content = "".join(
            c.get("delta", {}).get("content") or ""
            for o in objs for c in (o.get("choices") or [])
        )
        self.assertEqual(content, "你好")
        finish = [o for o in objs if o.get("choices")
                  and o["choices"][0].get("finish_reason")]
        self.assertTrue(finish)
        self.assertNotIn("usage", finish[0])

    def test_private_and_post_done_chunks_dropped(self):
        text = "".join(normalize_sse(BROKEN_LINES))
        objs, _ = _parse_frames(text)
        self.assertFalse(any("x-opencode-type" in o for o in objs))
        # [DONE] 之后的 {"choices":[],"cost":"0"} 必须被丢弃
        self.assertFalse(any(o.get("cost") == "0" for o in objs))

    def test_already_standard_stream_is_idempotent(self):
        standard = [
            'data: {"id":"y","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"hi"}}]}',
            'data: {"id":"y","object":"chat.completion.chunk","choices":[{"index":0,"finish_reason":"stop","delta":{}}]}',
            'data: {"id":"y","object":"chat.completion.chunk","choices":[],"usage":{"prompt_tokens":10,"prompt_cache_hit_tokens":8,"prompt_cache_miss_tokens":2,"completion_tokens":3}}',
            'data: [DONE]',
        ]
        objs, done = _parse_frames("".join(normalize_sse(standard)))
        self.assertTrue(done)
        usage_chunks = [o for o in objs if o.get("usage")]
        self.assertEqual(len(usage_chunks), 1)
        self.assertEqual(usage_chunks[0]["usage"]["prompt_cache_hit_tokens"], 8)
        self.assertEqual(usage_chunks[0]["choices"], [])

    def test_no_upstream_usage_is_not_fabricated(self):
        only_content = [
            'data: {"choices":[{"index":0,"delta":{"content":"a"}}]}',
            'data: {"choices":[{"index":0,"finish_reason":"stop","delta":{}}]}',
            'data: [DONE]',
        ]
        objs, done = _parse_frames("".join(normalize_sse(only_content)))
        self.assertTrue(done)
        self.assertFalse(any(o.get("usage") for o in objs))

    def test_terminates_and_appends_done_even_without_upstream_done(self):
        no_done = [
            'data: {"choices":[{"index":0,"delta":{"content":"a"}}]}',
        ]
        text = "".join(normalize_sse(no_done))
        _, done = _parse_frames(text)
        self.assertTrue(done)


class _FakeUpstream:
    def __init__(self, *, status_code=200, content_type="text/event-stream",
                 lines=None, content=b""):
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        self._lines = lines or []
        self.content = content
        self.closed = False

    def iter_lines(self, decode_unicode=False):
        for line in self._lines:
            yield line

    def close(self):
        self.closed = True


class NormalizerAppTests(unittest.TestCase):
    def setUp(self):
        self.settings = NormalizerSettings(upstream_base_url="https://opencode.example/zen/go")
        self.client = TestClient(create_app(self.settings))

    def test_health(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["upstream"], "https://opencode.example/zen/go")

    @mock.patch("opencode_proxy.app.requests.request")
    def test_streaming_response_is_normalized(self, mock_request):
        mock_request.return_value = _FakeUpstream(lines=BROKEN_LINES)
        r = self.client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer opencode-key"},
            json={"model": "deepseek-v4-pro", "stream": True},
        )
        self.assertEqual(r.status_code, 200)
        objs, done = _parse_frames(r.text)
        self.assertTrue(done)
        usage_chunks = [o for o in objs if o.get("usage")]
        self.assertEqual(len(usage_chunks), 1)
        self.assertEqual(usage_chunks[0]["choices"], [])
        self.assertEqual(usage_chunks[0]["usage"]["prompt_cache_hit_tokens"], 43648)

    @mock.patch("opencode_proxy.app.requests.request")
    def test_forwards_upstream_url_and_authorization(self, mock_request):
        mock_request.return_value = _FakeUpstream(lines=["data: [DONE]"])
        self.client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer opencode-key"},
            json={"model": "deepseek-v4-pro", "stream": True},
        )
        args, kwargs = mock_request.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], "https://opencode.example/zen/go/v1/chat/completions")
        self.assertEqual(kwargs["headers"].get("authorization"), "Bearer opencode-key")
        self.assertTrue(kwargs["stream"])

    @mock.patch("opencode_proxy.app.requests.request")
    def test_non_stream_response_passed_through_verbatim(self, mock_request):
        body = json.dumps({"object": "list", "data": []}).encode()
        mock_request.return_value = _FakeUpstream(
            content_type="application/json", content=body, lines=[])
        r = self.client.get("/v1/models", headers={"Authorization": "Bearer k"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, body)

    @mock.patch("opencode_proxy.app.requests.request")
    def test_upstream_error_returns_502(self, mock_request):
        import requests as _rq
        mock_request.side_effect = _rq.RequestException("boom")
        r = self.client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer k"},
            json={"stream": True},
        )
        self.assertEqual(r.status_code, 502)


if __name__ == "__main__":
    unittest.main()
