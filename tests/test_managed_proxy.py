import unittest
from unittest import mock

from fastapi.testclient import TestClient

from managed_proxy.app import ProxySettings, create_app


class ManagedProxyTests(unittest.TestCase):
    def setUp(self):
        settings = ProxySettings(
            upstream_base_url="https://upstream.example/v1",
            upstream_api_key="upstream-secret",
            allowed_models=["gemini-3-flash"],
            client_bearer_token="client-token",
        )
        self.client = TestClient(create_app(settings))

    def test_models_endpoint_returns_only_allowed_models(self):
        response = self.client.get(
            "/v1/models",
            headers={"Authorization": "Bearer client-token"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["object"], "list")
        self.assertEqual([model["id"] for model in payload["data"]], ["gemini-3-flash"])

    def test_missing_or_invalid_bearer_is_rejected(self):
        response = self.client.get("/v1/models")
        self.assertEqual(response.status_code, 401)

        response = self.client.get(
            "/v1/models",
            headers={"Authorization": "Bearer wrong-token"},
        )
        self.assertEqual(response.status_code, 401)

    @mock.patch("managed_proxy.app.requests.post")
    def test_non_whitelisted_model_is_rejected_before_upstream_call(self, mock_post):
        response = self.client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer client-token"},
            json={
                "model": "gpt-4.1-mini",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        self.assertEqual(response.status_code, 400)
        mock_post.assert_not_called()

    @mock.patch("managed_proxy.app.requests.post")
    def test_non_stream_request_passes_model_and_injects_upstream_key(self, mock_post):
        mock_post.return_value = mock.Mock(
            status_code=200,
            content=b'{"id":"chatcmpl-test","choices":[]}',
            headers={"content-type": "application/json"},
        )

        response = self.client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer client-token"},
            json={
                "model": "gemini-3-flash",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("chatcmpl-test", response.text)
        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer upstream-secret")
        self.assertEqual(kwargs["json"]["model"], "gemini-3-flash")
        self.assertFalse(kwargs["stream"])

    @mock.patch("managed_proxy.app.requests.post")
    def test_stream_request_passes_through_upstream_chunks(self, mock_post):
        upstream_response = mock.Mock(
            status_code=200,
            headers={"content-type": "text/event-stream"},
        )
        upstream_response.iter_content.return_value = [
            b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n',
            b"data: [DONE]\n\n",
        ]
        mock_post.return_value = upstream_response

        response = self.client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer client-token"},
            json={
                "model": "gemini-3-flash",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("data: [DONE]", response.text)
        _, kwargs = mock_post.call_args
        self.assertTrue(kwargs["stream"])


class ProxyPassthroughTests(unittest.TestCase):
    """B1: model passthrough, selectable_models, /health fields."""

    def _client(self):
        s = ProxySettings(
            upstream_base_url="http://up/v1",
            upstream_api_key="k",
            allowed_models=["deepseek-v4-pro", "Qwen/Qwen3-VL-8B-Instruct"],
            selectable_models=["deepseek-v4-pro"],
            client_bearer_token="managed",
        )
        return TestClient(create_app(s)), s

    def test_vision_model_passes_through_not_rewritten(self):
        client, _ = self._client()
        captured = {}

        def fake_post(url, headers, json, stream, timeout):
            captured["model"] = json["model"]
            m = mock.Mock()
            m.status_code = 200
            m.content = b'{"ok":1}'
            m.headers = {"content-type": "application/json"}
            m.close = lambda: None
            return m

        with mock.patch("managed_proxy.app.requests.post", side_effect=fake_post):
            r = client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer managed"},
                json={"model": "Qwen/Qwen3-VL-8B-Instruct", "messages": []},
            )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(captured["model"], "Qwen/Qwen3-VL-8B-Instruct")

    def test_primary_model_passes_through_unchanged(self):
        client, _ = self._client()
        captured = {}

        def fake_post(url, headers, json, stream, timeout):
            captured["model"] = json["model"]
            m = mock.Mock()
            m.status_code = 200
            m.content = b'{"ok":1}'
            m.headers = {"content-type": "application/json"}
            m.close = lambda: None
            return m

        with mock.patch("managed_proxy.app.requests.post", side_effect=fake_post):
            r = client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer managed"},
                json={"model": "deepseek-v4-pro", "messages": []},
            )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(captured["model"], "deepseek-v4-pro")

    def test_disallowed_model_returns_400(self):
        client, _ = self._client()
        with mock.patch("managed_proxy.app.requests.post") as mock_post:
            r = client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer managed"},
                json={"model": "gpt-99", "messages": []},
            )
        self.assertEqual(r.status_code, 400)
        mock_post.assert_not_called()

    def test_models_endpoint_only_lists_selectable(self):
        client, _ = self._client()
        r = client.get("/v1/models", headers={"Authorization": "Bearer managed"})
        self.assertEqual(r.status_code, 200)
        ids = [m["id"] for m in r.json()["data"]]
        self.assertIn("deepseek-v4-pro", ids)
        self.assertNotIn("Qwen/Qwen3-VL-8B-Instruct", ids)

    def test_health_lists_allowed_and_selectable_for_preflight(self):
        client, _ = self._client()
        r = client.get("/health")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("Qwen/Qwen3-VL-8B-Instruct", body["allowed_models"])
        self.assertEqual(body["selectable_models"], ["deepseek-v4-pro"])

    def test_selectable_defaults_to_allowed_when_not_specified(self):
        """Backward compat: if selectable_models not given, defaults to full allowed list."""
        s = ProxySettings(
            upstream_base_url="http://up/v1",
            upstream_api_key="k",
            allowed_models=["model-a", "model-b"],
            client_bearer_token="tok",
        )
        self.assertEqual(s.selectable_models, ["model-a", "model-b"])

    def test_from_env_reads_selectable_models(self):
        env = {
            "MANAGED_PROXY_UPSTREAM_BASE_URL": "http://up/v1",
            "MANAGED_PROXY_UPSTREAM_API_KEY": "k",
            "MANAGED_PROXY_ALLOWED_MODELS": "deepseek-v4-pro,Qwen/Qwen3-VL-8B-Instruct",
            "MANAGED_PROXY_SELECTABLE_MODELS": "deepseek-v4-pro",
            "MANAGED_PROXY_CLIENT_TOKEN": "tok",
        }
        with mock.patch.dict("os.environ", env, clear=False):
            s = ProxySettings.from_env()
        self.assertEqual(s.allowed_models, ["deepseek-v4-pro", "Qwen/Qwen3-VL-8B-Instruct"])
        self.assertEqual(s.selectable_models, ["deepseek-v4-pro"])

    def test_disallowed_model_rejected_on_stream_path(self):
        """Whitelist guard fires on stream requests too; no upstream call is made."""
        client, _ = self._client()
        with mock.patch("managed_proxy.app.requests.post") as mock_post:
            r = client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer managed"},
                json={"model": "gpt-99", "messages": [], "stream": True},
            )
        self.assertEqual(r.status_code, 400)
        mock_post.assert_not_called()

    def test_missing_model_defaults_to_primary(self):
        """Backward-compat: body with no 'model' key passes through with the primary model."""
        client, _ = self._client()
        captured = {}

        def fake_post(url, headers, json, stream, timeout):
            captured["model"] = json["model"]
            m = mock.Mock()
            m.status_code = 200
            m.content = b'{"ok":1}'
            m.headers = {"content-type": "application/json"}
            m.close = lambda: None
            return m

        with mock.patch("managed_proxy.app.requests.post", side_effect=fake_post):
            r = client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer managed"},
                json={"messages": [{"role": "user", "content": "hi"}]},
            )
        self.assertEqual(r.status_code, 200)
        # primary_model is allowed_models[0] = "deepseek-v4-pro"
        self.assertEqual(captured["model"], "deepseek-v4-pro")

    def test_empty_selectable_models_lists_nothing(self):
        """An explicit selectable_models=[] produces an empty /v1/models data list (no vision-model leak)."""
        s = ProxySettings(
            upstream_base_url="http://up/v1",
            upstream_api_key="k",
            allowed_models=["deepseek-v4-pro", "Qwen/Qwen3-VL-8B-Instruct"],
            selectable_models=[],
            client_bearer_token="managed",
        )
        client = TestClient(create_app(s))
        r = client.get("/v1/models", headers={"Authorization": "Bearer managed"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["data"], [])


if __name__ == "__main__":
    unittest.main()
