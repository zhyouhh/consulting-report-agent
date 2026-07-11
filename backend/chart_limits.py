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
# flowchart 布局修复（2026-07-11，0710 反馈 #6）：近线性多层流改纵向布局的判据与上限。
# 实测横向布局 5 层起中文标签就装不下列宽（10 汉字 ≈1.08in > 5 层列宽 0.94in），
# 判据取「层数 ≥5 且每层 ≤2 节点」；纵向单图上限 = axes 高度预算 / 最小行高
# （7.5in × 0.8 / 0.5in = 12 层），超限友好失败（拆分子流程）而非产糊图。
FLOW_VERTICAL_MIN_LAYERS = 5      # 层数 ≥ 此值且近线性 → 纵向布局
FLOW_VERTICAL_MAX_ROWS = 2        # 「近线性」判据：每层节点数 ≤ 此值
FLOW_MAX_VERTICAL_LAYERS = 12     # 纵向单图层数上限（超出抛 ChartRenderError）
FLOW_MIN_ROW_HEIGHT_IN = 0.5      # 纵向每层最小行高（节点框 + 行间箭头）

# ---- 文本 ----
MAX_TITLE_CHARS = 80              # 结论式标题
MAX_LABEL_CHARS = 60              # 单个标签/节点文本
MAX_SOURCE_CHARS = 200            # 来源行

# ---- 输出 ----
RENDER_DPI = 200                  # 6.4in 物理宽 × 200dpi = 1280px，docx 内清晰且不超 A4 文字区
MAX_FIGURE_WIDTH_IN = 6.4         # A4 文字区约 6.5in，恒不超宽（pandoc 按 PNG DPI 元数据取物理尺寸）
MAX_FIGURE_HEIGHT_IN = 7.5
MAX_SIDECAR_BYTES = 64 * 1024     # sidecar json 落盘上限
