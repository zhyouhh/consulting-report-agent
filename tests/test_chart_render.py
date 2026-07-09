"""图表渲染引擎测试：chart_render（数据图）+ diagram_render（结构图）。

覆盖：全 kind 出非空 PNG、字体注册、坏输入/超限归一 ChartRenderError、
并发渲染安全（Agg/OO 无 pyplot 全局）、无 pyplot 依赖 source-guard。
"""

import concurrent.futures
import unittest
from pathlib import Path

from backend import chart_limits
from backend.chart_render import CHART_KINDS, render_chart
from backend.chart_style import ChartRenderError, ensure_fonts_registered, FONT_FAMILY
from backend.diagram_render import DIAGRAM_KINDS, render_diagram

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
ROOT = Path(__file__).resolve().parents[1]


CHART_FIXTURES = {
    "bar": {"categories": ["华东", "华南"], "values": [420, 380]},
    "grouped_bar": {"categories": ["2024", "2025"],
                    "series": [{"name": "甲", "values": [1, 2]}, {"name": "乙", "values": [3, 4]}]},
    "stacked_bar": {"categories": ["Q1", "Q2"],
                    "series": [{"name": "线上", "values": [45, 52]}, {"name": "线下", "values": [88, 82]}]},
    "horizontal_bar": {"categories": ["产品力", "渠道"], "values": [8.6, 7.9]},
    "line": {"x": ["2024", "2025", "2026"],
             "series": [{"name": "规模", "values": [58, 72, 95]}]},
    "pie": {"labels": ["A", "B", "其他"], "values": [31, 22, 47]},
    "donut": {"labels": ["A", "B"], "values": [60, 40]},
    "waterfall": {"steps": [{"label": "期初", "delta": 120}, {"label": "增收", "delta": 45},
                            {"label": "成本", "delta": -38}], "total_label": "期末"},
    "funnel": {"stages": [{"label": "线索", "value": 5200}, {"label": "成交", "value": 148}]},
    "scatter": {"points": [{"x": 3.2, "y": 12, "label": "产品A"}, {"x": 5.1, "y": 8}]},
    "bubble": {"points": [{"x": 12, "y": 8, "size": 45, "label": "华东"},
                          {"x": 8, "y": 22, "size": 18}]},
    "heatmap": {"rows": ["战略", "财务"], "cols": ["方案一", "方案二"], "values": [[8, 6], [7, 9]]},
}

DIAGRAM_FIXTURES = {
    "matrix_2x2": {"x_axis": {"label": "吸引力", "low": "低", "high": "高"},
                   "y_axis": {"label": "能力", "low": "低", "high": "高"},
                   "quadrant_labels": ["观察", "进攻", "收缩", "跟进"],
                   "items": [{"label": "业务A", "x": 0.7, "y": 0.8}]},
    "value_chain": {"primary": [{"label": "研发"}, {"label": "生产"}, {"label": "销售"}],
                    "support": ["人力资源", "信息化"]},
    "process": {"steps": [{"label": "受理", "note": "1 天"}, {"label": "评审"}, {"label": "签约"}]},
    "roadmap": {"phases": [{"label": "夯基", "period": "2026 H1", "items": ["组织调整", "系统选型"]},
                           {"label": "推广", "items": ["全面复制"]}]},
    "pyramid": {"layers": [{"label": "愿景", "note": "领先服务商"}, {"label": "战略"}, {"label": "举措"}]},
    "flowchart": {"nodes": [{"id": "a", "label": "开始", "shape": "rounded"},
                            {"id": "b", "label": "审查"},
                            {"id": "c", "label": "合规?", "shape": "diamond"},
                            {"id": "d", "label": "放行"}],
                  "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "c"},
                            {"from": "c", "to": "d", "label": "是"}]},
    "org_chart": {"root": {"label": "总部", "children": [
        {"label": "运营部", "children": [{"label": "华东"}]}, {"label": "数字化中心"}]}},
    "tree": {"root": {"label": "降本", "children": [{"label": "采购"}, {"label": "运营"}]}},
}


class FontRegistrationTests(unittest.TestCase):
    def test_bundled_fonts_exist_in_repo(self):
        # 字体是仓库资产不是可选项（spec §5）：服务器/打包机默认无中文字体。
        for name in ("NotoSansCJKsc-Regular.otf", "NotoSansCJKsc-Bold.otf"):
            self.assertTrue((ROOT / "fonts" / name).is_file(), f"fonts/{name} 缺失")

    def test_ensure_fonts_registered_idempotent(self):
        self.assertEqual(ensure_fonts_registered(), FONT_FAMILY)
        self.assertEqual(ensure_fonts_registered(), FONT_FAMILY)
        from matplotlib import font_manager
        families = {f.name for f in font_manager.fontManager.ttflist}
        self.assertIn(FONT_FAMILY, families)


class ChartRenderTests(unittest.TestCase):
    def test_all_kinds_render_nonempty_png(self):
        for kind in CHART_KINDS:
            with self.subTest(kind=kind):
                png = render_chart(kind, CHART_FIXTURES[kind], f"{kind} 结论式标题", "测试来源")
                self.assertTrue(png.startswith(PNG_MAGIC))
                self.assertGreater(len(png), 5000)

    def test_unknown_kind_raises(self):
        with self.assertRaises(ChartRenderError):
            render_chart("radar", {"x": [1]}, "标题", "来源")

    def test_missing_title_or_source_raises(self):
        with self.assertRaises(ChartRenderError):
            render_chart("bar", CHART_FIXTURES["bar"], "", "来源")
        with self.assertRaises(ChartRenderError):
            render_chart("bar", CHART_FIXTURES["bar"], "标题", "")

    def test_bad_data_shape_raises_friendly(self):
        with self.assertRaises(ChartRenderError) as ctx:
            render_chart("bar", {"categories": ["a"], "values": [1, 2]}, "标题", "来源")
        self.assertIn("一致", str(ctx.exception))

    def test_nan_and_bool_rejected(self):
        with self.assertRaises(ChartRenderError):
            render_chart("bar", {"categories": ["a"], "values": [float("nan")]}, "标题", "来源")
        with self.assertRaises(ChartRenderError):
            render_chart("bar", {"categories": ["a"], "values": [True]}, "标题", "来源")

    def test_series_limit_enforced(self):
        data = {"categories": ["a"],
                "series": [{"name": f"s{i}", "values": [1]} for i in range(chart_limits.MAX_SERIES + 1)]}
        with self.assertRaises(ChartRenderError) as ctx:
            render_chart("grouped_bar", data, "标题", "来源")
        self.assertIn("上限", str(ctx.exception))

    def test_title_length_limit(self):
        with self.assertRaises(ChartRenderError):
            render_chart("bar", CHART_FIXTURES["bar"], "长" * (chart_limits.MAX_TITLE_CHARS + 1), "来源")

    def test_stacked_bar_rejects_negative(self):
        data = {"categories": ["a"], "series": [{"name": "s", "values": [-1]}]}
        with self.assertRaises(ChartRenderError) as ctx:
            render_chart("stacked_bar", data, "标题", "来源")
        self.assertIn("waterfall", str(ctx.exception))

    def test_forecast_from_must_be_in_x(self):
        data = {"x": ["2024", "2025"], "series": [{"name": "s", "values": [1, 2]}]}
        with self.assertRaises(ChartRenderError):
            render_chart("line", data, "标题", "来源", {"forecast_from": "2030"})

    def test_concurrent_render_thread_safe(self):
        # spec §4.8：多项目并发渲染跑在不同 worker 线程；Agg+OO 必须不串图不崩。
        def work(i):
            kind = list(CHART_KINDS)[i % len(CHART_KINDS)]
            return render_chart(kind, CHART_FIXTURES[kind], f"并发 {i}", "来源")

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(work, range(16)))
        for png in results:
            self.assertTrue(png.startswith(PNG_MAGIC))


class DiagramRenderTests(unittest.TestCase):
    def test_all_kinds_render_nonempty_png(self):
        for kind in DIAGRAM_KINDS:
            with self.subTest(kind=kind):
                png = render_diagram(kind, DIAGRAM_FIXTURES[kind], f"{kind} 结论式标题", "测试来源")
                self.assertTrue(png.startswith(PNG_MAGIC))
                self.assertGreater(len(png), 5000)

    def test_source_optional_for_diagram(self):
        png = render_diagram("process", DIAGRAM_FIXTURES["process"], "标题", None)
        self.assertTrue(png.startswith(PNG_MAGIC))

    def test_flowchart_cycle_rejected_friendly(self):
        spec = {"nodes": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
                "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}]}
        with self.assertRaises(ChartRenderError) as ctx:
            render_diagram("flowchart", spec, "标题", None)
        self.assertIn("环", str(ctx.exception))

    def test_flowchart_unknown_edge_endpoint_rejected(self):
        spec = {"nodes": [{"id": "a", "label": "A"}], "edges": [{"from": "a", "to": "zz"}]}
        with self.assertRaises(ChartRenderError):
            render_diagram("flowchart", spec, "标题", None)

    def test_tree_depth_limit(self):
        node = {"label": "leaf"}
        for _ in range(chart_limits.MAX_TREE_DEPTH + 1):
            node = {"label": "n", "children": [node]}
        with self.assertRaises(ChartRenderError) as ctx:
            render_diagram("tree", {"root": node}, "标题", None)
        self.assertIn("层", str(ctx.exception))

    def test_tree_node_count_limit(self):
        root = {"label": "root", "children": [{"label": f"c{i}"} for i in range(chart_limits.MAX_TREE_NODES)]}
        with self.assertRaises(ChartRenderError):
            render_diagram("org_chart", {"root": root}, "标题", None)

    def test_matrix_requires_four_quadrant_labels(self):
        spec = dict(DIAGRAM_FIXTURES["matrix_2x2"], quadrant_labels=["只有一个"])
        with self.assertRaises(ChartRenderError):
            render_diagram("matrix_2x2", spec, "标题", None)

    def test_matrix_item_position_must_be_0_to_1(self):
        spec = {**DIAGRAM_FIXTURES["matrix_2x2"], "items": [{"label": "越界", "x": 1.5, "y": 0.5}]}
        with self.assertRaises(ChartRenderError):
            render_diagram("matrix_2x2", spec, "标题", None)

    def test_unknown_kind_raises(self):
        with self.assertRaises(ChartRenderError):
            render_diagram("sankey", {}, "标题", None)


class RendererSourceGuardTests(unittest.TestCase):
    def test_renderers_never_touch_pyplot(self):
        # spec §4.8 硬约束：pyplot 全局状态机非线程安全，渲染器只许 OO API。
        for module in ("chart_render.py", "diagram_render.py", "chart_style.py"):
            src = (ROOT / "backend" / module).read_text(encoding="utf-8")
            self.assertNotIn("pyplot", src, f"{module} 不得使用 pyplot")

    def test_leaf_modules_never_import_chat_or_skill(self):
        for module in ("chart_render.py", "diagram_render.py", "chart_style.py",
                       "chart_assets.py", "chart_limits.py"):
            src = (ROOT / "backend" / module).read_text(encoding="utf-8")
            for forbidden in ("from .chat", "from backend.chat", "import chat",
                              "from .skill", "from backend.skill",
                              "from .main", "from backend.main"):
                self.assertNotIn(forbidden, src, f"{module} 叶子铁律：不得依赖 {forbidden}")


if __name__ == "__main__":
    unittest.main()
