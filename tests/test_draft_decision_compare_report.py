import json
import unittest
import tempfile
from pathlib import Path
from tools.draft_decision_compare_report import generate_report

class CompareReportSmokeTests(unittest.TestCase):
    def test_minimal_fixture_outputs_markdown_with_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state = {
                "events": [
                    {"type": "draft_decision_compare",
                     "turn_id": "t1", "user_message_hash": "abc",
                     "old_decision": {"mode": "no_write"},
                     "new_decision": {"mode": "no_write", "preflight_keyword_intent": None},
                     "agreement": True, "divergence_reason": None,
                     "tag_present": {"begin": False, "continue": False, "section": False, "replace": False},
                     "fallback_used": False, "fallback_tool": None, "fallback_intent": None,
                     "blocked_missing_tag": False, "blocked_tool": None,
                     "new_channel_exception": None,
                     "recorded_at": "2026-05-04T00:00:00"},
                    # 一致 case，1 条
                    # 不一致 case
                    {"type": "draft_decision_compare", "turn_id": "t2",
                     "user_message_hash": "def",
                     "old_decision": {"mode": "no_write"},
                     "new_decision": {"mode": "require"},
                     "agreement": False, "divergence_reason": "old.mode=no_write, new.mode=require",
                     "tag_present": {"begin": True, "continue": False, "section": False, "replace": False},
                     "fallback_used": False, "fallback_tool": None, "fallback_intent": None,
                     "blocked_missing_tag": False, "blocked_tool": None,
                     "new_channel_exception": None,
                     "recorded_at": "2026-05-04T00:01:00"},
                    # missing-tag case
                    {"type": "draft_decision_compare", "turn_id": "t3",
                     "user_message_hash": "ghi",
                     "old_decision": {"mode": "require"},
                     "new_decision": {"mode": "require"},
                     "agreement": True, "divergence_reason": None,
                     "tag_present": {"begin": False, "continue": False, "section": False, "replace": False},
                     "fallback_used": False, "fallback_tool": None, "fallback_intent": None,
                     "blocked_missing_tag": True, "blocked_tool": "edit_file",
                     "new_channel_exception": None,
                     "recorded_at": "2026-05-04T00:02:00"},
                    # exception case
                    {"type": "draft_decision_exception", "turn_id": "t4",
                     "stage": "preflight",
                     "exception_class": "ValueError",
                     "exception_message": "test",
                     "recorded_at": "2026-05-04T00:03:00"},
                ]
            }
            state_path.write_text(json.dumps(state), encoding="utf-8")
            
            md = generate_report([state_path])
            
            # 五个 cutover 指标都能从 md 直接读出
            self.assertIn("一致率", md)
            self.assertIn("67%", md)  # 2/3 = 66.67%，{:.0f} 四舍五入到 67%
            self.assertIn("不一致 case", md)
            self.assertIn("blocked_missing_tag", md)
            self.assertIn("异常数", md)
            # 1 missing-tag + 1 exception
            self.assertEqual(md.count("✗"), 2)  # 至少 missing-tag 和 exception 行有 ✗
