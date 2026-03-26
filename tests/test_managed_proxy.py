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
    def test_non_stream_request_forces_model_and_injects_upstream_key(self, mock_post):
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


if __name__ == "__main__":
    unittest.main()
