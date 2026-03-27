import unittest

from backend.models import ChatRequest, ProjectInfo
from backend.main import SettingsUpdate


class ModelsSchemaTests(unittest.TestCase):
    def test_settings_update_accepts_managed_mode_payload(self):
        payload = SettingsUpdate(
            mode="managed",
            managed_base_url="https://newapi.z0y0h.work/client/v1",
            managed_model="gemini-3-flash",
            custom_api_base="",
            custom_api_key="",
            custom_model="",
        )
        self.assertEqual(payload.mode, "managed")
        self.assertEqual(payload.managed_model, "gemini-3-flash")

    def test_project_info_accepts_v2_fields(self):
        payload = ProjectInfo(
            name="演示项目",
            workspace_dir="D:/Workspaces/demo",
            project_type="strategy-consulting",
            theme="AI 战略规划",
            target_audience="高层决策者",
            deadline="2026-04-01",
            expected_length="3000字",
            notes="已有访谈纪要",
            initial_material_paths=[
                "D:/Workspaces/demo/资料/访谈纪要.txt",
                "D:/Workspaces/demo/资料/市场图表.png",
            ],
        )
        self.assertEqual(payload.workspace_dir, "D:/Workspaces/demo")
        self.assertEqual(payload.project_type, "strategy-consulting")
        self.assertEqual(payload.expected_length, "3000字")
        self.assertEqual(len(payload.initial_material_paths), 2)

    def test_project_info_accepts_legacy_report_type_field(self):
        payload = ProjectInfo(
            name="旧项目",
            workspace_dir="D:/Workspaces/legacy",
            report_type="research-report",
            theme="旧主题",
            target_audience="高层决策者",
            deadline="2026-04-01",
            expected_length="3000字",
            notes="",
        )
        self.assertEqual(payload.project_type, "research-report")

    def test_chat_request_accepts_project_id_and_attached_material_ids(self):
        payload = ChatRequest(
            project_id="proj-demo",
            message_text="请结合新增材料整理问题树",
            attached_material_ids=["mat-1", "mat-2"],
        )
        self.assertEqual(payload.project_id, "proj-demo")
        self.assertEqual(payload.message_text, "请结合新增材料整理问题树")
        self.assertEqual(payload.attached_material_ids, ["mat-1", "mat-2"])
