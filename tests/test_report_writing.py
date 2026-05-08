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
    detect_canonical_draft_write_obligation,
    resolve_section_anchor,
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
            {"canonical_draft_mutations": []},
        ))

    def test_check_no_prior_canonical_mutation_in_turn_ignores_legacy_field(self):
        self.assertIsNone(check_no_prior_canonical_mutation_in_turn(
            {"canonical_draft_mutation": {"tool": "rewrite_report_section"}},
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


class DetectWriteObligationTests(unittest.TestCase):
    def test_begin(self):
        d = detect_canonical_draft_write_obligation("开始写报告正文")
        self.assertEqual(d["tool_family"], "begin")

    def test_continue(self):
        d = detect_canonical_draft_write_obligation("继续写下一章")
        self.assertEqual(d["tool_family"], "continue")

    def test_section_rewrite_explicit(self):
        d = detect_canonical_draft_write_obligation("请把第二章重写一下")
        self.assertEqual(d["tool_family"], "rewrite_section")

    def test_section_rewrite_multi(self):
        d = detect_canonical_draft_write_obligation("重写第二章和第三章")
        self.assertEqual(d["tool_family"], "rewrite_section")

    def test_replace_text_quoted(self):
        d = detect_canonical_draft_write_obligation("把正文里的'渠道效率'改成'渠道质量'")
        self.assertEqual(d["tool_family"], "replace_text")

    def test_replace_text_unquoted(self):
        d = detect_canonical_draft_write_obligation("把报告里的增长改成高质量增长")
        self.assertEqual(d["tool_family"], "replace_text")

    def test_whole_rewrite_explicit(self):
        d = detect_canonical_draft_write_obligation("整篇重写，推倒重来")
        self.assertEqual(d["tool_family"], "rewrite_draft")

    def test_whole_rewrite_with_constraint(self):
        d = detect_canonical_draft_write_obligation("全文重写，但保留原来的章节结构")
        self.assertEqual(d["tool_family"], "rewrite_draft")

    def test_section_strong_change(self):
        d = detect_canonical_draft_write_obligation("第二章太弱了，改强一点")
        self.assertEqual(d["tool_family"], "rewrite_section")

    def test_continue_with_export(self):
        # mixed intent — detector 只输出 first match (continue)
        d = detect_canonical_draft_write_obligation("继续写到5000字，然后导出")
        self.assertEqual(d["tool_family"], "continue")


class DetectUserMessageIntentTests(unittest.TestCase):
    def test_generative_keywords_match(self):
        from backend.report_writing import detect_user_message_intent
        cases = [
            "帮我起草第一章",
            "续写下一章",
            "写下一段",
            "继续写",
            "帮我写完第二章",
        ]
        for msg in cases:
            self.assertEqual(detect_user_message_intent(msg), "generative",
                             f"expected generative for: {msg}")

    def test_modify_keywords_match(self):
        from backend.report_writing import detect_user_message_intent
        cases = [
            "把'增长'改成'增速'",
            "把第二章里的渠道效率改成渠道质量",
            "把第二章里的渠道效率替换为渠道质量",
            "把正文中的2025年预测改为2026年预测",
            "重写第二章",
            "替换第一段",
            "修改结论部分",
            "删掉最后一节",
            "调整第三章的措辞",
            "对第二章的结构和措辞进行调整",
            "帮我写得更顺一点",
            "写得更清楚一点",
            "请润色这一段",
        ]
        for msg in cases:
            self.assertEqual(detect_user_message_intent(msg), "modify",
                             f"expected modify for: {msg}")

    def test_positive_suggestion_forms_are_not_negated(self):
        from backend.report_writing import detect_user_message_intent
        cases = [
            ("不如继续写下一章", "generative"),
            ("要不继续写下一章", "generative"),
            ("不妨继续写", "generative"),
            ("不如润色一下这一段", "modify"),
        ]
        for msg, expected in cases:
            self.assertEqual(detect_user_message_intent(msg), expected,
                             f"expected {expected} for: {msg}")

    def test_preservation_constrained_polish_is_modify(self):
        from backend.report_writing import detect_user_message_intent
        for msg in [
            "不用改原意，帮我写得更顺一点",
            "不用改内容，请润色这一段",
            "请润色这一段，不用改结构",
        ]:
            self.assertEqual(detect_user_message_intent(msg), "modify",
                             f"expected modify for: {msg}")

    def test_pure_noop_edit_feedback_is_ambiguous(self):
        from backend.report_writing import detect_user_message_intent
        for msg in [
            "不用修改",
            "不用润色",
            "不需要优化",
            "这段不用调整",
            "不要修改",
            "不要润色",
            "先不要调整",
            "先不修改了",
            "不再润色",
            "别修改了",
            "修改就不用了",
            "润色先不用了",
        ]:
            self.assertEqual(detect_user_message_intent(msg), "ambiguous",
                             f"expected ambiguous for: {msg}")

    def test_negated_replacement_requests_are_ambiguous(self):
        from backend.report_writing import detect_user_message_intent
        for msg in [
            "不用把第二章里的渠道效率改成渠道质量",
            "不用再把第二章里的渠道效率改成渠道质量",
            "不要把正文中的2025年预测改为2026年预测",
            "不要再把第二章里的渠道效率改成渠道质量",
            "不用把第二章里的渠道效率替换为渠道质量",
            "别把第二章里的渠道效率改成渠道质量",
            "别再把第二章里的渠道效率改成渠道质量",
            "先不要再把第二章里的渠道效率改成渠道质量",
            "先不把第二章里的渠道效率改成渠道质量了",
            "不再把正文中的2025年预测改为2026年预测",
            "把第二章里的渠道效率改成渠道质量就不用了",
            "把正文中的2025年预测改为2026年预测先不用了",
        ]:
            self.assertEqual(detect_user_message_intent(msg), "ambiguous",
                             f"expected ambiguous for: {msg}")

    def test_long_same_clause_negated_edit_requests_are_ambiguous(self):
        from backend.report_writing import detect_user_message_intent
        for msg in [
            "不要对第二章的结构和措辞进行调整",
            "不用对第二章的结构和措辞做修改",
            "别对最后一节的表述进行润色",
        ]:
            self.assertEqual(detect_user_message_intent(msg), "ambiguous",
                             f"expected ambiguous for: {msg}")

    def test_negated_write_requests_are_ambiguous(self):
        from backend.report_writing import detect_user_message_intent
        for msg in [
            "不用继续写",
            "先不用起草",
            "不用写下一章",
            "别继续写了",
            "先别继续写",
            "先不继续写了",
            "不再继续写",
            "别写下一章",
            "继续写就不用了",
            "写下一章不用了",
        ]:
            self.assertEqual(detect_user_message_intent(msg), "ambiguous",
                             f"expected ambiguous for: {msg}")

    def test_ambiguous_returns_ambiguous(self):
        from backend.report_writing import detect_user_message_intent
        for msg in [
            "",
            "看一下背景",
            "你好",
            "ok 继续",
            "上一段写得不错",
            "这段写得很好，不用改",
            "这段写得很清楚，不用改",
            "上一段写得自然",
            "上一段写得很通顺，不用改",
        ]:
            self.assertEqual(detect_user_message_intent(msg), "ambiguous",
                             f"expected ambiguous for: {msg}")

    def test_chinese_punctuation_does_not_break(self):
        from backend.report_writing import detect_user_message_intent
        self.assertEqual(
            detect_user_message_intent("把第二章里的'30%'改成'三成'，谢谢。"),
            "modify",
        )


class MutationLimit3Tests(unittest.TestCase):
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

    def test_two_mutations_passes(self):
        self.assertIsNone(check_no_prior_canonical_mutation_in_turn(self._ctx(2)))

    def test_three_mutations_blocks_fourth(self):
        msg = check_no_prior_canonical_mutation_in_turn(self._ctx(3))
        self.assertIsNotNone(msg)
        self.assertIn("3", msg)

    def test_error_msg_summarizes_mutations(self):
        msg = check_no_prior_canonical_mutation_in_turn(self._ctx(3))
        self.assertIn("text_replace", msg)
        self.assertIn("m0", msg)
        self.assertIn("m2", msg)

    def test_legacy_field_name_returns_none(self):
        ctx = {"canonical_draft_mutation": {"tool": "rewrite_report_section"}}
        self.assertIsNone(check_no_prior_canonical_mutation_in_turn(ctx))

    def test_non_list_mutations_field_passes(self):
        ctx = {"canonical_draft_mutations": {"tool": "edit_file"}}
        self.assertIsNone(check_no_prior_canonical_mutation_in_turn(ctx))

    def test_error_msg_summarizes_malformed_mutation_entries(self):
        ctx = {
            "canonical_draft_mutations": [
                {
                    "canonical_action": "text_replace",
                    "target_label": "m0",
                    "old_len": 1,
                    "new_len": 2,
                },
                "not-a-dict",
                None,
            ]
        }
        msg = check_no_prior_canonical_mutation_in_turn(ctx)
        self.assertIn("text_replace", msg)
        self.assertIn("m0", msg)
        self.assertIn("无法解析", msg)


if __name__ == "__main__":
    unittest.main()
