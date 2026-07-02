import json
import unittest

import httpx
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
        objs, done = _parse_frames("".join(normalize_sse(BROKEN_LINES)))
        self.assertTrue(done)
        usage_chunks = [o for o in objs if o.get("usage")]
        self.assertEqual(len(usage_chunks), 1)
        self.assertEqual(usage_chunks[0]["choices"], [])
        self.assertEqual(usage_chunks[0]["usage"]["prompt_cache_hit_tokens"], 43648)
        self.assertEqual(usage_chunks[0]["usage"]["prompt_cache_miss_tokens"], 1548)
        # usage 块须是最后一个 data 对象（在 [DONE] 前）
        self.assertIs(objs[-1], usage_chunks[0])

    def test_content_deltas_preserved_and_finish_usage_stripped(self):
        objs, _ = _parse_frames("".join(normalize_sse(BROKEN_LINES)))
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
        objs, _ = _parse_frames("".join(normalize_sse(BROKEN_LINES)))
        self.assertFalse(any("x-opencode-type" in o for o in objs))
        self.assertFalse(any(o.get("cost") == "0" for o in objs))

    def test_already_standard_stream_is_idempotent(self):
        standard = [
            'data: {"id":"y","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"hi"}}]}',
            'data: {"id":"y","object":"chat.completion.chunk","choices":[{"index":0,"finish_reason":"stop","delta":{}}]}',
            'data: {"id":"y","object":"chat.completion.chunk","choices":[],"usage":{"prompt_tokens":10,"completion_tokens":3,"prompt_cache_hit_tokens":8,"prompt_cache_miss_tokens":2}}',
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

    def test_truncated_stream_without_done_does_not_synthesize_done(self):
        """上游截断（没等到 [DONE]）→ 不得合成 [DONE]（fail-closed，让下游感知异常）。"""
        truncated = ['data: {"choices":[{"index":0,"delta":{"content":"a"}}]}']
        _, done = _parse_frames("".join(normalize_sse(truncated)))
        self.assertFalse(done)

    def test_error_object_is_passed_through_not_dropped(self):
        """{"error":...} 无 choices 无 usage，但绝不能当私有块吞掉。"""
        lines = ['data: {"error":{"message":"boom","type":"server_error"}}']
        objs, done = _parse_frames("".join(normalize_sse(lines)))
        self.assertFalse(done)  # 上游没发 [DONE]
        self.assertTrue(any("error" in o for o in objs))

    def test_partial_usage_without_completion_not_promoted(self):
        """缺 completion_tokens 的 usage 不转正（否则 CRA 把输出计 0，非 fail-closed）。"""
        lines = [
            'data: {"choices":[{"index":0,"finish_reason":"stop","delta":{"content":""}}],"usage":{"prompt_tokens":100,"prompt_cache_hit_tokens":80}}',
            'data: [DONE]',
        ]
        objs, _ = _parse_frames("".join(normalize_sse(lines)))
        self.assertFalse(any(o.get("usage") for o in objs))

    def test_last_billable_usage_wins(self):
        """出现"中间 partial usage + 最终完整 usage"时，采信最后一个可计费 usage。"""
        lines = [
            'data: {"choices":[{"index":0,"delta":{"content":"a"}}],"usage":{"prompt_tokens":50,"completion_tokens":1,"prompt_cache_hit_tokens":0}}',
            'data: {"choices":[{"index":0,"finish_reason":"stop","delta":{"content":""}}],"usage":{"prompt_tokens":50,"completion_tokens":9,"prompt_cache_hit_tokens":40,"prompt_cache_miss_tokens":10}}',
            'data: [DONE]',
        ]
        objs, _ = _parse_frames("".join(normalize_sse(lines)))
        usage_chunks = [o for o in objs if o.get("usage")]
        self.assertEqual(len(usage_chunks), 1)
        self.assertEqual(usage_chunks[0]["usage"]["completion_tokens"], 9)
        self.assertEqual(usage_chunks[0]["usage"]["prompt_cache_hit_tokens"], 40)

    def test_multiline_data_event_is_assembled(self):
        """SSE 允许一个 event 跨多行 data；跨行 JSON 应累积到可解析。"""
        lines = [
            'data: {"choices":[{"index":0,',
            'data: "delta":{"content":"hi"}}]}',
            '',
            'data: [DONE]',
        ]
        objs, done = _parse_frames("".join(normalize_sse(lines)))
        self.assertTrue(done)
        content = "".join(c.get("delta", {}).get("content") or ""
                          for o in objs for c in (o.get("choices") or []))
        self.assertEqual(content, "hi")


def _sse_body(lines):
    return "".join(l + "\n\n" for l in lines).encode("utf-8")


class NormalizerAppTests(unittest.TestCase):
    def _client(self, handler):
        transport = httpx.MockTransport(handler)
        settings = NormalizerSettings(upstream_base_url="https://opencode.example/zen/go")
        return TestClient(create_app(settings, transport=transport))

    def test_health(self):
        with self._client(lambda req: httpx.Response(200)) as c:
            r = c.get("/health")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["upstream"], "https://opencode.example/zen/go")

    def test_streaming_response_is_normalized(self):
        def handler(req):
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=_sse_body(BROKEN_LINES))
        with self._client(handler) as c:
            r = c.post("/v1/chat/completions",
                       headers={"Authorization": "Bearer opencode-key"},
                       json={"model": "deepseek-v4-pro", "stream": True})
            self.assertEqual(r.status_code, 200)
            objs, done = _parse_frames(r.text)
            self.assertTrue(done)
            usage_chunks = [o for o in objs if o.get("usage")]
            self.assertEqual(len(usage_chunks), 1)
            self.assertEqual(usage_chunks[0]["choices"], [])
            self.assertEqual(usage_chunks[0]["usage"]["prompt_cache_hit_tokens"], 43648)

    def test_forwards_upstream_url_and_authorization(self):
        seen = {}

        def handler(req):
            seen["url"] = str(req.url)
            seen["auth"] = req.headers.get("authorization")
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=_sse_body(["data: [DONE]"]))
        with self._client(handler) as c:
            c.post("/v1/chat/completions?a=1&a=2",
                   headers={"Authorization": "Bearer opencode-key"},
                   json={"model": "deepseek-v4-pro", "stream": True})
        self.assertEqual(seen["url"], "https://opencode.example/zen/go/v1/chat/completions?a=1&a=2")
        self.assertEqual(seen["auth"], "Bearer opencode-key")

    def test_non_stream_response_passed_through_verbatim(self):
        body = json.dumps({"object": "list", "data": []}).encode()

        def handler(req):
            return httpx.Response(200, headers={"content-type": "application/json"}, content=body)
        with self._client(handler) as c:
            r = c.get("/v1/models", headers={"Authorization": "Bearer k"})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.content, body)

    def test_error_status_sse_passed_through_verbatim(self):
        """4xx/5xx 的 SSE 错误体不走 normalizer，逐字透传保留 opencode 错误详情。"""
        err = _sse_body(['data: {"error":{"message":"rate limited"}}'])

        def handler(req):
            return httpx.Response(429, headers={"content-type": "text/event-stream"}, content=err)
        with self._client(handler) as c:
            r = c.post("/v1/chat/completions", headers={"Authorization": "Bearer k"},
                       json={"stream": True})
            self.assertEqual(r.status_code, 429)
            self.assertEqual(r.content, err)

    def test_upstream_error_returns_502(self):
        def handler(req):
            raise httpx.ConnectError("boom", request=req)
        with self._client(handler) as c:
            r = c.post("/v1/chat/completions", headers={"Authorization": "Bearer k"},
                       json={"stream": True})
            self.assertEqual(r.status_code, 502)


if __name__ == "__main__":
    unittest.main()
