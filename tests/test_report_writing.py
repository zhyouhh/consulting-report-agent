# tests/test_report_writing.py
import pathlib
import tempfile
import unittest
from unittest import mock

from backend.report_writing import (
    MAX_CANONICAL_MUTATIONS_PER_TURN,
    check_no_fetch_url_pending,
    check_no_prior_canonical_mutation_in_turn,
    check_outline_confirmed,
    check_read_before_write_canonical_draft,
    check_report_writing_stage,
    resolve_section_anchor,
)


class ResolveSectionAnchorTests(unittest.TestCase):
    DRAFT = (
        "# 报告标题\n"
        "## 第一章 引言\n"
        "引言正文 1\n"
        "引言正文 2\n"
        "## 第二章 战略选择\n"
        "战略正文 1\n"
        "## 第三章 实施路径\n"
        "实施正文 1\n"
    )

    def test_empty_anchor_returns_none(self):
        self.assertIsNone(resolve_section_anchor("", self.DRAFT))

    def test_empty_draft_returns_none(self):
        self.assertIsNone(resolve_section_anchor("## 第二章 战略选择", ""))

    def test_blank_h2_label_anchor_returns_none(self):
        self.assertIsNone(resolve_section_anchor("## ", "## \nbody\n"))

    def test_anchor_first_line_only_match_returns_full_section(self):
        result = resolve_section_anchor(
            "## 第二章 战略选择\n模型复述的旧正文不可信\n## 第三章 幻觉标题\n",
            self.DRAFT,
        )
        self.assertEqual(result, "## 第二章 战略选择\n战略正文 1\n")

    def test_single_line_anchor_matches(self):
        result = resolve_section_anchor("## 第一章 引言", self.DRAFT)
        self.assertEqual(
            result,
            "## 第一章 引言\n引言正文 1\n引言正文 2\n",
        )

    def test_label_not_in_draft_returns_none(self):
        self.assertIsNone(resolve_section_anchor("## 第四章 未知章节", self.DRAFT))

    def test_duplicate_label_returns_none(self):
        duplicate_draft = self.DRAFT + "## 第二章 战略选择\n另一段正文\n"
        self.assertIsNone(resolve_section_anchor("## 第二章 战略选择", duplicate_draft))

    def test_anchor_must_start_with_h2(self):
        for anchor in ["# 报告标题", "第二章 战略选择", "### 第二章 战略选择", "  ## 第二章 战略选择"]:
            with self.subTest(anchor=anchor):
                self.assertIsNone(resolve_section_anchor(anchor, self.DRAFT))

    def test_last_section_extends_to_eof(self):
        result = resolve_section_anchor("## 第三章 实施路径", self.DRAFT)
        self.assertEqual(result, "## 第三章 实施路径\n实施正文 1\n")

    def test_indented_draft_h2_is_not_matched(self):
        draft = (
            "# 报告标题\n"
            "## 第一章 引言\n"
            "引言正文\n"
            "  ## 第二章 战略选择\n"
            "战略正文\n"
        )
        self.assertIsNone(resolve_section_anchor("## 第二章 战略选择", draft))

    def test_indented_h2_inside_section_does_not_terminate_section(self):
        draft = (
            "# 报告标题\n"
            "## 第二章 战略选择\n"
            "战略正文 1\n"
            "  ## 第三章 实施路径\n"
            "这只是缩进文本，不是 h2\n"
            "战略正文 2\n"
            "## 第四章 落地计划\n"
            "落地正文\n"
        )
        result = resolve_section_anchor("## 第二章 战略选择", draft)
        self.assertEqual(
            result,
            "## 第二章 战略选择\n"
            "战略正文 1\n"
            "  ## 第三章 实施路径\n"
            "这只是缩进文本，不是 h2\n"
            "战略正文 2\n",
        )

    def test_h3_subheading_inside_section_is_preserved(self):
        draft = (
            "# 报告标题\n"
            "## 第二章 战略选择\n"
            "战略正文 1\n"
            "### 关键判断\n"
            "判断正文\n"
            "## 第三章 实施路径\n"
            "实施正文\n"
        )
        result = resolve_section_anchor("## 第二章 战略选择", draft)
        self.assertEqual(
            result,
            "## 第二章 战略选择\n"
            "战略正文 1\n"
            "### 关键判断\n"
            "判断正文\n",
        )

    def test_crlf_headings_match_and_preserve_line_endings(self):
        draft = (
            "# 报告标题\r\n"
            "## 第二章 战略选择\r\n"
            "战略正文 1\r\n"
            "## 第三章 实施路径\r\n"
            "实施正文 1\r\n"
        )
        result = resolve_section_anchor("## 第二章 战略选择\r\n旧正文不用信", draft)
        self.assertEqual(result, "## 第二章 战略选择\r\n战略正文 1\r\n")


class CheckHelpersTests(unittest.TestCase):
    def test_check_no_prior_canonical_mutation_in_turn_pass(self):
        self.assertIsNone(check_no_prior_canonical_mutation_in_turn({}))
        self.assertIsNone(check_no_prior_canonical_mutation_in_turn(
            {"canonical_draft_mutations": []},
        ))

    def test_check_no_fetch_url_pending_no_search_pass(self):
        self.assertIsNone(check_no_fetch_url_pending({}))

    def test_check_no_fetch_url_pending_search_no_fetch_reject(self):
        msg = check_no_fetch_url_pending(
            {"web_search_performed": True, "fetch_url_performed": False},
        )
        self.assertIsNotNone(msg)
        self.assertIn("fetch_url", msg)

    def test_check_no_fetch_url_pending_both_pass(self):
        self.assertIsNone(check_no_fetch_url_pending(
            {"web_search_performed": True, "fetch_url_performed": True},
        ))


class _FakeSkillEngine:
    """Minimal stub for invariant-check helpers."""
    REPORT_DRAFT_PATH = "content/report_draft_v1.md"

    def __init__(self, *, project_path=None, stage_code="S0", checkpoints=None):
        self._project_path = project_path
        self._stage_code = stage_code
        self._checkpoints = checkpoints or {}

    def get_project_path(self, project_id):
        return self._project_path

    def _infer_stage_state(self, project_path):
        return {"stage_code": self._stage_code}

    def _load_stage_checkpoints(self, project_path):
        return self._checkpoints

    def formal_content_write_block_guidance(self, project_id):
        if not self._project_path:
            return "项目不存在，无法写入正式内容。"
        if self._stage_code == "done":
            return "项目已归档。需要修改正文时，请先撤销交付归档，回到 S7 后再修改。"
        if self._stage_code not in {"S4", "S5", "S6", "S7"}:
            return f"本工具仅在 S4–S7 可用。当前阶段：{self._stage_code}"
        return None


class CheckReportWritingStageTests(unittest.TestCase):
    def test_project_missing_returns_error(self):
        engine = _FakeSkillEngine(project_path=None)
        self.assertIsNotNone(check_report_writing_stage(engine, "p1"))

    def test_stage_below_s4_rejected(self):
        engine = _FakeSkillEngine(project_path=pathlib.Path("/tmp/x"), stage_code="S2")
        msg = check_report_writing_stage(engine, "p1")
        self.assertIsNotNone(msg)
        self.assertIn("S4", msg)

    def test_stage_s4_accepted(self):
        engine = _FakeSkillEngine(project_path=pathlib.Path("/tmp/x"), stage_code="S4")
        self.assertIsNone(check_report_writing_stage(engine, "p1"))

    def test_stage_s7_accepted(self):
        engine = _FakeSkillEngine(project_path=pathlib.Path("/tmp/x"), stage_code="S7")
        self.assertIsNone(check_report_writing_stage(engine, "p1"))

    def test_done_archived_stage_rejected_with_actionable_s7_guidance(self):
        engine = _FakeSkillEngine(project_path=pathlib.Path("/tmp/x"), stage_code="done")
        msg = check_report_writing_stage(engine, "p1")
        self.assertIsNotNone(msg)
        self.assertIn("项目已归档", msg)
        self.assertIn("撤销交付归档", msg)
        self.assertIn("S7", msg)

    def test_delegates_to_shared_formal_content_gate_verbatim(self):
        engine = _FakeSkillEngine(project_path=pathlib.Path("/tmp/x"), stage_code="S4")
        engine.formal_content_write_block_guidance = mock.Mock(
            return_value="shared-state-sentinel"
        )

        result = check_report_writing_stage(engine, "p1")

        self.assertEqual(result, "shared-state-sentinel")
        engine.formal_content_write_block_guidance.assert_called_once_with("p1")


class CheckOutlineConfirmedTests(unittest.TestCase):
    def test_project_missing_returns_error(self):
        engine = _FakeSkillEngine(project_path=None)
        self.assertIsNotNone(check_outline_confirmed(engine, "p1"))

    def test_outline_not_confirmed_rejected(self):
        engine = _FakeSkillEngine(project_path=pathlib.Path("/tmp/x"), checkpoints={})
        msg = check_outline_confirmed(engine, "p1")
        self.assertIsNotNone(msg)
        self.assertIn("大纲", msg)

    def test_outline_confirmed_accepted(self):
        engine = _FakeSkillEngine(
            project_path=pathlib.Path("/tmp/x"),
            checkpoints={"outline_confirmed_at": "2026-05-06T00:00:00"},
        )
        self.assertIsNone(check_outline_confirmed(engine, "p1"))


class CheckReadBeforeWriteCanonicalDraftTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project_root = pathlib.Path(self._tmp.name)
        (self.project_root / "content").mkdir(parents=True, exist_ok=True)
        self.engine = _FakeSkillEngine(project_path=self.project_root)

    def tearDown(self):
        self._tmp.cleanup()

    def _draft_path(self):
        return self.project_root / self.engine.REPORT_DRAFT_PATH

    def test_draft_missing_returns_none(self):
        # 首次起草场景：无 draft → require_read 不阻断
        self.assertIsNone(check_read_before_write_canonical_draft(
            {}, self.engine, "p1", require_read=True,
        ))

    def test_require_read_false_skips_check(self):
        self._draft_path().write_text("# x\n", encoding="utf-8")
        self.assertIsNone(check_read_before_write_canonical_draft(
            {}, self.engine, "p1", require_read=False,
        ))

    def test_no_snapshot_rejects(self):
        self._draft_path().write_text("# x\n", encoding="utf-8")
        msg = check_read_before_write_canonical_draft(
            {"read_file_snapshots": {}}, self.engine, "p1", require_read=True,
        )
        self.assertIsNotNone(msg)
        self.assertIn("read_file", msg)

    def test_matching_mtime_passes(self):
        self._draft_path().write_text("# x\n", encoding="utf-8")
        mtime = self._draft_path().stat().st_mtime
        ctx = {"read_file_snapshots": {self.engine.REPORT_DRAFT_PATH: mtime}}
        self.assertIsNone(check_read_before_write_canonical_draft(
            ctx, self.engine, "p1", require_read=True,
        ))

    def test_stale_mtime_rejects(self):
        self._draft_path().write_text("# x\n", encoding="utf-8")
        ctx = {"read_file_snapshots": {self.engine.REPORT_DRAFT_PATH: 1.0}}
        msg = check_read_before_write_canonical_draft(
            ctx, self.engine, "p1", require_read=True,
        )
        self.assertIsNotNone(msg)
        self.assertIn("重新", msg)

    def test_non_numeric_snapshot_rejects(self):
        # Robustness: malformed snapshot value should not crash
        self._draft_path().write_text("# x\n", encoding="utf-8")
        ctx = {"read_file_snapshots": {self.engine.REPORT_DRAFT_PATH: "garbage"}}
        msg = check_read_before_write_canonical_draft(
            ctx, self.engine, "p1", require_read=True,
        )
        self.assertIsNotNone(msg)


class ReadBeforeWriteSelfRefreshTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project_root = pathlib.Path(self._tmp.name)
        (self.project_root / "content").mkdir(parents=True, exist_ok=True)
        self.engine = _FakeSkillEngine(project_path=self.project_root)
        self.draft_path = self.project_root / self.engine.REPORT_DRAFT_PATH
        self.draft_path.write_text("# x\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def _ctx_with_self_mutation(self, mtime_after):
        return {
            "canonical_draft_mutations": [
                {
                    "tool": "edit_file",
                    "canonical_action": "text_replace",
                    "target_label": "m1",
                    "old_len": 1,
                    "new_len": 1,
                    "mtime_after": mtime_after,
                    "ts": 0.0,
                }
            ],
            "read_file_snapshots": {},
        }

    def test_skip_when_current_mtime_matches_last_self_mutation(self):
        ctx = self._ctx_with_self_mutation(mtime_after=12345.0)

        def fake_stat(path):
            self.assertEqual(path, self.draft_path)

            class S:
                st_mtime = 12345.0

            return S()

        result = check_read_before_write_canonical_draft(
            ctx,
            self.engine,
            "p1",
            require_read=True,
            stat_func=fake_stat,
        )
        self.assertIsNone(result)

    def test_block_when_external_mtime_differs_from_last_self_mutation(self):
        ctx = self._ctx_with_self_mutation(mtime_after=12345.0)

        def fake_stat(path):
            self.assertEqual(path, self.draft_path)

            class S:
                st_mtime = 99999.0

            return S()

        result = check_read_before_write_canonical_draft(
            ctx,
            self.engine,
            "p1",
            require_read=True,
            stat_func=fake_stat,
        )
        self.assertIsNotNone(result)
        self.assertIn("read_file", result)

    def test_first_write_no_mutations_yet_requires_read(self):
        result = check_read_before_write_canonical_draft(
            {"canonical_draft_mutations": [], "read_file_snapshots": {}},
            self.engine,
            "p1",
            require_read=True,
        )
        self.assertIsNotNone(result)
        self.assertIn("read_file", result)

    def test_require_read_false_still_skips_check(self):
        result = check_read_before_write_canonical_draft(
            self._ctx_with_self_mutation(mtime_after=12345.0),
            self.engine,
            "p1",
            require_read=False,
        )
        self.assertIsNone(result)

    def test_malformed_mutation_falls_through_to_read_requirement(self):
        result = check_read_before_write_canonical_draft(
            {"canonical_draft_mutations": ["bad"], "read_file_snapshots": {}},
            self.engine,
            "p1",
            require_read=True,
        )
        self.assertIsNotNone(result)
        self.assertIn("read_file", result)


class MutationLimitTests(unittest.TestCase):
    def _ctx(self, mutations_count):
        return {
            "canonical_draft_mutations": [
                {
                    "tool": "edit_file",
                    "canonical_action": "text_replace",
                    "target_label": f"m{i}",
                    "old_len": 1,
                    "new_len": 1,
                    "mtime_after": 0.0,
                    "ts": 0.0,
                }
                for i in range(mutations_count)
            ]
        }

    def test_zero_mutations_passes(self):
        self.assertIsNone(check_no_prior_canonical_mutation_in_turn(self._ctx(0)))

    def test_below_cap_passes(self):
        self.assertIsNone(
            check_no_prior_canonical_mutation_in_turn(
                self._ctx(MAX_CANONICAL_MUTATIONS_PER_TURN - 1)
            )
        )

    def test_at_cap_blocks_next(self):
        msg = check_no_prior_canonical_mutation_in_turn(
            self._ctx(MAX_CANONICAL_MUTATIONS_PER_TURN)
        )
        self.assertIsNotNone(msg)
        self.assertIn(str(MAX_CANONICAL_MUTATIONS_PER_TURN), msg)

    def test_error_msg_summarizes_mutations(self):
        msg = check_no_prior_canonical_mutation_in_turn(
            self._ctx(MAX_CANONICAL_MUTATIONS_PER_TURN)
        )
        self.assertIn("text_replace", msg)
        self.assertIn("m0", msg)
        self.assertIn(f"m{MAX_CANONICAL_MUTATIONS_PER_TURN - 1}", msg)

    def test_non_list_mutations_field_passes(self):
        ctx = {"canonical_draft_mutations": {"tool": "edit_file"}}
        self.assertIsNone(check_no_prior_canonical_mutation_in_turn(ctx))

    def test_error_msg_summarizes_malformed_mutation_entries(self):
        entries = [
            {
                "canonical_action": "text_replace",
                "target_label": "m0",
                "old_len": 1,
                "new_len": 2,
            },
            "not-a-dict",
            None,
        ]
        # 补满到上限以触发限流分支，保留前三条畸形项验证摘要容错。
        while len(entries) < MAX_CANONICAL_MUTATIONS_PER_TURN:
            entries.append({"canonical_action": "noop", "target_label": "pad"})
        ctx = {"canonical_draft_mutations": entries}
        msg = check_no_prior_canonical_mutation_in_turn(ctx)
        self.assertIn("text_replace", msg)
        self.assertIn("m0", msg)
        self.assertIn("无法解析", msg)


if __name__ == "__main__":
    unittest.main()
