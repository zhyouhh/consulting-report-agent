"""图表渲染资源上限（叶子模块，只依赖 stdlib——对齐 material_limits.py 的定位）。

所有上限服务同一个目标：把最坏渲染耗时/内存压到亚秒级、亚百 MB 级——
matplotlib 渲染跑在 chat worker 线程里，Python 线程级 timeout 杀不掉 C 层渲染，
所以防线只能设在输入侧（spec §4.8）。超限一律抛可控错误，绝不 OOM / 卡 worker。
"""

# ---- 数据图（create_chart）----
MAX_SERIES = 8                    # 多序列图（grouped/stacked/line）最大序列数
MAX_POINTS_PER_SERIES = 60        # 每序列最大数据点数
MAX_CATEGORIES = 30               # 类目轴最大类目数
MAX_SCATTER_POINTS = 200          # scatter/bubble 最大点数
MAX_HEATMAP_CELLS = 400           # heatmap 最大单元格数（rows×cols）

# ---- 结构图（create_diagram）----
MAX_DIAGRAM_ITEMS = 24            # matrix 落点 / process 步骤 / roadmap 阶段项等
MAX_TREE_NODES = 40               # org_chart / tree 总节点数
MAX_TREE_DEPTH = 5                # org_chart / tree 最大层深
MAX_FLOW_NODES = 20               # flowchart 节点数
MAX_FLOW_EDGES = 40               # flowchart 边数

# ---- 文本 ----
MAX_TITLE_CHARS = 80              # 结论式标题
MAX_LABEL_CHARS = 60              # 单个标签/节点文本
MAX_SOURCE_CHARS = 200            # 来源行

# ---- 输出 ----
RENDER_DPI = 200                  # 6.4in 物理宽 × 200dpi = 1280px，docx 内清晰且不超 A4 文字区
MAX_FIGURE_WIDTH_IN = 6.4         # A4 文字区约 6.5in，恒不超宽（pandoc 按 PNG DPI 元数据取物理尺寸）
MAX_FIGURE_HEIGHT_IN = 7.5
MAX_SIDECAR_BYTES = 64 * 1024     # sidecar json 落盘上限
