# tests/test_report_writing.py
import pathlib
import tempfile
import unittest

from backend.report_writing import (
    assistant_text_claims_modification,
    check_no_fetch_url_pending,
    check_no_mixed_intent_in_turn,
    check_no_prior_canonical_mutation_in_turn,
    check_outline_confirmed,
    check_read_before_write_canonical_draft,
    check_report_writing_stage,
    resolve_section_target,
)


def _fake_heading_nodes(items):
    """items: list[(label, snapshot, start, end)]"""
    return [
        {"label": label, "snapshot": snap, "start": s, "end": e, "section_snapshot": snap}
        for label, snap, s, e in items
    ]


class ResolveSectionTargetTests(unittest.TestCase):
    def setUp(self):
        self.draft = "# 报告\n## 第一章 引言\n内容0\n## 第二章 战力分析\n内容B\n## 第三章 总结\n内容C\n"
        self.nodes = _fake_heading_nodes([
            ("第一章 引言", "## 第一章 引言\n内容0", 5, 25),
            ("第二章 战力分析", "## 第二章 战力分析\n内容B", 25, 50),
            ("第三章 总结", "## 第三章 总结\n内容C", 50, 75),
        ])

    def test_unique_prefix_returns_target(self):
        result = resolve_section_target(
            "重写第二章", self.draft,
            extract_markdown_heading_nodes=lambda _: self.nodes,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["label"], "第二章 战力分析")

    def test_zero_candidates_returns_none(self):
        result = resolve_section_target(
            "重写第四章", self.draft,
            extract_markdown_heading_nodes=lambda _: self.nodes,
        )
        self.assertIsNone(result)

    def test_partial_multi_prefix_fail_fast(self):
        # 第二章 unique，第四章 not in draft → fail-fast
        result = resolve_section_target(
            "把第二章和第四章重写", self.draft,
            extract_markdown_heading_nodes=lambda _: self.nodes,
        )
        self.assertIsNone(result)

    def test_multi_prefix_distinct_targets_returns_none(self):
        # 两个 prefix 都 unique 但指向不同 heading
        result = resolve_section_target(
            "把第二章和第三章重写", self.draft,
            extract_markdown_heading_nodes=lambda _: self.nodes,
        )
        self.assertIsNone(result)

    def test_multi_prefix_same_target_returns_target(self):
        # 重复 prefix 都指向同一个 heading
        result = resolve_section_target(
            "第二章再说第二章", self.draft,
            extract_markdown_heading_nodes=lambda _: self.nodes,
        )
        self.assertIsNotNone(result)

    def test_section_node_compound_excluded(self):
        # 第二章节 不应匹配 第二章
        result = resolve_section_target(
            "改第二章节", "# 报告\n## 第二章 X\n内容\n",
            extract_markdown_heading_nodes=lambda _: _fake_heading_nodes(
                [("第二章 X", "## 第二章 X\n内容", 5, 30)],
            ),
        )
        self.assertIsNone(result)


class AssistantTextClaimsModificationTests(unittest.TestCase):
    def test_explicit_completion_returns_true(self):
        self.assertTrue(assistant_text_claims_modification(
            "我已经把第二章重写完毕，请查看。",
        ))
        self.assertTrue(assistant_text_claims_modification(
            "正文已同步更新到 content/report_draft_v1.md。",
        ))
        self.assertTrue(assistant_text_claims_modification(
            "草稿完成第三章的扩写。",
        ))

    def test_intent_only_returns_false(self):
        self.assertFalse(assistant_text_claims_modification(
            "我会重写第二章，请稍等。",
        ))
        self.assertFalse(assistant_text_claims_modification(
            "我准备开始起草正文。",
        ))

    def test_unrelated_text_returns_false(self):
        self.assertFalse(assistant_text_claims_modification(
            "我不太确定这块怎么处理。",
        ))

    def test_intent_plus_completion_returns_true(self):
        # "我会修改" + "已完成" 混合 — 仍按完成处理（model 在文本里同时混合时算撒谎风险）
        self.assertTrue(assistant_text_claims_modification(
            "我会重写第二章，已经完成了起草。",
        ))


class CheckHelpersTests(unittest.TestCase):
    def test_check_no_prior_canonical_mutation_in_turn_pass(self):
        self.assertIsNone(check_no_prior_canonical_mutation_in_turn({}))
        self.assertIsNone(check_no_prior_canonical_mutation_in_turn(
            {"canonical_draft_mutation": None},
        ))

    def test_check_no_prior_canonical_mutation_in_turn_reject(self):
        msg = check_no_prior_canonical_mutation_in_turn(
            {"canonical_draft_mutation": {"tool": "rewrite_report_section"}},
        )
        self.assertIsNotNone(msg)
        self.assertIn("本轮已经修改过", msg)

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


class _FakeHandler:
    def __init__(self, families):
        self._families = list(families)

    def _secondary_action_families_in_message(self, user_message):
        return self._families


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


class CheckNoMixedIntentInTurnTests(unittest.TestCase):
    def test_zero_secondary_actions_pass(self):
        handler = _FakeHandler([])
        self.assertIsNone(check_no_mixed_intent_in_turn(handler, "重写第二章"))

    def test_one_secondary_action_pass(self):
        handler = _FakeHandler(["export"])
        self.assertIsNone(check_no_mixed_intent_in_turn(handler, "重写第二章并导出"))

    def test_two_secondary_actions_reject(self):
        handler = _FakeHandler(["export", "quality_check"])
        msg = check_no_mixed_intent_in_turn(handler, "重写并导出并质检")
        self.assertIsNotNone(msg)
        self.assertIn("拆", msg)


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


if __name__ == "__main__":
    unittest.main()
