import json
import unittest

import httpx
from fastapi.testclient import TestClient

from opencode_proxy.app import NormalizerSettings, create_app
from opencode_proxy.normalizer import _SseNormalizer, normalize_sse_text


# 实测 opencode 当前畸形流（usage 挂在 finish_reason 块 + 私有块 + [DONE] 后多发块），每条 = 一个 SSE 事件
BROKEN_EVENTS = [
    'data: {"id":"x","object":"chat.completion.chunk","created":1,"model":"deepseek-v4-pro","choices":[{"index":0,"delta":{"role":"assistant","content":"你"}}]}',
    'data: {"id":"x","object":"chat.completion.chunk","created":1,"model":"deepseek-v4-pro","choices":[{"index":0,"delta":{"content":"好"}}]}',
    'data: {"id":"x","object":"chat.completion.chunk","created":1,"model":"deepseek-v4-pro","choices":[{"index":0,"finish_reason":"stop","delta":{"content":""}}],"usage":{"prompt_tokens":45196,"completion_tokens":100,"total_tokens":45296,"prompt_cache_hit_tokens":43648,"prompt_cache_miss_tokens":1548,"prompt_tokens_details":{"cached_tokens":43648}}}',
    'data: {"choices":[],"x-opencode-type":"inference-cost","cost":"0.001","normalizedUsage":{"inputTokens":45196,"cacheReadTokens":43648}}',
    'data: [DONE]',
    'data: {"choices":[],"cost":"0"}',
]


def _sse(events):
    """把每个事件字符串组成合法 SSE 文本（事件间空行分隔）。"""
    return "".join(e + "\n\n" for e in events)


def _norm(events):
    return "".join(normalize_sse_text(_sse(events)))


def _parse_frames(text):
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
        objs, done = _parse_frames(_norm(BROKEN_EVENTS))
        self.assertTrue(done)
        usage_chunks = [o for o in objs if o.get("usage")]
        self.assertEqual(len(usage_chunks), 1)
        self.assertEqual(usage_chunks[0]["choices"], [])
        self.assertEqual(usage_chunks[0]["usage"]["prompt_cache_hit_tokens"], 43648)
        self.assertIs(objs[-1], usage_chunks[0])  # usage 是最后一个 data 对象（在 [DONE] 前）

    def test_content_deltas_preserved_and_finish_usage_stripped(self):
        objs, _ = _parse_frames(_norm(BROKEN_EVENTS))
        content = "".join(c.get("delta", {}).get("content") or ""
                          for o in objs for c in (o.get("choices") or []))
        self.assertEqual(content, "你好")
        finish = [o for o in objs if o.get("choices") and o["choices"][0].get("finish_reason")]
        self.assertTrue(finish)
        self.assertNotIn("usage", finish[0])

    def test_private_and_post_done_chunks_dropped(self):
        objs, _ = _parse_frames(_norm(BROKEN_EVENTS))
        self.assertFalse(any("x-opencode-type" in o for o in objs))
        self.assertFalse(any(o.get("cost") == "0" for o in objs))

    def test_already_standard_stream_is_idempotent(self):
        events = [
            'data: {"id":"y","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"hi"}}]}',
            'data: {"id":"y","object":"chat.completion.chunk","choices":[{"index":0,"finish_reason":"stop","delta":{}}]}',
            'data: {"id":"y","object":"chat.completion.chunk","choices":[],"usage":{"prompt_tokens":10,"completion_tokens":3,"prompt_cache_hit_tokens":8,"prompt_cache_miss_tokens":2}}',
            'data: [DONE]',
        ]
        objs, done = _parse_frames(_norm(events))
        self.assertTrue(done)
        usage_chunks = [o for o in objs if o.get("usage")]
        self.assertEqual(len(usage_chunks), 1)
        self.assertEqual(usage_chunks[0]["usage"]["prompt_cache_hit_tokens"], 8)
        self.assertEqual(usage_chunks[0]["choices"], [])

    def test_no_upstream_usage_is_not_fabricated(self):
        events = [
            'data: {"choices":[{"index":0,"delta":{"content":"a"}}]}',
            'data: {"choices":[{"index":0,"finish_reason":"stop","delta":{}}]}',
            'data: [DONE]',
        ]
        objs, done = _parse_frames(_norm(events))
        self.assertTrue(done)
        self.assertFalse(any(o.get("usage") for o in objs))

    def test_truncated_stream_without_done_does_not_synthesize_done(self):
        objs, done = _parse_frames(_norm(['data: {"choices":[{"index":0,"delta":{"content":"a"}}]}']))
        self.assertFalse(done)

    def test_truncated_stream_with_billable_usage_drops_usage(self):
        events = [
            'data: {"choices":[{"index":0,"delta":{"content":"a"}}]}',
            'data: {"choices":[{"index":0,"finish_reason":"stop","delta":{"content":""}}],"usage":{"prompt_tokens":50,"completion_tokens":9,"prompt_cache_hit_tokens":40,"prompt_cache_miss_tokens":10}}',
        ]  # 无 [DONE]
        objs, done = _parse_frames(_norm(events))
        self.assertFalse(done)
        self.assertFalse(any(o.get("usage") for o in objs))

    def test_partial_usage_without_completion_not_promoted(self):
        events = [
            'data: {"choices":[{"index":0,"finish_reason":"stop","delta":{}}],"usage":{"prompt_tokens":100,"prompt_cache_hit_tokens":80}}',
            'data: [DONE]',
        ]
        objs, _ = _parse_frames(_norm(events))
        self.assertFalse(any(o.get("usage") for o in objs))

    def test_nonfinite_or_negative_usage_not_promoted(self):
        events = [
            'data: {"choices":[{"index":0,"finish_reason":"stop","delta":{}}],"usage":{"prompt_tokens":100,"completion_tokens":-1,"prompt_cache_hit_tokens":80}}',
            'data: [DONE]',
        ]
        objs, _ = _parse_frames(_norm(events))
        self.assertFalse(any(o.get("usage") for o in objs))

    def test_usage_on_nonterminal_delta_not_promoted(self):
        events = [
            'data: {"choices":[{"index":0,"delta":{"content":"a"}}],"usage":{"prompt_tokens":100,"completion_tokens":1}}',
            'data: {"choices":[{"index":0,"delta":{"content":"bbbb"}}]}',
            'data: {"choices":[{"index":0,"finish_reason":"stop","delta":{}}]}',
            'data: [DONE]',
        ]
        objs, done = _parse_frames(_norm(events))
        self.assertTrue(done)
        self.assertFalse(any(o.get("usage") for o in objs))

    def test_last_terminal_usage_must_be_billable(self):
        events = [
            'data: {"choices":[{"index":0,"finish_reason":"stop","delta":{}}],"usage":{"prompt_tokens":100,"completion_tokens":5,"prompt_cache_hit_tokens":80,"prompt_cache_miss_tokens":20}}',
            'data: {"choices":[],"usage":{"prompt_tokens":100}}',
            'data: [DONE]',
        ]
        objs, _ = _parse_frames(_norm(events))
        self.assertFalse(any(o.get("usage") for o in objs))

    def test_cache_hit_exceeding_prompt_not_billable(self):
        events = [
            'data: {"choices":[{"index":0,"finish_reason":"stop","delta":{}}],"usage":{"prompt_tokens":10,"completion_tokens":2,"prompt_cache_hit_tokens":999}}',
            'data: [DONE]',
        ]
        objs, _ = _parse_frames(_norm(events))
        self.assertFalse(any(o.get("usage") for o in objs))

    def test_miss_present_without_hit_not_billable(self):
        """miss 存在但无 hit：CRA 直接用 miss、hit 按 0 → 漏计 prompt-miss。必须不转正。"""
        events = [
            'data: {"choices":[{"index":0,"finish_reason":"stop","delta":{}}],"usage":{"prompt_tokens":1000,"completion_tokens":10,"prompt_cache_miss_tokens":10}}',
            'data: [DONE]',
        ]
        objs, _ = _parse_frames(_norm(events))
        self.assertFalse(any(o.get("usage") for o in objs))

    def test_hit_plus_miss_not_equal_prompt_not_billable(self):
        events = [
            'data: {"choices":[{"index":0,"finish_reason":"stop","delta":{}}],"usage":{"prompt_tokens":1000,"completion_tokens":10,"prompt_cache_hit_tokens":100,"prompt_cache_miss_tokens":100}}',
            'data: [DONE]',
        ]
        objs, _ = _parse_frames(_norm(events))
        self.assertFalse(any(o.get("usage") for o in objs))

    def test_consistent_hit_miss_is_billable(self):
        events = [
            'data: {"choices":[{"index":0,"finish_reason":"stop","delta":{}}],"usage":{"prompt_tokens":1000,"completion_tokens":10,"prompt_cache_hit_tokens":700,"prompt_cache_miss_tokens":300}}',
            'data: [DONE]',
        ]
        objs, _ = _parse_frames(_norm(events))
        usage = [o for o in objs if o.get("usage")]
        self.assertEqual(len(usage), 1)
        self.assertEqual(usage[0]["usage"]["prompt_cache_hit_tokens"], 700)

    def test_nested_cached_tokens_inconsistent_not_billable(self):
        events = [
            'data: {"choices":[{"index":0,"finish_reason":"stop","delta":{}}],"usage":{"prompt_tokens":10,"completion_tokens":1,"prompt_tokens_details":{"cached_tokens":999}}}',
            'data: [DONE]',
        ]
        objs, _ = _parse_frames(_norm(events))
        self.assertFalse(any(o.get("usage") for o in objs))

    def test_error_object_with_usage_key_is_passed_through(self):
        """带 error 且含 usage:null 的对象绝不能被 usage 分支吞掉。"""
        objs, done = _parse_frames(_norm([
            'data: {"choices":[],"usage":null,"error":{"message":"boom"}}',
            'data: [DONE]',
        ]))
        self.assertTrue(done)
        self.assertTrue(any("error" in o for o in objs))

    def test_empty_terminal_usage_clears_earlier_billable_candidate(self):
        """终态 usage:{} 出现在可计费 usage 之后 → 清掉候选，最终不发 usage（fail-closed）。"""
        events = [
            'data: {"choices":[{"index":0,"finish_reason":"stop","delta":{}}],"usage":{"prompt_tokens":100,"completion_tokens":5,"prompt_cache_hit_tokens":80,"prompt_cache_miss_tokens":20}}',
            'data: {"choices":[],"usage":{}}',
            'data: [DONE]',
        ]
        objs, _ = _parse_frames(_norm(events))
        self.assertFalse(any(o.get("usage") for o in objs))

    def test_empty_usage_object_is_not_emitted(self):
        objs, done = _parse_frames(_norm(['data: {"choices":[],"usage":{}}', 'data: [DONE]']))
        self.assertTrue(done)
        self.assertFalse(any("usage" in o for o in objs))

    def test_malformed_data_event_fails_closed(self):
        objs, done = _parse_frames(_norm(['data: not-json', 'data: [DONE]']))
        self.assertFalse(done)
        self.assertFalse(any(o.get("usage") for o in objs))

    def test_error_object_is_passed_through_not_dropped(self):
        objs, done = _parse_frames(_norm(['data: {"error":{"message":"boom","type":"server_error"}}']))
        self.assertFalse(done)
        self.assertTrue(any("error" in o for o in objs))

    def test_private_chunk_with_error_key_is_not_dropped(self):
        objs, done = _parse_frames(_norm(['data: {"choices":[],"cost":"0","error":{"message":"boom"}}']))
        self.assertFalse(done)
        self.assertTrue(any("error" in o for o in objs))

    def test_multiline_data_event_is_assembled(self):
        # 一个事件跨两行 data:（SSE 规范），framer 应以 \n 连接成完整 JSON
        multiline_event = 'data: {"choices":[{"index":0,\ndata: "delta":{"content":"hi"}}]}'
        objs, done = _parse_frames(_norm([multiline_event, 'data: [DONE]']))
        self.assertTrue(done)
        content = "".join(c.get("delta", {}).get("content") or ""
                          for o in objs for c in (o.get("choices") or []))
        self.assertEqual(content, "hi")

    def test_unicode_line_separator_in_content_does_not_break_stream(self):
        """核心回归：正文含 U+2028（合法 JSON 字符、但 str.splitlines/httpx 会误切）时，
        自建 framer 不切断 → usage + [DONE] 正常，且正文原样保留、输出对下游安全（已转义）。"""
        u = " "
        events = [
            'data: {"choices":[{"index":0,"delta":{"content":"a' + u + 'b"}}],"usage":null}',
            'data: {"choices":[{"index":0,"finish_reason":"stop","delta":{}}],"usage":{"prompt_tokens":10,"completion_tokens":2,"prompt_cache_hit_tokens":8,"prompt_cache_miss_tokens":2}}',
            'data: [DONE]',
        ]
        out_text = _norm(events)
        # 输出的任何一行都不得含裸 U+2028（否则把问题传给下游）
        self.assertNotIn(u, out_text)
        objs, done = _parse_frames(out_text)
        self.assertTrue(done)
        usage = [o for o in objs if o.get("usage")]
        self.assertEqual(len(usage), 1)
        self.assertEqual(usage[0]["usage"]["prompt_cache_hit_tokens"], 8)
        content = "".join(c.get("delta", {}).get("content") or ""
                          for o in objs for c in (o.get("choices") or []))
        self.assertIn(u, content)  # 内容里的 U+2028 经 json 往返无损保留

    def test_crlf_line_endings_are_handled(self):
        text = ('data: {"choices":[{"index":0,"finish_reason":"stop","delta":{"content":"x"}}],'
                '"usage":{"prompt_tokens":5,"completion_tokens":1}}\r\n\r\ndata: [DONE]\r\n\r\n')
        objs, done = _parse_frames("".join(normalize_sse_text(text)))
        self.assertTrue(done)
        self.assertTrue(any(o.get("usage") for o in objs))

    def test_usage_and_done_emitted_on_done_not_on_close(self):
        n = _SseNormalizer()
        emitted = []
        emitted += n.feed_event('{"choices":[{"index":0,"finish_reason":"stop","delta":{}}],"usage":{"prompt_tokens":10,"completion_tokens":2,"prompt_cache_hit_tokens":8,"prompt_cache_miss_tokens":2}}')
        emitted += n.feed_event("[DONE]")
        self.assertTrue(n.done)
        text = "".join(emitted)
        self.assertIn('"usage"', text)
        self.assertTrue(text.rstrip().endswith("[DONE]"))
        self.assertEqual(n.close(), [])


def _sse_body(events):
    return _sse(events).encode("utf-8")


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
                                  content=_sse_body(BROKEN_EVENTS))
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
        err = _sse_body(['data: {"error":{"message":"rate limited"}}'])

        def handler(req):
            return httpx.Response(429, headers={"content-type": "text/event-stream"}, content=err)
        with self._client(handler) as c:
            r = c.post("/v1/chat/completions", headers={"Authorization": "Bearer k"},
                       json={"stream": True})
            self.assertEqual(r.status_code, 429)
            self.assertEqual(r.content, err)

    def test_content_type_case_insensitive_is_normalized(self):
        def handler(req):
            return httpx.Response(200, headers={"content-type": "Text/Event-Stream; charset=utf-8"},
                                  content=_sse_body(BROKEN_EVENTS))
        with self._client(handler) as c:
            r = c.post("/v1/chat/completions", headers={"Authorization": "Bearer k"},
                       json={"stream": True})
            objs, done = _parse_frames(r.text)
            self.assertTrue(done)
            self.assertEqual([o for o in objs if o.get("usage")][0]["choices"], [])

    def test_upstream_error_returns_502(self):
        def handler(req):
            raise httpx.ConnectError("boom", request=req)
        with self._client(handler) as c:
            r = c.post("/v1/chat/completions", headers={"Authorization": "Bearer k"},
                       json={"stream": True})
            self.assertEqual(r.status_code, 502)

    def test_dotdot_path_segments_rejected(self):
        called = {"n": 0}

        def handler(req):
            called["n"] += 1
            return httpx.Response(200)
        with self._client(handler) as c:
            r = c.get("/%2e%2e/%2e%2e/admin", headers={"Authorization": "Bearer k"})
            self.assertEqual(r.status_code, 404)
            self.assertEqual(called["n"], 0)  # 未打到上游


if __name__ == "__main__":
    unittest.main()
