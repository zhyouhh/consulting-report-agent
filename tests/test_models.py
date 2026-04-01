import unittest

from pydantic import ValidationError

from backend.models import ChatRequest, ProjectInfo


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
                    "name": "bug.png",
                    "mime_type": "image/png",
                    "data_url": "data:image/png;base64,AAAA",
                }
            ],
        )

        self.assertEqual(len(payload.transient_attachments), 1)
        self.assertEqual(payload.transient_attachments[0].mime_type, "image/png")

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
