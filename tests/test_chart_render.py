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


def _linear_flow(labels, shapes=None):
    nodes = []
    for i, label in enumerate(labels):
        node = {"id": f"n{i}", "label": label}
        if shapes and shapes.get(i):
            node["shape"] = shapes[i]
        nodes.append(node)
    edges = [{"from": f"n{i}", "to": f"n{i + 1}"} for i in range(len(labels) - 1)]
    return {"nodes": nodes, "edges": edges}


# 陈燕 0710 反馈 #6 锚样例：12 节点近线性链、双段中文标签（proj-c051 sidecar 同形态）。
CHENYAN_ANCHOR_LABELS = [
    "开始", "规则需求收集 [业务部门]", "规则定义与转化 [领域数据架构师]",
    "规则评审 [数据治理委员会]", "规则入库登记 [数据管理员]", "规则配置部署 [平台运维]",
    "试运行验证 [质量专员]", "正式发布 [数据治理办公室]", "质量监测执行 [质量专员]",
    "问题整改跟踪 [业务部门]", "规则优化迭代 [领域数据架构师]", "结束",
]


class FlowchartLayoutTests(unittest.TestCase):
    """flowchart 布局修复（2026-07-11，0710 反馈 #6）：方向自适应 + 字随框走 + 友好失败。"""

    def _layout(self, spec):
        from backend.diagram_render import _flow_layers, _flow_layout, _parse_flow
        nodes, edges = _parse_flow(spec)
        layers = _flow_layers(nodes, edges)
        return _flow_layout(nodes, layers)

    def _assert_no_overlap(self, boxes):
        entries = list(boxes.items())
        for i in range(len(entries)):
            for j in range(i + 1, len(entries)):
                (na, a), (nb, b) = entries[i], entries[j]
                overlap_x = abs(a["cx"] - b["cx"]) < (a["w"] + b["w"]) / 2
                overlap_y = abs(a["cy"] - b["cy"]) < (a["h"] + b["h"]) / 2
                self.assertFalse(overlap_x and overlap_y, f"节点框重叠: {na} vs {nb}")

    def _assert_in_bounds(self, boxes):
        for nid, box in boxes.items():
            self.assertGreaterEqual(box["cx"] - box["w"] / 2, -1e-6, f"{nid} 左越界")
            self.assertLessEqual(box["cx"] + box["w"] / 2, 1 + 1e-6, f"{nid} 右越界")
            self.assertGreaterEqual(box["cy"] - box["h"] / 2, -1e-6, f"{nid} 下越界")
            self.assertLessEqual(box["cy"] + box["h"] / 2, 1 + 1e-6, f"{nid} 上越界")

    def test_anchor_case_12_node_linear_goes_vertical(self):
        # 锚样例必须走纵向（n_layers≈12 ≥ FLOW_VERTICAL_MIN_LAYERS 且 max_rows≤2）。
        spec = _linear_flow(CHENYAN_ANCHOR_LABELS, {0: "rounded", 6: "diamond", 11: "rounded"})
        layout = self._layout(spec)
        self.assertTrue(layout["vertical"])
        self.assertLessEqual(layout["fig_h"], chart_limits.MAX_FIGURE_HEIGHT_IN + 1e-6)
        self._assert_no_overlap(layout["boxes"])
        self._assert_in_bounds(layout["boxes"])
        png = render_diagram("flowchart", spec, "数据质量规则管理流程", "内部访谈整理")
        self.assertTrue(png.startswith(PNG_MAGIC))
        self.assertGreater(len(png), 5000)

    def test_anchor_labels_fit_single_line_vertically(self):
        # 纵向整行宽度下，锚样例的双段中文标签不应再被断行（修复前被挤成叠压碎行）。
        spec = _linear_flow(CHENYAN_ANCHOR_LABELS)
        layout = self._layout(spec)
        for nid, box in layout["boxes"].items():
            self.assertEqual(len(box["lines"]), 1, f"{nid} 标签被意外断行: {box['lines']}")

    def test_6_layer_linear_goes_vertical(self):
        # 横向 5 层起列宽已装不下中文标签（实测溢出）→ 判据下探到 5，6 层线性必走纵向。
        spec = _linear_flow([
            "数据标准制定 [数据治理委员会]", "标准发布与宣贯 [数据治理办公室]",
            "标准落地实施 [各业务部门]", "执行情况检查 [质量专员]",
            "考核与通报 [人力资源部]", "标准修订完善 [领域数据架构师]",
        ])
        layout = self._layout(spec)
        self.assertTrue(layout["vertical"])
        self._assert_no_overlap(layout["boxes"])

    def test_4_layer_linear_stays_horizontal(self):
        spec = _linear_flow(["受理申请", "资格审查", "领导审批", "归档发布"])
        layout = self._layout(spec)
        self.assertFalse(layout["vertical"])
        self._assert_no_overlap(layout["boxes"])
        self._assert_in_bounds(layout["boxes"])

    def test_wide_shallow_multibranch_stays_horizontal(self):
        # 宽而浅（3 层、中层 4 节点）不满足近线性判据 → 保持横向。
        spec = {
            "nodes": [{"id": "s", "label": "需求输入", "shape": "rounded"},
                      {"id": "a", "label": "渠道A评估"}, {"id": "b", "label": "渠道B评估"},
                      {"id": "c", "label": "渠道C评估"}, {"id": "d", "label": "渠道D评估"},
                      {"id": "t", "label": "汇总决策", "shape": "diamond"}],
            "edges": [{"from": "s", "to": x} for x in "abcd"]
                     + [{"from": x, "to": "t"} for x in "abcd"],
        }
        layout = self._layout(spec)
        self.assertFalse(layout["vertical"])
        png = render_diagram("flowchart", spec, "多渠道并行评估", None)
        self.assertTrue(png.startswith(PNG_MAGIC))

    def test_vertical_layer_cap_friendly_fail(self):
        # 13 层 > FLOW_MAX_VERTICAL_LAYERS(12) → 人话错误（拆分子流程），不产糊图。
        spec = _linear_flow([f"步骤{i:02d}处理" for i in range(chart_limits.FLOW_MAX_VERTICAL_LAYERS + 1)])
        with self.assertRaises(ChartRenderError) as ctx:
            render_diagram("flowchart", spec, "超长流程", None)
        self.assertIn("拆分", str(ctx.exception))
        # process 模板上限 8 步，>12 层的流程它也装不下 → 失败文案不得再推荐 process
        self.assertNotIn("process", str(ctx.exception))

    def test_deep_multibranch_horizontal_unreadable_friendly_fail(self):
        # 10+ 层且含 3 节点层：不满足纵向判据、横向列宽又低于可读下限 → 友好失败。
        spec = _linear_flow([f"环节{i}审查处理" for i in range(10)])
        spec["nodes"] += [{"id": "x1", "label": "并行会签A"}, {"id": "x2", "label": "并行会签B"}]
        spec["edges"] += [{"from": "n3", "to": "x1"}, {"from": "n3", "to": "x2"},
                          {"from": "x1", "to": "n5"}, {"from": "x2", "to": "n5"}]
        with self.assertRaises(ChartRenderError) as ctx:
            render_diagram("flowchart", spec, "深多分支", None)
        self.assertIn("拆分", str(ctx.exception))

    def test_max_flow_nodes_limit_still_enforced(self):
        spec = _linear_flow([f"节点{i}" for i in range(chart_limits.MAX_FLOW_NODES + 1)])
        with self.assertRaises(ChartRenderError) as ctx:
            render_diagram("flowchart", spec, "标题", None)
        self.assertIn("节点过多", str(ctx.exception))

    def test_horizontal_labels_wrap_within_box_width(self):
        # 字随框走（横向）：断行后每行字数不超过列宽反推的容量。
        spec = _linear_flow(["受理申请与初审", "跨部门联合资格审查", "分管领导审批", "归档发布"])
        layout = self._layout(spec)
        self.assertFalse(layout["vertical"])
        for nid, box in layout["boxes"].items():
            box_w_in = box["w"] * chart_limits.MAX_FIGURE_WIDTH_IN * 0.96
            char_in = box["fontsize"] / 72.0
            for line in box["lines"]:
                self.assertLessEqual(
                    len(line) * char_in, box_w_in + 1e-6,
                    f"{nid} 行「{line}」超出框宽",
                )

    def test_vertical_two_column_layer_no_overlap(self):
        # 5 层含一个双节点层（max_rows=2 仍近线性）→ 纵向、双列互不重叠。
        spec = _linear_flow(["项目立项 [项目办]", "需求分析 [业务部门]", "方案设计 [技术部]",
                             "实施部署 [实施组]", "验收归档 [项目办]"])
        spec["nodes"].append({"id": "p", "label": "风险评估 [风控部]"})
        spec["edges"] += [{"from": "n0", "to": "p"}, {"from": "p", "to": "n2"}]
        layout = self._layout(spec)
        self.assertTrue(layout["vertical"])
        self._assert_no_overlap(layout["boxes"])
        self._assert_in_bounds(layout["boxes"])

    def test_vertical_min_row_height_respected(self):
        # 每层行高 ≥ FLOW_MIN_ROW_HEIGHT_IN：axes 高度 / 层数不小于最小行高。
        spec = _linear_flow(CHENYAN_ANCHOR_LABELS)
        layout = self._layout(spec)
        axes_h_in = layout["fig_h"] * 0.8
        n_layers = len(CHENYAN_ANCHOR_LABELS)
        self.assertGreaterEqual(
            axes_h_in / n_layers, chart_limits.FLOW_MIN_ROW_HEIGHT_IN - 1e-6
        )


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
