# tests/test_report_writing.py
import pathlib
import tempfile
import unittest

from backend.report_writing import (
    MAX_CANONICAL_MUTATIONS_PER_TURN,
    assistant_text_claims_modification,
    check_no_fetch_url_pending,
    check_no_mixed_intent_in_turn,
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
        handler = _FakeHandler(["export", "inspect_file"])
        msg = check_no_mixed_intent_in_turn(handler, "重写并导出并查看文件")
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
            "请把第二章重写一下",
            "全文重写这份报告正文",
            "整篇重写",
            "推倒重来",
            "全部改写",
            "第二章太弱了，改强一点",
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

    def test_negated_rewrite_requests_are_ambiguous(self):
        from backend.report_writing import detect_user_message_intent
        for msg in [
            "不是全文重写",
            "不想全文重写",
            "并非全文重写",
            "不是整篇重写",
            "不想整篇重写",
            "并非整篇重写",
            "不是全部改写",
            "不想全部改写",
            "并非全部改写",
            "不是重写第二章",
            "不想重写第二章",
            "并非重写第二章",
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


class UserMessageRequestsFullRewriteTests(unittest.TestCase):
    def test_positive_full_rewrite_requests_match(self):
        from backend.report_writing import user_message_requests_full_rewrite

        for msg in [
            "整篇重写这份报告",
            "全文重写这份报告正文",
            "推倒重来",
            "全部改写",
        ]:
            self.assertTrue(
                user_message_requests_full_rewrite(msg),
                f"expected full rewrite request for: {msg}",
            )

    def test_negated_full_rewrite_requests_do_not_match(self):
        from backend.report_writing import user_message_requests_full_rewrite

        for msg in [
            "不是全文重写，只把标题改一下",
            "不想全文重写",
            "并非整篇重写",
        ]:
            self.assertFalse(
                user_message_requests_full_rewrite(msg),
                f"expected no full rewrite request for: {msg}",
            )

    def test_generic_modify_request_does_not_match(self):
        from backend.report_writing import user_message_requests_full_rewrite

        self.assertFalse(user_message_requests_full_rewrite("把标题改一下"))


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
