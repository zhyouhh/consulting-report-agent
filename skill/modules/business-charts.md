# 商业图表生成模块

## 一、图表类型与应用场景

### 1.1 瀑布图（Waterfall Chart）
**应用场景**: 财务分析、成本分解、利润桥接
**适用于**: 展示累积效应和各因素贡献

### 1.2 热力图（Heatmap）
**应用场景**: 优先级矩阵、风险评估、相关性分析
**适用于**: 展示二维数据的强度分布

### 1.3 漏斗图（Funnel Chart）
**应用场景**: 转化分析、销售漏斗、用户流程
**适用于**: 展示逐步递减的流程

### 1.4 甘特图（Gantt Chart）
**应用场景**: 项目计划、实施路线图、时间线
**适用于**: 展示任务时间安排和依赖关系

### 1.5 桑基图（Sankey Diagram）
**应用场景**: 流程分析、资源流动、价值链
**适用于**: 展示流量的分配和转移

## 二、技术栈

推荐使用 Python + Plotly 实现交互式图表。

**使用辅助库**: 项目提供了 `lib/chart_utils.py` 封装了常用图表函数，可直接调用。

```python
from lib.chart_utils import (
    create_waterfall_chart,
    create_heatmap,
    create_funnel_chart,
    create_gantt_chart,
    create_sankey_diagram,
    create_bubble_chart
)
```

## 三、代码示例

### 3.1 瀑布图示例

```python
from lib.chart_utils import create_waterfall_chart

# 数据
categories = ['初始收入', '成本节约', '新业务', '运营成本', '最终收入']
values = [100, 20, 30, -15, 135]

# 创建瀑布图
create_waterfall_chart(
    categories=categories,
    values=values,
    title='收入变化分析',
    output_path='waterfall_chart.html'
)
```

### 3.2 热力图示例

```python
from lib.chart_utils import create_heatmap
import pandas as pd

# 优先级矩阵数据
df = pd.DataFrame({
    '影响': ['高', '高', '中', '中', '低'],
    '难度': ['低', '高', '低', '高', '中'],
    '项目': ['项目A', '项目B', '项目C', '项目D', '项目E']
})

# 创建优先级矩阵
create_heatmap(
    data=df,
    x_col='难度',
    y_col='影响',
    text_col='项目',
    title='项目优先级矩阵',
    output_path='priority_matrix.html'
)
```

### 3.3 漏斗图示例

```python
from lib.chart_utils import create_funnel_chart

# 转化漏斗数据
stages = ['访问', '注册', '试用', '付费', '续费']
values = [10000, 5000, 2000, 800, 600]

create_funnel_chart(
    stages=stages,
    values=values,
    title='用户转化漏斗',
    output_path='funnel_chart.html'
)
```

### 3.4 甘特图示例

```python
from lib.chart_utils import create_gantt_chart

# 项目任务数据
tasks = [
    dict(Task='需求分析', Start='2026-03-01', Finish='2026-03-15', Resource='团队A'),
    dict(Task='方案设计', Start='2026-03-10', Finish='2026-03-25', Resource='团队B'),
    dict(Task='开发实施', Start='2026-03-20', Finish='2026-04-30', Resource='团队A'),
    dict(Task='测试验收', Start='2026-04-25', Finish='2026-05-15', Resource='团队C'),
]

create_gantt_chart(
    tasks=tasks,
    title='项目实施计划',
    output_path='gantt_chart.html'
)
```

### 3.5 桑基图示例

```python
from lib.chart_utils import create_sankey_diagram

# 流动数据
flows = {
    'source': [0, 0, 1, 1, 2],
    'target': [2, 3, 2, 3, 4],
    'value': [8, 2, 2, 8, 10],
    'label': ['原材料', '供应商', '生产', '分销', '终端']
}

create_sankey_diagram(
    flows=flows,
    title='供应链流动分析',
    output_path='sankey_diagram.html'
)
```

### 3.6 气泡图示例

```python
from lib.chart_utils import create_bubble_chart
import pandas as pd

# 市场竞争数据
df = pd.DataFrame({
    '市场份额': [20, 15, 10, 8, 5],
    '增长率': [15, 25, 10, 5, 30],
    '收入': [100, 80, 60, 40, 20],
    '公司': ['公司A', '公司B', '公司C', '公司D', '公司E']
})

create_bubble_chart(
    data=df,
    x_col='市场份额',
    y_col='增长率',
    size_col='收入',
    text_col='公司',
    title='市场竞争格局',
    output_path='bubble_chart.html'
)
```

## 四、图表设计原则

1. **简洁明了**: 避免过度装饰
2. **数据准确**: 确保数据来源可靠
3. **颜色一致**: 使用统一的配色方案
4. **标注清晰**: 添加必要的标签和说明
5. **交互友好**: 利用 Plotly 的交互特性

## 五、常用配色方案

### 专业配色
- 主色：#1f77b4（蓝色）
- 辅色：#ff7f0e（橙色）
- 强调：#2ca02c（绿色）
- 警告：#d62728（红色）

### McKinsey 风格
- 深蓝：#003f5c
- 中蓝：#58508d
- 紫色：#bc5090
- 粉色：#ff6361
- 橙色：#ffa600
