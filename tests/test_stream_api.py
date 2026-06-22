import socket
import threading
import time
import unittest
from unittest import mock

import pytest
import requests
import uvicorn

import backend.main as main_module
from backend.skill import SkillEngine


pytestmark = pytest.mark.slow


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ChatStreamApiTests(unittest.TestCase):
    def setUp(self):
        # W2-B (T11c): the stream endpoint resolves require_project(project_id, uid) before streaming.
        # auth off → uid "local"; install a MagicMock engine whose get_project_record echoes so the
        # 'demo' project resolves. get_chat_handler is monkeypatched per-test (now 2-arg uid,pid).
        self._prev_auth = getattr(main_module.app.state, "auth_required", True)
        main_module.app.state.auth_required = False
        engine = mock.MagicMock(spec=SkillEngine)
        engine.get_project_record.side_effect = lambda pid: {"id": pid, "name": pid}
        with main_module._engines_guard:
            self._prev_engine = main_module._engines.get("local")
            main_module._engines["local"] = engine

    def tearDown(self):
        main_module.app.state.auth_required = self._prev_auth
        with main_module._engines_guard:
            if self._prev_engine is None:
                main_module._engines.pop("local", None)
            else:
                main_module._engines["local"] = self._prev_engine

    def test_stream_endpoint_emits_provider_usage_shape_incrementally(self):
        handler = mock.Mock()

        def fake_stream(project_id, message_text, attached_material_ids, transient_attachments, **kwargs):
            events = [
                {"type": "tool", "data": "🔧 调用工具: web_search({\"query\":\"q1\"})"},
                {"type": "content", "data": "第一段"},
                {
                    "type": "usage",
                    "data": {
                        "usage_source": "provider",
                        "context_used_tokens": 180000,
                        "input_tokens": 180000,
                        "output_tokens": 1200,
                        "total_tokens": 181200,
                        "max_tokens": 200000,
                        "effective_max_tokens": 200000,
                        "provider_max_tokens": 1000000,
                        "preflight_compaction_used": False,
                        "post_turn_compaction_status": "completed",
                        "compressed": False,
                    },
                },
            ]
            for event in events:
                time.sleep(0.35)
                yield event

        handler.chat_stream.side_effect = fake_stream
        original_get_chat_handler = main_module.get_chat_handler
        main_module.get_chat_handler = lambda uid, project_id: handler
        port = _pick_free_port()
        config = uvicorn.Config(main_module.app, host="127.0.0.1", port=port, log_level="error")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        try:
            time.sleep(1.0)
            start = time.time()
            response = requests.post(
                f"http://127.0.0.1:{port}/api/chat/stream",
                json={
                    "project_id": "demo",
                    "message_text": "test",
                    "attached_material_ids": [],
                    "transient_attachments": [],
                },
                stream=True,
                timeout=30,
            )
            arrivals = []
            buffer = ""
            for chunk in response.iter_content(chunk_size=1, decode_unicode=True):
                if not chunk:
                    continue
                buffer += chunk
                if not buffer.endswith("\n\n"):
                    continue
                arrivals.append((time.time() - start, buffer.strip()))
                if buffer.strip() == "data: [DONE]":
                    break
                buffer = ""
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            main_module.get_chat_handler = original_get_chat_handler

        self.assertGreaterEqual(len(arrivals), 4)
        self.assertLess(arrivals[0][0], 0.7)
        self.assertIn('"type": "tool"', arrivals[0][1])
        self.assertIn('"type": "content"', arrivals[1][1])
        self.assertGreaterEqual(arrivals[1][0], arrivals[0][0])
        self.assertIn('"usage_source": "provider"', arrivals[2][1])
        self.assertIn('"context_used_tokens": 180000', arrivals[2][1])
        self.assertIn('"max_tokens": 200000', arrivals[2][1])
        self.assertIn('"effective_max_tokens": 200000', arrivals[2][1])
        self.assertIn('"provider_max_tokens": 1000000', arrivals[2][1])
        self.assertIn('"post_turn_compaction_status": "completed"', arrivals[2][1])

    def test_stream_endpoint_forwards_transient_attachments(self):
        handler = mock.Mock()

        def fake_stream(project_id, message_text, attached_material_ids, transient_attachments, **kwargs):
            self.assertEqual(project_id, "demo")
            self.assertEqual(message_text, "请看截图")
            self.assertEqual(attached_material_ids, [])
            self.assertEqual(
                transient_attachments,
                [
                    {
                        "id": "att-1",
                        "name": "bug.png",
                        "mime_type": "image/png",
                        "data_url": "data:image/png;base64,AAAA",
                    }
                ],
            )
            yield {"type": "content", "data": "已看到截图"}

        handler.chat_stream.side_effect = fake_stream
        original_get_chat_handler = main_module.get_chat_handler
        main_module.get_chat_handler = lambda uid, project_id: handler
        port = _pick_free_port()
        config = uvicorn.Config(main_module.app, host="127.0.0.1", port=port, log_level="error")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        try:
            time.sleep(1.0)
            response = requests.post(
                f"http://127.0.0.1:{port}/api/chat/stream",
                json={
                    "project_id": "demo",
                    "message_text": "请看截图",
                    "attached_material_ids": [],
                    "transient_attachments": [
                        {
                            "id": "att-1",
                            "name": "bug.png",
                            "mime_type": "image/png",
                            "data_url": "data:image/png;base64,AAAA",
                        }
                    ],
                },
                stream=True,
                timeout=30,
            )
            payload = "".join(response.iter_content(chunk_size=1024, decode_unicode=True))
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            main_module.get_chat_handler = original_get_chat_handler

        self.assertIn("已看到截图", payload)

    def test_stream_endpoint_keeps_usage_event_last_before_done(self):
        handler = mock.Mock()

        def fake_stream(project_id, message_text, attached_material_ids, transient_attachments, **kwargs):
            yield {"type": "content", "data": "第一段"}
            yield {
                "type": "usage",
                "data": {
                    "usage_source": "provider",
                    "context_used_tokens": 180000,
                    "effective_max_tokens": 200000,
                    "provider_max_tokens": 1000000,
                    "max_tokens": 200000,
                    "preflight_compaction_used": False,
                    "post_turn_compaction_status": "completed",
                },
            }

        handler.chat_stream.side_effect = fake_stream
        original_get_chat_handler = main_module.get_chat_handler
        main_module.get_chat_handler = lambda uid, project_id: handler
        port = _pick_free_port()
        config = uvicorn.Config(main_module.app, host="127.0.0.1", port=port, log_level="error")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        try:
            time.sleep(1.0)
            response = requests.post(
                f"http://127.0.0.1:{port}/api/chat/stream",
                json={
                    "project_id": "demo",
                    "message_text": "test",
                    "attached_material_ids": [],
                    "transient_attachments": [],
                },
                stream=True,
                timeout=30,
            )
            events = [
                chunk.strip()
                for chunk in response.iter_content(chunk_size=1024, decode_unicode=True)
                if chunk.strip()
            ]
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            main_module.get_chat_handler = original_get_chat_handler

        content_event_index = next(index for index, item in enumerate(events) if '"type": "content"' in item)
        usage_event_index = next(index for index, item in enumerate(events) if '"type": "usage"' in item)
        done_event_index = next(index for index, item in enumerate(events) if item == "data: [DONE]")
        self.assertLess(content_event_index, usage_event_index)
        self.assertLess(usage_event_index, done_event_index)
