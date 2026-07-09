"""数据图渲染器（叶子模块）：结构化数据 → 咨询级 PNG。

依赖 chart_style / chart_limits + matplotlib + stdlib，绝不依赖 chat/skill/main。
入口 `render_chart(kind, data, title, source, options) -> bytes(PNG)`；
任何坏输入/超限统一抛 ChartRenderError（人话 message，给模型自愈用）。

设计原则（spec §4.7 附录 A）：模型不写代码只给数据，版式/配色/标签是后端固定手艺——
一图一结论（标题即结论）、数据标签直读、来源脚注、无 chart junk。
"""

from __future__ import annotations

import math
from typing import Dict, List, Sequence

from . import chart_limits as limits
from .chart_style import (
    GRID, MUTED, NAVY, NEGATIVE, POSITIVE, TEXT, FONT_FAMILY,
    ChartRenderError, apply_axes_style, finalize_png, new_figure, series_color,
)

CHART_KINDS = (
    "bar", "grouped_bar", "stacked_bar", "horizontal_bar",
    "line", "pie", "donut", "waterfall", "funnel",
    "scatter", "bubble", "heatmap",
)

_LABEL_KW = {"fontfamily": FONT_FAMILY}


# ---------------- 校验助手 ----------------

def _fail(msg: str) -> None:
    raise ChartRenderError(msg)


def _require_dict(value, name: str) -> Dict:
    if not isinstance(value, dict):
        _fail(f"{name} 必须是对象")
    return value


def _clean_text(value, name: str, max_chars: int) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(f"{name} 必须是非空字符串")
    text = " ".join(value.split())
    if len(text) > max_chars:
        _fail(f"{name} 过长（{len(text)} 字符，上限 {max_chars}）")
    return text


def _number(value, name: str):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"{name} 必须是数字")
    if math.isnan(value) or math.isinf(value):
        _fail(f"{name} 不能是 NaN/Inf")
    return float(value)


def _number_list(values, name: str, max_len: int) -> List[float]:
    if not isinstance(values, (list, tuple)) or not values:
        _fail(f"{name} 必须是非空数组")
    if len(values) > max_len:
        _fail(f"{name} 数据点过多（{len(values)} 个，上限 {max_len}）")
    return [_number(v, f"{name}[{i}]") for i, v in enumerate(values)]


def _label_list(values, name: str, max_len: int) -> List[str]:
    if not isinstance(values, (list, tuple)) or not values:
        _fail(f"{name} 必须是非空数组")
    if len(values) > max_len:
        _fail(f"{name} 条目过多（{len(values)} 个，上限 {max_len}）")
    return [_clean_text(v, f"{name}[{i}]", limits.MAX_LABEL_CHARS) for i, v in enumerate(values)]


def _series_list(data: Dict, *, value_len: int | None = None) -> List[Dict]:
    raw = data.get("series")
    if not isinstance(raw, (list, tuple)) or not raw:
        _fail("data.series 必须是非空数组")
    if len(raw) > limits.MAX_SERIES:
        _fail(f"序列过多（{len(raw)} 条，上限 {limits.MAX_SERIES}）")
    series = []
    for i, item in enumerate(raw):
        item = _require_dict(item, f"series[{i}]")
        name = _clean_text(item.get("name"), f"series[{i}].name", limits.MAX_LABEL_CHARS)
        values = _number_list(item.get("values"), f"series[{i}].values", limits.MAX_POINTS_PER_SERIES)
        if value_len is not None and len(values) != value_len:
            _fail(f"series[{i}].values 长度（{len(values)}）与类目数（{value_len}）不一致")
        series.append({"name": name, "values": values, "style": item.get("style")})
    return series


def _fmt(value: float) -> str:
    """数据标签数字格式：整数不带小数，其余保留 1 位。"""
    if abs(value - round(value)) < 1e-9:
        return f"{int(round(value)):,}"
    return f"{value:,.1f}"


def _figure(height: float):
    return new_figure(limits.MAX_FIGURE_WIDTH_IN, min(height, limits.MAX_FIGURE_HEIGHT_IN))


def _new_axes(fig, *, y_grid: bool = True, rect=(0.09, 0.14, 0.88, 0.72)):
    ax = fig.add_axes(rect)
    apply_axes_style(ax, y_grid=y_grid)
    return ax


def _legend(ax, handles=None, labels=None) -> None:
    kwargs = dict(
        loc="upper right", bbox_to_anchor=(1.0, 1.13), ncols=4, frameon=False,
        fontsize=8, labelcolor=TEXT, prop={"family": FONT_FAMILY, "size": 8},
    )
    if handles is not None:
        ax.legend(handles, labels, **kwargs)
    else:
        ax.legend(**kwargs)


def _bar_label(ax, x, y, text: str, *, above: bool = True) -> None:
    offset = 3 if above else -10
    ax.annotate(
        text, (x, y), textcoords="offset points", xytext=(0, offset),
        ha="center", va="bottom" if above else "top",
        fontsize=7.5, color=TEXT, **_LABEL_KW,
    )


def _category_ticks(ax, positions, labels) -> None:
    ax.set_xticks(list(positions))
    ax.set_xticklabels(labels, fontsize=8.5, color=TEXT, **_LABEL_KW)
    # 类目多或标签长时斜排，防重叠
    if len(labels) > 6 or max(len(l) for l in labels) > 6:
        for tick in ax.get_xticklabels():
            tick.set_rotation(28)
            tick.set_ha("right")


# ---------------- 各 kind 渲染 ----------------

def _render_bar(data: Dict, options: Dict):
    categories = _label_list(data.get("categories"), "data.categories", limits.MAX_CATEGORIES)
    values = _number_list(data.get("values"), "data.values", limits.MAX_CATEGORIES)
    if len(values) != len(categories):
        _fail("data.values 长度必须与 data.categories 一致")
    fig = _figure(3.9)
    ax = _new_axes(fig)
    xs = range(len(categories))
    ax.bar(xs, values, width=0.62, color=NAVY)
    for x, v in zip(xs, values):
        _bar_label(ax, x, v, _fmt(v), above=v >= 0)
    _category_ticks(ax, xs, categories)
    return fig


def _render_horizontal_bar(data: Dict, options: Dict):
    categories = _label_list(data.get("categories"), "data.categories", limits.MAX_CATEGORIES)
    values = _number_list(data.get("values"), "data.values", limits.MAX_CATEGORIES)
    if len(values) != len(categories):
        _fail("data.values 长度必须与 data.categories 一致")
    fig = _figure(max(2.6, 0.42 * len(categories) + 1.4))
    ax = _new_axes(fig, y_grid=False, rect=(0.22, 0.08, 0.72, 0.78))
    ys = range(len(categories))
    ax.barh(ys, values, height=0.6, color=NAVY)
    ax.invert_yaxis()  # 第一条在最上
    ax.set_yticks(list(ys))
    ax.set_yticklabels(categories, fontsize=8.5, color=TEXT, **_LABEL_KW)
    ax.xaxis.grid(True, color=GRID, linewidth=0.7)
    ax.set_axisbelow(True)
    span = max((abs(v) for v in values), default=1) or 1
    for y, v in zip(ys, values):
        ax.annotate(
            _fmt(v), (v, y), textcoords="offset points",
            xytext=(4 if v >= 0 else -4, 0),
            ha="left" if v >= 0 else "right", va="center",
            fontsize=7.5, color=TEXT, **_LABEL_KW,
        )
    ax.set_xlim(min(0, min(values) * 1.12), max(0, max(values) + span * 0.12))
    return fig


def _render_grouped_bar(data: Dict, options: Dict):
    categories = _label_list(data.get("categories"), "data.categories", limits.MAX_CATEGORIES)
    series = _series_list(data, value_len=len(categories))
    fig = _figure(3.9)
    ax = _new_axes(fig)
    n = len(series)
    group_width = 0.72
    bar_width = group_width / n
    for si, item in enumerate(series):
        xs = [i - group_width / 2 + bar_width * (si + 0.5) for i in range(len(categories))]
        ax.bar(xs, item["values"], width=bar_width * 0.92, color=series_color(si), label=item["name"])
        if n * len(categories) <= 24:  # 标签太密不放
            for x, v in zip(xs, item["values"]):
                _bar_label(ax, x, v, _fmt(v), above=v >= 0)
    _category_ticks(ax, range(len(categories)), categories)
    _legend(ax)
    return fig


def _render_stacked_bar(data: Dict, options: Dict):
    categories = _label_list(data.get("categories"), "data.categories", limits.MAX_CATEGORIES)
    series = _series_list(data, value_len=len(categories))
    for item in series:
        if any(v < 0 for v in item["values"]):
            _fail("stacked_bar 不支持负值；含增减对比请改用 waterfall 或 grouped_bar")
    fig = _figure(3.9)
    ax = _new_axes(fig)
    xs = list(range(len(categories)))
    bottoms = [0.0] * len(categories)
    for si, item in enumerate(series):
        ax.bar(xs, item["values"], width=0.62, bottom=bottoms, color=series_color(si), label=item["name"])
        for x, v, b in zip(xs, item["values"], bottoms):
            if v > 0 and len(series) * len(categories) <= 30:
                ax.annotate(
                    _fmt(v), (x, b + v / 2), ha="center", va="center",
                    fontsize=7, color="#FFFFFF", **_LABEL_KW,
                )
        bottoms = [b + v for b, v in zip(bottoms, item["values"])]
    for x, total in zip(xs, bottoms):
        _bar_label(ax, x, total, _fmt(total))
    _category_ticks(ax, xs, categories)
    _legend(ax)
    return fig


def _render_line(data: Dict, options: Dict):
    x_labels = _label_list(data.get("x"), "data.x", limits.MAX_POINTS_PER_SERIES)
    series = _series_list(data, value_len=len(x_labels))
    forecast_from = options.get("forecast_from")
    forecast_idx = None
    if forecast_from is not None:
        if forecast_from not in x_labels:
            _fail(f"options.forecast_from（{forecast_from}）必须是 data.x 中的一个值")
        forecast_idx = x_labels.index(forecast_from)

    fig = _figure(3.9)
    ax = _new_axes(fig)
    xs = list(range(len(x_labels)))
    for si, item in enumerate(series):
        color = series_color(si)
        dashed = item.get("style") == "dashed"
        if forecast_idx is not None and not dashed:
            solid_end = forecast_idx + 1
            ax.plot(xs[:solid_end], item["values"][:solid_end], color=color,
                    linewidth=2, marker="o", markersize=3.5, label=item["name"])
            ax.plot(xs[forecast_idx:], item["values"][forecast_idx:], color=color,
                    linewidth=2, linestyle="--", marker="o", markersize=3.5,
                    markerfacecolor="#FFFFFF")
        else:
            ax.plot(xs, item["values"], color=color, linewidth=2,
                    linestyle="--" if dashed else "-",
                    marker="o", markersize=3.5, label=item["name"])
        if len(series) * len(x_labels) <= 40:
            for x, v in zip(xs, item["values"]):
                _bar_label(ax, x, v, _fmt(v))
    if forecast_idx is not None:
        ax.axvline(forecast_idx, color=GRID, linewidth=1, linestyle=":")
        ax.annotate(
            "预测", (forecast_idx, ax.get_ylim()[1]), ha="left", va="top",
            textcoords="offset points", xytext=(4, -2), fontsize=7.5, color=MUTED, **_LABEL_KW,
        )
    _category_ticks(ax, xs, x_labels)
    if len(series) > 1:
        _legend(ax)
    return fig


def _render_pie(data: Dict, options: Dict, *, donut: bool = False):
    labels = _label_list(data.get("labels"), "data.labels", 12)
    values = _number_list(data.get("values"), "data.values", 12)
    if len(values) != len(labels):
        _fail("data.values 长度必须与 data.labels 一致")
    if any(v < 0 for v in values):
        _fail("饼图不支持负值")
    if sum(values) <= 0:
        _fail("饼图数值合计必须大于 0")
    fig = _figure(3.9)
    ax = fig.add_axes((0.06, 0.05, 0.88, 0.8))
    ax.set_aspect("equal")
    total = sum(values)
    colors = [series_color(i) for i in range(len(values))]
    wedges, texts, autotexts = ax.pie(
        values, colors=colors, startangle=90, counterclock=False,
        autopct=lambda pct: f"{pct:.1f}%" if pct >= 3 else "",
        pctdistance=0.75 if donut else 0.6,
        wedgeprops={"width": 0.42, "edgecolor": "#FFFFFF"} if donut else {"edgecolor": "#FFFFFF"},
        textprops={"fontsize": 8, "fontfamily": FONT_FAMILY},
    )
    for t in autotexts:
        t.set_color("#FFFFFF")
        t.set_fontsize(7.5)
    legend_labels = [f"{l}（{_fmt(v)}，{v / total * 100:.1f}%）" for l, v in zip(labels, values)]
    ax.legend(
        wedges, legend_labels, loc="center left", bbox_to_anchor=(1.0, 0.5),
        frameon=False, fontsize=8, prop={"family": FONT_FAMILY, "size": 8},
    )
    ax.set_position((0.02, 0.05, 0.55, 0.8))
    return fig


def _render_waterfall(data: Dict, options: Dict):
    raw_steps = data.get("steps")
    if not isinstance(raw_steps, (list, tuple)) or not raw_steps:
        _fail("data.steps 必须是非空数组")
    if len(raw_steps) > limits.MAX_CATEGORIES:
        _fail(f"steps 过多（{len(raw_steps)} 个，上限 {limits.MAX_CATEGORIES}）")
    steps = []
    for i, step in enumerate(raw_steps):
        step = _require_dict(step, f"steps[{i}]")
        steps.append({
            "label": _clean_text(step.get("label"), f"steps[{i}].label", limits.MAX_LABEL_CHARS),
            "delta": _number(step.get("delta"), f"steps[{i}].delta"),
        })
    total_label = data.get("total_label")
    total_label = (
        _clean_text(total_label, "data.total_label", limits.MAX_LABEL_CHARS)
        if total_label is not None else "合计"
    )

    fig = _figure(3.9)
    ax = _new_axes(fig)
    labels = [s["label"] for s in steps] + [total_label]
    cumulative = 0.0
    for i, step in enumerate(steps):
        delta = step["delta"]
        color = NAVY if i == 0 else (POSITIVE if delta >= 0 else NEGATIVE)
        bottom = 0 if i == 0 else (cumulative if delta >= 0 else cumulative + delta)
        height = abs(delta) if i > 0 else delta
        ax.bar(i, height, width=0.58, bottom=bottom, color=color)
        prev = cumulative
        cumulative += delta if i > 0 else (delta - cumulative)
        if i == 0:
            cumulative = delta
        top = max(prev, cumulative) if i > 0 else delta
        _bar_label(ax, i, top, (f"+{_fmt(delta)}" if delta > 0 and i > 0 else _fmt(delta)))
        # 连接线到下一根
        ax.plot([i + 0.29, i + 1 - 0.29], [cumulative, cumulative],
                color=MUTED, linewidth=0.8, linestyle=":")
    ax.bar(len(steps), cumulative, width=0.58, color=NAVY)
    _bar_label(ax, len(steps), cumulative, _fmt(cumulative))
    _category_ticks(ax, range(len(labels)), labels)
    return fig


def _render_funnel(data: Dict, options: Dict):
    raw = data.get("stages")
    if not isinstance(raw, (list, tuple)) or not raw:
        _fail("data.stages 必须是非空数组")
    if len(raw) > 10:
        _fail(f"漏斗层级过多（{len(raw)} 层，上限 10）")
    stages = []
    for i, item in enumerate(raw):
        item = _require_dict(item, f"stages[{i}]")
        stages.append({
            "label": _clean_text(item.get("label"), f"stages[{i}].label", limits.MAX_LABEL_CHARS),
            "value": _number(item.get("value"), f"stages[{i}].value"),
        })
    if any(s["value"] < 0 for s in stages):
        _fail("漏斗数值不能为负")
    top = max(s["value"] for s in stages) or 1

    fig = _figure(max(2.8, 0.52 * len(stages) + 1.5))
    ax = fig.add_axes((0.05, 0.06, 0.9, 0.78))
    ax.set_axis_off()
    for i, stage in enumerate(stages):
        width = max(stage["value"] / top, 0.04)
        y = len(stages) - 1 - i
        ax.barh(y, width, height=0.72, left=(1 - width) / 2, color=series_color(i))
        ax.annotate(f"{stage['label']}  {_fmt(stage['value'])}",
                    (0.5, y), ha="center", va="center", fontsize=8.5,
                    color="#FFFFFF", fontweight="bold", **_LABEL_KW)
        if i > 0:
            prev_v = stages[i - 1]["value"]
            rate = stage["value"] / prev_v * 100 if prev_v else 0
            ax.annotate(f"{rate:.0f}%", (1.0, y + 0.5), ha="right", va="center",
                        fontsize=7.5, color=MUTED, **_LABEL_KW)
    ax.set_xlim(0, 1)
    ax.set_ylim(-0.6, len(stages) - 0.3)
    return fig


def _scatter_points(data: Dict, *, need_size: bool) -> List[Dict]:
    raw = data.get("points")
    if not isinstance(raw, (list, tuple)) or not raw:
        _fail("data.points 必须是非空数组")
    if len(raw) > limits.MAX_SCATTER_POINTS:
        _fail(f"数据点过多（{len(raw)} 个，上限 {limits.MAX_SCATTER_POINTS}）")
    points = []
    for i, item in enumerate(raw):
        item = _require_dict(item, f"points[{i}]")
        point = {
            "x": _number(item.get("x"), f"points[{i}].x"),
            "y": _number(item.get("y"), f"points[{i}].y"),
            "label": None,
        }
        if item.get("label") is not None:
            point["label"] = _clean_text(item["label"], f"points[{i}].label", limits.MAX_LABEL_CHARS)
        if need_size:
            size = _number(item.get("size"), f"points[{i}].size")
            if size <= 0:
                _fail(f"points[{i}].size 必须大于 0")
            point["size"] = size
        points.append(point)
    return points


def _axis_labels(ax, options: Dict) -> None:
    if options.get("x_label"):
        ax.set_xlabel(str(options["x_label"])[:limits.MAX_LABEL_CHARS],
                      fontsize=8.5, color=MUTED, **_LABEL_KW)
    if options.get("y_label"):
        ax.set_ylabel(str(options["y_label"])[:limits.MAX_LABEL_CHARS],
                      fontsize=8.5, color=MUTED, **_LABEL_KW)


def _render_scatter(data: Dict, options: Dict, *, bubble: bool = False):
    points = _scatter_points(data, need_size=bubble)
    fig = _figure(4.4)
    ax = _new_axes(fig, rect=(0.1, 0.12, 0.86, 0.74))
    ax.xaxis.grid(True, color=GRID, linewidth=0.7)
    if bubble:
        max_size = max(p["size"] for p in points)
        sizes = [60 + 1400 * p["size"] / max_size for p in points]
        ax.scatter([p["x"] for p in points], [p["y"] for p in points],
                   s=sizes, color=NAVY, alpha=0.55, edgecolors=NAVY, linewidths=0.8)
    else:
        ax.scatter([p["x"] for p in points], [p["y"] for p in points],
                   s=34, color=NAVY, alpha=0.85)
    for p in points:
        if p["label"]:
            ax.annotate(p["label"], (p["x"], p["y"]), textcoords="offset points",
                        xytext=(6, 5), fontsize=7.5, color=TEXT, **_LABEL_KW)
    _axis_labels(ax, options)
    return fig


def _render_heatmap(data: Dict, options: Dict):
    rows = _label_list(data.get("rows"), "data.rows", limits.MAX_CATEGORIES)
    cols = _label_list(data.get("cols"), "data.cols", limits.MAX_CATEGORIES)
    if len(rows) * len(cols) > limits.MAX_HEATMAP_CELLS:
        _fail(f"热力图单元格过多（{len(rows)}×{len(cols)}，上限 {limits.MAX_HEATMAP_CELLS}）")
    raw_values = data.get("values")
    if not isinstance(raw_values, (list, tuple)) or len(raw_values) != len(rows):
        _fail("data.values 必须是与 rows 等长的二维数组")
    matrix = []
    for ri, row in enumerate(raw_values):
        matrix.append(_number_list(row, f"values[{ri}]", len(cols)))
        if len(matrix[-1]) != len(cols):
            _fail(f"values[{ri}] 长度必须与 cols 一致")

    fig = _figure(max(2.8, 0.4 * len(rows) + 1.7))
    ax = fig.add_axes((0.16, 0.06, 0.8, 0.76))
    from matplotlib.colors import LinearSegmentedColormap
    cmap = LinearSegmentedColormap.from_list("navy", ["#F2F5F9", NAVY])
    ax.imshow(matrix, cmap=cmap, aspect="auto")
    ax.set_xticks(range(len(cols)))
    ax.set_xticklabels(cols, fontsize=8, color=TEXT, **_LABEL_KW)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels(rows, fontsize=8, color=TEXT, **_LABEL_KW)
    ax.tick_params(length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    flat = [v for row in matrix for v in row]
    lo, hi = min(flat), max(flat)
    threshold = lo + (hi - lo) * 0.55
    for ri in range(len(rows)):
        for ci in range(len(cols)):
            v = matrix[ri][ci]
            ax.annotate(_fmt(v), (ci, ri), ha="center", va="center", fontsize=7.5,
                        color="#FFFFFF" if v >= threshold else TEXT, **_LABEL_KW)
    if len(cols) > 6 or max(len(c) for c in cols) > 6:
        for tick in ax.get_xticklabels():
            tick.set_rotation(28)
            tick.set_ha("right")
    return fig


_RENDERERS = {
    "bar": _render_bar,
    "grouped_bar": _render_grouped_bar,
    "stacked_bar": _render_stacked_bar,
    "horizontal_bar": _render_horizontal_bar,
    "line": _render_line,
    "pie": lambda d, o: _render_pie(d, o, donut=False),
    "donut": lambda d, o: _render_pie(d, o, donut=True),
    "waterfall": _render_waterfall,
    "funnel": _render_funnel,
    "scatter": lambda d, o: _render_scatter(d, o, bubble=False),
    "bubble": lambda d, o: _render_scatter(d, o, bubble=True),
    "heatmap": _render_heatmap,
}


def render_chart(kind: str, data: Dict, title: str, source: str, options: Dict | None = None) -> bytes:
    """渲染数据图。返回 PNG bytes；任何失败抛 ChartRenderError。"""
    if kind not in _RENDERERS:
        _fail(f"不支持的图类型: {kind}（可用：{', '.join(CHART_KINDS)}）")
    title = _clean_text(title, "title", limits.MAX_TITLE_CHARS)
    source = _clean_text(source, "source", limits.MAX_SOURCE_CHARS)
    data = _require_dict(data, "data")
    options = _require_dict(options, "options") if options is not None else {}
    unit = None
    if options.get("unit") is not None:
        unit = _clean_text(options["unit"], "options.unit", 20)

    try:
        fig = _RENDERERS[kind](data, options)
        return finalize_png(fig, title=title, source=source, unit=unit)
    except ChartRenderError:
        raise
    except Exception as exc:  # matplotlib 内部错误也归一为人话，不泄栈给模型
        raise ChartRenderError(f"图表渲染失败：{exc}") from exc
