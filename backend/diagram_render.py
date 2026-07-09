"""结构图渲染器（叶子模块）：版式化 spec / 拓扑 → 咨询级 PNG。

依赖 chart_style / chart_limits + matplotlib + stdlib，绝不依赖 chat/skill/main。
入口 `render_diagram(kind, spec, title, source, options) -> bytes(PNG)`。

覆盖两类：
- 签名版式（版式固定，matplotlib 图元手绘）：matrix_2x2 / value_chain / process /
  roadmap / pyramid——咨询签名图，house style 完全可控。
- 任意拓扑（纯 Python 布局，替代 graphviz——设计 spec §4.2 的 v2.1 二进制依赖被此
  消掉）：org_chart / tree 用递归树布局（叶子均分、父居中）；flowchart 用分层 DAG
  布局（最长路径分层 + 重心排序）。咨询报告里这三类图拓扑都浅（层深/节点数有
  chart_limits 硬上限），无需通用图布局引擎。
"""

from __future__ import annotations

import textwrap
from typing import Dict, List

from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Polygon

from . import chart_limits as limits
from .chart_style import (
    GRID, MUTED, NAVY, TEXT, FONT_FAMILY,
    ChartRenderError, finalize_png, new_figure, series_color,
)

DIAGRAM_KINDS = (
    "matrix_2x2", "value_chain", "process", "roadmap", "pyramid",
    "flowchart", "org_chart", "tree",
)

_TXT = {"fontfamily": FONT_FAMILY}
_LIGHT_FILL = "#EDF1F6"
_MID_FILL = "#C9D4E2"


def _fail(msg: str) -> None:
    raise ChartRenderError(msg)


def _require_dict(value, name: str) -> Dict:
    if not isinstance(value, dict):
        _fail(f"{name} 必须是对象")
    return value


def _clean_text(value, name: str, max_chars: int = limits.MAX_LABEL_CHARS) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(f"{name} 必须是非空字符串")
    text = " ".join(value.split())
    if len(text) > max_chars:
        _fail(f"{name} 过长（{len(text)} 字符，上限 {max_chars}）")
    return text


def _wrap(text: str, width: int) -> str:
    return "\n".join(textwrap.wrap(text, width=width) or [text])


def _canvas(height: float):
    """无坐标轴画布，坐标系 0..1 × 0..1。"""
    fig = new_figure(limits.MAX_FIGURE_WIDTH_IN, min(height, limits.MAX_FIGURE_HEIGHT_IN))
    ax = fig.add_axes((0.02, 0.05, 0.96, 0.8))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_axis_off()
    return fig, ax


def _box(ax, x, y, w, h, label, *, fill=NAVY, text_color="#FFFFFF", fontsize=8.5,
         wrap=12, edge=None, bold=False):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h,
        boxstyle="round,pad=0,rounding_size=0.008",
        facecolor=fill, edgecolor=edge or fill, linewidth=1, mutation_aspect=1,
    ))
    ax.annotate(
        _wrap(label, wrap), (x + w / 2, y + h / 2), ha="center", va="center",
        fontsize=fontsize, color=text_color,
        fontweight="bold" if bold else "normal", **_TXT,
    )


def _arrow(ax, start, end, *, label=None):
    ax.add_patch(FancyArrowPatch(
        start, end, arrowstyle="-|>", mutation_scale=11,
        color=MUTED, linewidth=1.1, shrinkA=2, shrinkB=2,
    ))
    if label:
        mx, my = (start[0] + end[0]) / 2, (start[1] + end[1]) / 2
        ax.annotate(label, (mx, my), ha="center", va="bottom",
                    textcoords="offset points", xytext=(0, 3),
                    fontsize=7, color=MUTED, **_TXT)


# ---------------- 签名版式 ----------------

def _render_matrix_2x2(spec: Dict, options: Dict):
    x_axis = _require_dict(spec.get("x_axis"), "spec.x_axis")
    y_axis = _require_dict(spec.get("y_axis"), "spec.y_axis")
    quadrants = spec.get("quadrant_labels")
    if not isinstance(quadrants, (list, tuple)) or len(quadrants) != 4:
        _fail("spec.quadrant_labels 必须是 4 个字符串（左上、右上、左下、右下）")
    quadrants = [_clean_text(q, f"quadrant_labels[{i}]") for i, q in enumerate(quadrants)]
    raw_items = spec.get("items") or []
    if not isinstance(raw_items, (list, tuple)):
        _fail("spec.items 必须是数组")
    if len(raw_items) > limits.MAX_DIAGRAM_ITEMS:
        _fail(f"items 过多（{len(raw_items)} 个，上限 {limits.MAX_DIAGRAM_ITEMS}）")
    items = []
    for i, item in enumerate(raw_items):
        item = _require_dict(item, f"items[{i}]")
        x = item.get("x")
        y = item.get("y")
        for v, axis in ((x, "x"), (y, "y")):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 1:
                _fail(f"items[{i}].{axis} 必须是 0~1 之间的数字（象限内相对位置）")
        items.append({
            "label": _clean_text(item.get("label"), f"items[{i}].label"),
            "x": float(x), "y": float(y),
        })

    fig = new_figure(limits.MAX_FIGURE_WIDTH_IN, 5.4)
    ax = fig.add_axes((0.1, 0.1, 0.82, 0.72))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_xticks([])
    ax.set_yticks([])
    # 象限底色（左上、右上、左下、右下）
    fills = ["#F4F6FA", "#E8EEF6", "#FAFBFC", "#F4F6FA"]
    coords = [(0, 0.5), (0.5, 0.5), (0, 0), (0.5, 0)]
    for (qx, qy), fill, label in zip(coords, fills, quadrants):
        ax.add_patch(Polygon([(qx, qy), (qx + 0.5, qy), (qx + 0.5, qy + 0.5), (qx, qy + 0.5)],
                             facecolor=fill, edgecolor="none"))
        ax.annotate(label, (qx + 0.02, qy + 0.475), ha="left", va="top",
                    fontsize=9, color=NAVY, fontweight="bold", **_TXT)
    ax.axhline(0.5, color=GRID, linewidth=1.2)
    ax.axvline(0.5, color=GRID, linewidth=1.2)
    for spine in ax.spines.values():
        spine.set_color(GRID)
    for item in items:
        ax.scatter([item["x"]], [item["y"]], s=52, color=NAVY, zorder=3)
        ax.annotate(item["label"], (item["x"], item["y"]),
                    textcoords="offset points", xytext=(6, 5),
                    fontsize=8, color=TEXT, **_TXT)
    # 轴标签与两端注记
    x_label = _clean_text(x_axis.get("label"), "x_axis.label")
    y_label = _clean_text(y_axis.get("label"), "y_axis.label")
    ax.set_xlabel(x_label, fontsize=9, color=TEXT, **_TXT)
    ax.set_ylabel(y_label, fontsize=9, color=TEXT, **_TXT)
    for axis, obj in (("x_axis", x_axis), ("y_axis", y_axis)):
        for key in ("low", "high"):
            if obj.get(key) is not None:
                _clean_text(obj.get(key), f"{axis}.{key}")
    if x_axis.get("low"):
        ax.annotate(str(x_axis["low"]), (0, -0.03), xycoords="axes fraction",
                    ha="left", va="top", fontsize=7.5, color=MUTED, **_TXT)
    if x_axis.get("high"):
        ax.annotate(str(x_axis["high"]), (1, -0.03), xycoords="axes fraction",
                    ha="right", va="top", fontsize=7.5, color=MUTED, **_TXT)
    if y_axis.get("low"):
        ax.annotate(str(y_axis["low"]), (-0.03, 0), xycoords="axes fraction",
                    ha="right", va="bottom", fontsize=7.5, color=MUTED, rotation=90, **_TXT)
    if y_axis.get("high"):
        ax.annotate(str(y_axis["high"]), (-0.03, 1), xycoords="axes fraction",
                    ha="right", va="top", fontsize=7.5, color=MUTED, rotation=90, **_TXT)
    return fig


def _chevron(ax, x, y, w, h, label, *, fill, tip=0.028, first=False, fontsize=8.5, wrap=10):
    """横向 chevron 箭头块。"""
    left_notch = 0 if first else tip
    pts = [
        (x, y), (x + w - tip, y), (x + w, y + h / 2), (x + w - tip, y + h),
        (x, y + h), (x + left_notch, y + h / 2),
    ]
    ax.add_patch(Polygon(pts, facecolor=fill, edgecolor="#FFFFFF", linewidth=1))
    ax.annotate(_wrap(label, wrap), (x + (w + left_notch - tip) / 2, y + h / 2),
                ha="center", va="center", fontsize=fontsize, color="#FFFFFF",
                fontweight="bold", **_TXT)


def _steps_list(spec: Dict, key: str, max_items: int) -> List[Dict]:
    raw = spec.get(key)
    if not isinstance(raw, (list, tuple)) or not raw:
        _fail(f"spec.{key} 必须是非空数组")
    if len(raw) > max_items:
        _fail(f"{key} 过多（{len(raw)} 个，上限 {max_items}）")
    result = []
    for i, item in enumerate(raw):
        item = _require_dict(item, f"{key}[{i}]")
        entry = {"label": _clean_text(item.get("label"), f"{key}[{i}].label")}
        if item.get("note") is not None:
            entry["note"] = _clean_text(item["note"], f"{key}[{i}].note", 120)
        result.append(entry)
    return result


def _render_process(spec: Dict, options: Dict):
    steps = _steps_list(spec, "steps", 8)
    has_notes = any("note" in s for s in steps)
    fig, ax = _canvas(2.9 if has_notes else 2.2)
    n = len(steps)
    gap = 0.012
    w = (1 - gap * (n - 1)) / n
    y, h = (0.5, 0.34) if has_notes else (0.33, 0.4)
    for i, step in enumerate(steps):
        x = i * (w + gap)
        _chevron(ax, x, y, w, h, step["label"], fill=series_color(i % 2), first=(i == 0),
                 fontsize=8.5 if n <= 6 else 7.5, wrap=max(4, int(16 / n * 4)))
        if step.get("note"):
            ax.annotate(_wrap(step["note"], 11), (x + w / 2, y - 0.06), ha="center", va="top",
                        fontsize=7.2, color=MUTED, **_TXT)
    return fig


def _render_value_chain(spec: Dict, options: Dict):
    primary = _steps_list(spec, "primary", 8)
    support_raw = spec.get("support") or []
    if not isinstance(support_raw, (list, tuple)):
        _fail("spec.support 必须是数组")
    if len(support_raw) > 6:
        _fail(f"support 过多（{len(support_raw)} 个，上限 6）")
    support = [_clean_text(s, f"support[{i}]") for i, s in enumerate(support_raw)]

    rows = len(support)
    fig, ax = _canvas(1.9 + 0.42 * rows + 0.9)
    total_h = 0.86
    row_h = (total_h * 0.55) / rows if rows else 0
    chain_h = total_h * (0.4 if rows else 0.62)
    chain_y = 0.06
    # 支持活动：横条自上而下
    for i, label in enumerate(support):
        y = chain_y + chain_h + 0.05 + (rows - 1 - i) * (row_h + 0.012)
        _box(ax, 0, y, 1, row_h, label, fill=_LIGHT_FILL, text_color=TEXT,
             fontsize=8, wrap=40, edge=_MID_FILL)
    # 主链 chevron
    n = len(primary)
    gap = 0.012
    w = (1 - gap * (n - 1)) / n
    for i, step in enumerate(primary):
        _chevron(ax, i * (w + gap), chain_y, w, chain_h, step["label"],
                 fill=NAVY, first=(i == 0), fontsize=8.5 if n <= 6 else 7.5,
                 wrap=max(4, int(16 / n * 4)))
    return fig


def _render_roadmap(spec: Dict, options: Dict):
    raw = spec.get("phases")
    if not isinstance(raw, (list, tuple)) or not raw:
        _fail("spec.phases 必须是非空数组")
    if len(raw) > 5:
        _fail(f"phases 过多（{len(raw)} 个，上限 5）")
    phases = []
    max_items = 0
    for i, item in enumerate(raw):
        item = _require_dict(item, f"phases[{i}]")
        entries = item.get("items") or []
        if not isinstance(entries, (list, tuple)):
            _fail(f"phases[{i}].items 必须是数组")
        if len(entries) > 8:
            _fail(f"phases[{i}].items 过多（{len(entries)} 个，上限 8）")
        entries = [_clean_text(e, f"phases[{i}].items[{j}]", 80) for j, e in enumerate(entries)]
        phase = {
            "label": _clean_text(item.get("label"), f"phases[{i}].label"),
            "period": (_clean_text(item["period"], f"phases[{i}].period", 30)
                       if item.get("period") is not None else None),
            "items": entries,
        }
        max_items = max(max_items, len(entries))
        phases.append(phase)

    fig, ax = _canvas(2.1 + 0.34 * max_items)
    n = len(phases)
    gap = 0.018
    w = (1 - gap * (n - 1)) / n
    header_h = 0.16 if max_items else 0.3
    header_y = 0.86 - header_h
    for i, phase in enumerate(phases):
        x = i * (w + gap)
        head = phase["label"] + (f"\n{phase['period']}" if phase["period"] else "")
        _chevron(ax, x, header_y, w, header_h, head, fill=series_color(i % len(phases)),
                 first=(i == 0), fontsize=8.5, wrap=14)
        body_top = header_y - 0.04
        for j, entry in enumerate(phase["items"]):
            wrapped = _wrap(entry, max(6, int(w * 30)))
            lines = wrapped.count("\n") + 1
            ax.annotate("· " + wrapped, (x + 0.012, body_top), ha="left", va="top",
                        fontsize=7.6, color=TEXT, **_TXT)
            body_top -= 0.075 * lines + 0.015
    return fig


def _render_pyramid(spec: Dict, options: Dict):
    layers = _steps_list(spec, "layers", 6)
    n = len(layers)
    fig, ax = _canvas(1.6 + 0.5 * n)
    top_w, bottom_w = 0.24, 0.92
    layer_h = 0.8 / n
    has_notes = any("note" in l for l in layers)
    cx = 0.5 if not has_notes else 0.36
    for i, layer in enumerate(layers):
        w_top = top_w + (bottom_w - top_w) * (i / n)
        w_bot = top_w + (bottom_w - top_w) * ((i + 1) / n)
        if has_notes:
            w_top *= 0.72
            w_bot *= 0.72
        y_top = 0.86 - i * layer_h
        y_bot = y_top - layer_h + 0.008
        pts = [(cx - w_bot / 2, y_bot), (cx + w_bot / 2, y_bot),
               (cx + w_top / 2, y_top), (cx - w_top / 2, y_top)]
        ax.add_patch(Polygon(pts, facecolor=series_color(i), edgecolor="#FFFFFF", linewidth=1))
        ax.annotate(_wrap(layer["label"], 14), (cx, (y_top + y_bot) / 2),
                    ha="center", va="center", fontsize=8.5, color="#FFFFFF",
                    fontweight="bold", **_TXT)
        if layer.get("note"):
            ax.annotate(_wrap(layer["note"], 22), (cx + w_bot / 2 + 0.04, (y_top + y_bot) / 2),
                        ha="left", va="center", fontsize=7.5, color=MUTED, **_TXT)
    return fig


# ---------------- 任意拓扑（纯 Python 布局） ----------------

def _parse_tree(spec: Dict) -> Dict:
    root = _require_dict(spec.get("root"), "spec.root")

    count = 0

    def walk(node: Dict, depth: int, path: str) -> Dict:
        nonlocal count
        count += 1
        if count > limits.MAX_TREE_NODES:
            _fail(f"节点过多（上限 {limits.MAX_TREE_NODES}）")
        if depth > limits.MAX_TREE_DEPTH:
            _fail(f"层级过深（上限 {limits.MAX_TREE_DEPTH} 层）")
        label = _clean_text(node.get("label"), f"{path}.label")
        raw_children = node.get("children") or []
        if not isinstance(raw_children, (list, tuple)):
            _fail(f"{path}.children 必须是数组")
        children = [
            walk(_require_dict(c, f"{path}.children[{i}]"), depth + 1, f"{path}.children[{i}]")
            for i, c in enumerate(raw_children)
        ]
        return {"label": label, "children": children, "depth": depth}

    return walk(root, 0, "root")


def _tree_layout(root: Dict) -> tuple[List[Dict], int]:
    """经典树布局：叶子从左到右均分，父节点居中于子节点上方。返回 (节点列表, 最大深度)。"""
    nodes: List[Dict] = []
    next_leaf_x = [0.0]
    max_depth = [0]

    def place(node: Dict) -> float:
        max_depth[0] = max(max_depth[0], node["depth"])
        if not node["children"]:
            x = next_leaf_x[0]
            next_leaf_x[0] += 1.0
        else:
            child_xs = [place(c) for c in node["children"]]
            x = sum(child_xs) / len(child_xs)
        node["x"] = x
        nodes.append(node)
        return x

    place(root)
    total = next_leaf_x[0] - 1.0
    for node in nodes:
        node["x"] = (node["x"] / total) if total > 0 else 0.5  # 归一化 0..1，边距由渲染层按盒宽定
    return nodes, max_depth[0]


def _render_tree_like(spec: Dict, options: Dict, *, org: bool):
    root = _parse_tree(spec)
    nodes, max_depth = _tree_layout(root)
    n_leaves = sum(1 for n in nodes if not n["children"])
    fig, ax = _canvas(1.7 + 0.75 * (max_depth + 1))
    level_h = 0.86 / (max_depth + 1)
    box_h = min(0.14, level_h * 0.52)
    box_w = min(0.16, 0.9 / max(n_leaves, 1) * 0.92)
    margin = box_w / 2 + 0.015  # 盒宽入边距，保证最左/最右节点不被裁
    for node in nodes:
        node["x"] = margin + (1 - 2 * margin) * node["x"]
        node["y"] = 0.88 - node["depth"] * level_h - box_h / 2

    # 连接线先画（在盒子下层）：父底部 → 肘形 → 子顶部
    for node in nodes:
        for child in node["children"]:
            px, py = node["x"], node["y"] - box_h / 2
            cx_, cy = child["x"], child["y"] + box_h / 2
            mid_y = (py + cy) / 2
            ax.plot([px, px, cx_, cx_], [py, mid_y, mid_y, cy],
                    color=GRID if not org else MUTED, linewidth=1.1, zorder=1)

    for node in nodes:
        depth = node["depth"]
        if org:
            fill = NAVY if depth == 0 else (_MID_FILL if depth == 1 else _LIGHT_FILL)
            text_color = "#FFFFFF" if depth == 0 else TEXT
        else:
            fill = _LIGHT_FILL if depth else NAVY
            text_color = TEXT if depth else "#FFFFFF"
        _box(ax, node["x"] - box_w / 2, node["y"] - box_h / 2, box_w, box_h,
             node["label"], fill=fill, text_color=text_color,
             fontsize=8 if len(nodes) <= 16 else 7,
             wrap=max(4, int(box_w * 62)), edge=_MID_FILL, bold=(depth == 0))
    return fig


def _parse_flow(spec: Dict) -> tuple[Dict[str, Dict], List[Dict]]:
    raw_nodes = spec.get("nodes")
    if not isinstance(raw_nodes, (list, tuple)) or not raw_nodes:
        _fail("spec.nodes 必须是非空数组")
    if len(raw_nodes) > limits.MAX_FLOW_NODES:
        _fail(f"节点过多（{len(raw_nodes)} 个，上限 {limits.MAX_FLOW_NODES}）")
    nodes: Dict[str, Dict] = {}
    for i, item in enumerate(raw_nodes):
        item = _require_dict(item, f"nodes[{i}]")
        node_id = item.get("id")
        if not isinstance(node_id, str) or not node_id.strip():
            _fail(f"nodes[{i}].id 必须是非空字符串")
        node_id = node_id.strip()
        if node_id in nodes:
            _fail(f"节点 id 重复: {node_id}")
        shape = item.get("shape") or "box"
        if shape not in ("box", "rounded", "diamond"):
            _fail(f"nodes[{i}].shape 只支持 box / rounded / diamond")
        nodes[node_id] = {
            "id": node_id,
            "label": _clean_text(item.get("label"), f"nodes[{i}].label"),
            "shape": shape,
        }
    raw_edges = spec.get("edges") or []
    if not isinstance(raw_edges, (list, tuple)):
        _fail("spec.edges 必须是数组")
    if len(raw_edges) > limits.MAX_FLOW_EDGES:
        _fail(f"边过多（{len(raw_edges)} 条，上限 {limits.MAX_FLOW_EDGES}）")
    edges = []
    for i, item in enumerate(raw_edges):
        item = _require_dict(item, f"edges[{i}]")
        src, dst = item.get("from"), item.get("to")
        if src not in nodes or dst not in nodes:
            _fail(f"edges[{i}] 的 from/to 必须是已定义的节点 id")
        label = None
        if item.get("label") is not None:
            label = _clean_text(item["label"], f"edges[{i}].label", 30)
        edges.append({"from": src, "to": dst, "label": label})
    return nodes, edges


def _flow_layers(nodes: Dict[str, Dict], edges: List[Dict]) -> List[List[str]]:
    """Kahn 拓扑 + 最长路径分层；有环给人话错误（流程图应是有向无环）。"""
    indegree = {nid: 0 for nid in nodes}
    adjacency: Dict[str, List[str]] = {nid: [] for nid in nodes}
    for edge in edges:
        adjacency[edge["from"]].append(edge["to"])
        indegree[edge["to"]] += 1
    queue = [nid for nid in nodes if indegree[nid] == 0]
    layer_of = {nid: 0 for nid in queue}
    seen = 0
    order = list(queue)
    while queue:
        nid = queue.pop(0)
        seen += 1
        for nxt in adjacency[nid]:
            layer_of[nxt] = max(layer_of.get(nxt, 0), layer_of[nid] + 1)
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
                order.append(nxt)
    if seen != len(nodes):
        _fail("流程图存在环路（A→B→…→A）；请拆开循环，用文字注明回路，或改用 process 模板")
    layers: List[List[str]] = [[] for _ in range(max(layer_of.values()) + 1)]
    for nid in order:
        layers[layer_of[nid]].append(nid)

    # 重心排序两轮：按前驱平均位置排，减少交叉
    predecessors: Dict[str, List[str]] = {nid: [] for nid in nodes}
    for edge in edges:
        predecessors[edge["to"]].append(edge["from"])
    for _ in range(2):
        pos = {nid: idx for layer in layers for idx, nid in enumerate(layer)}
        for li in range(1, len(layers)):
            layers[li].sort(
                key=lambda nid: (
                    sum(pos[p] for p in predecessors[nid]) / len(predecessors[nid])
                    if predecessors[nid] else pos[nid]
                )
            )
    return layers


def _render_flowchart(spec: Dict, options: Dict):
    nodes, edges = _parse_flow(spec)
    layers = _flow_layers(nodes, edges)
    n_layers = len(layers)
    max_rows = max(len(layer) for layer in layers)
    fig, ax = _canvas(1.6 + 0.72 * max_rows)

    box_w = min(0.19, 0.92 / n_layers * 0.8)
    box_h = min(0.16, 0.75 / max_rows * 0.6)
    for li, layer in enumerate(layers):
        x = 0.05 + (0.9 / max(n_layers - 1, 1)) * li if n_layers > 1 else 0.5
        for ri, nid in enumerate(layer):
            y = 0.85 - (0.78 / (len(layer) + 1)) * (ri + 1) + box_h / 2
            nodes[nid]["cx"] = min(max(x, box_w / 2 + 0.02), 1 - box_w / 2 - 0.02)
            nodes[nid]["cy"] = y

    for edge in edges:
        src, dst = nodes[edge["from"]], nodes[edge["to"]]
        start = (src["cx"] + box_w / 2, src["cy"])
        end = (dst["cx"] - box_w / 2, dst["cy"])
        if dst["cx"] <= src["cx"]:  # 同层/回指：走底部
            start = (src["cx"], src["cy"] - box_h / 2)
            end = (dst["cx"], dst["cy"] - box_h / 2)
        _arrow(ax, start, end, label=edge["label"])

    for node in nodes.values():
        cx_, cy = node["cx"], node["cy"]
        if node["shape"] == "diamond":
            half_w, half_h = box_w * 0.62, box_h * 0.85
            ax.add_patch(Polygon(
                [(cx_ - half_w, cy), (cx_, cy + half_h), (cx_ + half_w, cy), (cx_, cy - half_h)],
                facecolor=_MID_FILL, edgecolor=NAVY, linewidth=1, zorder=3,
            ))
            ax.annotate(_wrap(node["label"], 8), (cx_, cy), ha="center", va="center",
                        fontsize=7.5, color=TEXT, zorder=4, **_TXT)
        else:
            fill = NAVY if node["shape"] == "rounded" else _LIGHT_FILL
            text_color = "#FFFFFF" if node["shape"] == "rounded" else TEXT
            ax.add_patch(FancyBboxPatch(
                (cx_ - box_w / 2, cy - box_h / 2), box_w, box_h,
                boxstyle=("round,pad=0,rounding_size=0.02" if node["shape"] == "rounded"
                          else "square,pad=0"),
                facecolor=fill, edgecolor=NAVY if node["shape"] != "rounded" else fill,
                linewidth=1, zorder=3,
            ))
            ax.annotate(_wrap(node["label"], 10), (cx_, cy), ha="center", va="center",
                        fontsize=7.8, color=text_color, zorder=4, **_TXT)
    return fig


_RENDERERS = {
    "matrix_2x2": _render_matrix_2x2,
    "value_chain": _render_value_chain,
    "process": _render_process,
    "roadmap": _render_roadmap,
    "pyramid": _render_pyramid,
    "flowchart": _render_flowchart,
    "org_chart": lambda s, o: _render_tree_like(s, o, org=True),
    "tree": lambda s, o: _render_tree_like(s, o, org=False),
}


def render_diagram(kind: str, spec: Dict, title: str, source: str | None,
                   options: Dict | None = None) -> bytes:
    """渲染结构图。返回 PNG bytes；任何失败抛 ChartRenderError。"""
    if kind not in _RENDERERS:
        _fail(f"不支持的图类型: {kind}（可用：{', '.join(DIAGRAM_KINDS)}）")
    title = _clean_text(title, "title", limits.MAX_TITLE_CHARS)
    if source is not None:
        source = _clean_text(source, "source", limits.MAX_SOURCE_CHARS)
    spec = _require_dict(spec, "spec")
    options = _require_dict(options, "options") if options is not None else {}

    try:
        fig = _RENDERERS[kind](spec, options)
        return finalize_png(fig, title=title, source=source)
    except ChartRenderError:
        raise
    except Exception as exc:
        raise ChartRenderError(f"结构图渲染失败：{exc}") from exc
