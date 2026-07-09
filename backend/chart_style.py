"""咨询图表 house style：字体注册 + 配色 + Figure 工厂（叶子模块）。

依赖：matplotlib + config.get_base_path（定位仓库/打包根下的 fonts/），绝不依赖
chat/skill/main。chart_render / diagram_render 共享本模块，保证两类图同一套手艺。

并发硬约束（spec §4.8）：全程 Agg 后端 + 面向对象 API（Figure + FigureCanvasAgg），
不碰 matplotlib 的全局状态机接口——多项目并发渲染跑在不同 worker 线程，全局接口
非线程安全（tests/test_chart_render.py 有 source-guard 锁死）。字体注册进程内只做
一次（锁保护），渲染期对 font_manager 只读。
"""

from __future__ import annotations

import io
import threading

import matplotlib

matplotlib.use("Agg", force=True)  # 必须在任何 figure 创建前锁定无头后端

from matplotlib import font_manager  # noqa: E402
from matplotlib.backends.backend_agg import FigureCanvasAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

from .chart_limits import RENDER_DPI  # noqa: E402
from .config import get_base_path  # noqa: E402


class ChartRenderError(ValueError):
    """渲染失败统一异常：坏数据 / 超限 / 字体缺失。message 必须是给模型看的人话。"""


# ---- 配色（对齐前端 redesign 海军蓝 #1B2A4A）----
NAVY = "#1B2A4A"
PALETTE = [
    "#1B2A4A",  # 海军蓝（主）
    "#2E6E8E",  # 青蓝
    "#C89B3C",  # 金
    "#7A8899",  # 灰蓝
    "#4A7856",  # 墨绿
    "#A34E3F",  # 砖红
    "#5B4E77",  # 灰紫
    "#8A9BB0",  # 浅灰蓝
]
POSITIVE = "#2F6B4F"   # 瀑布图增量
NEGATIVE = "#A34E3F"   # 瀑布图减量
GRID = "#D8DDE4"
TEXT = "#1F2733"
MUTED = "#6B7686"
BG = "#FFFFFF"

FONT_FAMILY = "Noto Sans CJK SC"
_FONT_FILES = ("NotoSansCJKsc-Regular.otf", "NotoSansCJKsc-Bold.otf")

_fonts_ready = False
_fonts_lock = threading.Lock()


def ensure_fonts_registered() -> str:
    """注册仓库自带 CJK 字体（幂等、线程安全）。返回 family 名。

    字体是仓库资产（fonts/），不依赖操作系统字体——kr-web-01 这类 Linux 服务器
    默认无中文字体，不注册则全是方框（spec §5 最易漏的坑）。
    """
    global _fonts_ready
    if _fonts_ready:
        return FONT_FAMILY
    with _fonts_lock:
        if _fonts_ready:
            return FONT_FAMILY
        fonts_dir = get_base_path() / "fonts"
        missing = [name for name in _FONT_FILES if not (fonts_dir / name).is_file()]
        if missing:
            raise ChartRenderError(
                "图表渲染所需的中文字体缺失（fonts/ 目录不完整），请联系管理员检查部署。"
            )
        for name in _FONT_FILES:
            font_manager.fontManager.addfont(str(fonts_dir / name))
        _fonts_ready = True
    return FONT_FAMILY


def new_figure(width_in: float, height_in: float) -> Figure:
    """建 OO Figure（不经全局状态机接口），并挂 Agg canvas。"""
    ensure_fonts_registered()
    fig = Figure(figsize=(width_in, height_in), dpi=RENDER_DPI)
    FigureCanvasAgg(fig)
    fig.patch.set_facecolor(BG)
    return fig


def apply_axes_style(ax, *, y_grid: bool = True) -> None:
    """去 chart junk：只留左/下轴线，浅色横向网格线在数据层之下。"""
    ax.set_facecolor(BG)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=8.5, labelfontfamily=FONT_FAMILY)
    if y_grid:
        ax.yaxis.grid(True, color=GRID, linewidth=0.7)
        ax.set_axisbelow(True)


def finalize_png(fig: Figure, *, title: str, source: str | None, unit: str | None = None) -> bytes:
    """统一收尾：结论式标题（左对齐加粗）+ 单位注记 + 来源脚注 → PNG bytes。"""
    fig.suptitle(
        title,
        x=0.02, y=0.985, ha="left", va="top",
        fontsize=12.5, fontweight="bold", color=NAVY, fontfamily=FONT_FAMILY,
    )
    if unit:
        fig.text(
            0.98, 0.985, f"单位：{unit}", ha="right", va="top",
            fontsize=8, color=MUTED, fontfamily=FONT_FAMILY,
        )
    if source:
        fig.text(
            0.02, 0.012, f"来源：{source}", ha="left", va="bottom",
            fontsize=7.5, color=MUTED, fontfamily=FONT_FAMILY,
        )
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=RENDER_DPI, facecolor=BG)
    return buf.getvalue()


def series_color(index: int) -> str:
    return PALETTE[index % len(PALETTE)]
