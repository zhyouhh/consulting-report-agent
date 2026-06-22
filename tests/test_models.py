import unittest

from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.models import ChatRequest, ProjectInfo

from tests.test_auth_api import AuthApiTestBase


class ModelsListApiTests(AuthApiTestBase):
    """POST /api/models/list — B3 Task 5：custom api_base 走 SSRF 白名单校验。"""

    def setUp(self):
        super().setUp()
        self.m.app.state.auth_required = False   # get_current_uid → "local"
        self.client = TestClient(self.m.app)

    def test_models_list_rejects_offlist_base(self):
        resp = self.client.post("/api/models/list", json={
            "api_key": "sk-x", "api_base": "https://evil.example.com/v1",
        })
        self.assertEqual(resp.status_code, 400)


class ModelsSchemaTests(unittest.TestCase):
    def test_project_info_accepts_v2_fields(self):
        payload = ProjectInfo(
            name="demo-project",
            workspace_dir="D:/Workspaces/demo",
            project_type="strategy-consulting",
            theme="AI strategy planning",
            target_audience="executive audience",
            deadline="2026-04-01",
            expected_length="3000 words",
            notes="existing interview notes",
            initial_material_paths=[
                "D:/Workspaces/demo/materials/interview-notes.txt",
                "D:/Workspaces/demo/materials/chart.png",
            ],
        )
        self.assertEqual(payload.workspace_dir, "D:/Workspaces/demo")
        self.assertEqual(payload.project_type, "strategy-consulting")
        self.assertEqual(payload.expected_length, "3000 words")
        self.assertEqual(len(payload.initial_material_paths), 2)

    def test_project_info_rejects_legacy_report_type_field(self):
        with self.assertRaises(ValidationError):
            ProjectInfo(
                name="legacy-project",
                workspace_dir="D:/Workspaces/legacy",
                report_type="research-report",
                theme="legacy-theme",
                target_audience="executive audience",
                deadline="2026-04-01",
                expected_length="3000 words",
                notes="",
            )

    def test_chat_request_accepts_project_id_and_attached_material_ids(self):
        payload = ChatRequest(
            project_id="proj-demo",
            message_text="Please update the issue tree with new materials.",
            attached_material_ids=["mat-1", "mat-2"],
        )
        self.assertEqual(payload.project_id, "proj-demo")
        self.assertEqual(payload.message_text, "Please update the issue tree with new materials.")
        self.assertEqual(payload.attached_material_ids, ["mat-1", "mat-2"])

    def test_chat_request_accepts_transient_image_attachments(self):
        payload = ChatRequest(
            project_id="proj-demo",
            message_text="Please review this screenshot.",
            transient_attachments=[
                {
                    "id": "att-1",
                    "name": "bug.png",
                    "mime_type": "image/png",
                    "data_url": "data:image/png;base64,AAAA",
                }
            ],
        )

        self.assertEqual(len(payload.transient_attachments), 1)
        self.assertEqual(payload.transient_attachments[0].mime_type, "image/png")
        self.assertEqual(payload.transient_attachments[0].id, "att-1")

    def test_chat_request_defaults_transient_attachments_to_empty_list(self):
        payload = ChatRequest(
            project_id="proj-demo",
            message_text="Please review this screenshot.",
        )

        self.assertEqual(payload.transient_attachments, [])

    def test_chat_request_rejects_non_image_transient_attachments(self):
        with self.assertRaises(Exception):
            ChatRequest(
                project_id="proj-demo",
                message_text="Please review this file.",
                transient_attachments=[
                    {
                        "id": "att-1",
                        "name": "memo.pdf",
                        "mime_type": "application/pdf",
                        "data_url": "data:application/pdf;base64,AAAA",
                    }
                ],
            )

    def test_chat_request_rejects_legacy_project_name_and_message_fields(self):
        with self.assertRaises(ValidationError):
            ChatRequest(
                project_name="proj-demo",
                message="Please continue.",
            )

    # --- N6 transient attachment limit tests ---

    def test_transient_attachments_count_limit(self):
        from backend.material_limits import MAX_TRANSIENT_ATTACHMENTS
        big = [
            {
                "id": f"att-{i}",
                "name": f"{i}.png",
                "mime_type": "image/png",
                "data_url": "data:image/png;base64," + "A" * 20,
            }
            for i in range(MAX_TRANSIENT_ATTACHMENTS + 1)
        ]
        with self.assertRaisesRegex(Exception, "数量|过多|最多"):
            ChatRequest(project_id="p", message_text="看图", transient_attachments=big)

    def test_transient_oversized_decoded_rejected(self):
        # ~9MB of base64 → decoded ~6.75MB > 8MB limit?
        # base64 encodes 3 bytes as 4 chars, so 12*1024*1024 chars ≈ 9MB decoded
        huge_b64 = "A" * (12 * 1024 * 1024)
        with self.assertRaisesRegex(Exception, "大小|过大|字节"):
            ChatRequest(
                project_id="p",
                message_text="看图",
                transient_attachments=[
                    {
                        "id": "att-x",
                        "name": "x.png",
                        "mime_type": "image/png",
                        "data_url": "data:image/png;base64," + huge_b64,
                    }
                ],
            )

    def test_transient_count_ok_passes(self):
        ok = ChatRequest(
            project_id="p",
            message_text="看图",
            transient_attachments=[
                {
                    "id": "att-1",
                    "name": "a.png",
                    "mime_type": "image/png",
                    "data_url": "data:image/png;base64,Zg==",
                }
            ],
        )
        self.assertEqual(len(ok.transient_attachments), 1)

    def test_transient_disallowed_mime_rejected(self):
        with self.assertRaisesRegex(Exception, "不支持|格式|image/tiff"):
            ChatRequest(
                project_id="p",
                message_text="看图",
                transient_attachments=[
                    {
                        "id": "att-1",
                        "name": "a.tiff",
                        "mime_type": "image/tiff",
                        "data_url": "data:image/tiff;base64,Zg==",
                    }
                ],
            )

    def test_transient_malformed_data_url_raises_validation_error(self):
        # data_url without base64 payload — should raise a clear validation error, not 500
        with self.assertRaises(Exception):
            ChatRequest(
                project_id="p",
                message_text="看图",
                transient_attachments=[
                    {
                        "id": "att-1",
                        "name": "a.png",
                        "mime_type": "image/png",
                        "data_url": "not-a-valid-data-url",
                    }
                ],
            )

    # --- N6 security fixes: data_url MIME allowlist + strict base64 ---

    def test_transient_data_url_mime_mismatch_rejected(self):
        # Fix2: declaring mime_type="image/png" but smuggling a text/html data_url must be rejected.
        with self.assertRaisesRegex(Exception, "不一致|不被支持|格式"):
            ChatRequest(
                project_id="p",
                message_text="看图",
                transient_attachments=[
                    {
                        "id": "att-1",
                        "name": "evil.png",
                        "mime_type": "image/png",
                        "data_url": "data:text/html;base64,Zg==",
                    }
                ],
            )

    def test_transient_matching_data_url_mime_accepted(self):
        # Fix2: a data_url whose header mime matches the (allow-listed) mime_type field passes.
        ok = ChatRequest(
            project_id="p",
            message_text="看图",
            transient_attachments=[
                {
                    "id": "att-1",
                    "name": "a.png",
                    "mime_type": "image/png",
                    "data_url": "data:image/png;base64,Zg==",
                }
            ],
        )
        self.assertEqual(len(ok.transient_attachments), 1)
        self.assertEqual(ok.transient_attachments[0].data_url, "data:image/png;base64,Zg==")

    def test_transient_noncanonical_base64_rejected(self):
        # Fix3: a valid-looking but non-canonical base64 payload (invalid chars) must NOT be
        # silently accepted — strict decode surfaces a clean validation error, not a 500.
        with self.assertRaisesRegex(Exception, "损坏|无法解码"):
            ChatRequest(
                project_id="p",
                message_text="看图",
                transient_attachments=[
                    {
                        "id": "att-1",
                        "name": "a.png",
                        "mime_type": "image/png",
                        "data_url": "data:image/png;base64,AAAA!!!!",
                    }
                ],
            )

    def test_system_trigger_turn_passes_without_attachments(self):
        """system_trigger turns carry no transient_attachments — must not fail limit check."""
        req = ChatRequest(
            project_id="p",
            system_trigger="independent_review_done",
            run_id="run-abc",
            report_mtime_ns="1234567890",
        )
        self.assertEqual(req.system_trigger, "independent_review_done")
        self.assertEqual(req.transient_attachments, [])
