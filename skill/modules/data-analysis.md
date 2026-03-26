# 商业数据分析模块

## 一、数据分析在咨询中的作用

数据分析用于发现问题、验证假设、支持决策。

## 二、分析流程

### 2.1 数据准备
- 数据收集
- 数据清洗
- 数据整合

### 2.2 探索性分析
- 描述性统计
- 数据可视化
- 异常值识别

### 2.3 深度分析
- 趋势分析
- 对比分析
- 相关性分析
- 根因分析

## 三、常用分析方法

### 3.1 描述性分析
- 均值、中位数、众数
- 标准差、方差
- 分布分析

### 3.2 对比分析
- 同比、环比
- 对标分析
- 前后对比

### 3.3 趋势分析
- 时间序列分析
- 移动平均
- 增长率分析

### 3.4 相关性分析
- 散点图
- 相关系数
- 回归分析

## 四、Python 数据分析示例

### 4.1 数据清洗

```python
import pandas as pd

# 读取数据
df = pd.read_csv('data.csv')

# 处理缺失值
df = df.dropna()  # 删除缺失值
# 或
df = df.fillna(0)  # 填充缺失值

# 处理重复值
df = df.drop_duplicates()

# 数据类型转换
df['date'] = pd.to_datetime(df['date'])
```

### 4.2 描述性统计

```python
# 基本统计
print(df.describe())

# 分组统计
df.groupby('category')['sales'].agg(['mean', 'sum', 'count'])
```

### 4.3 趋势分析

```python
import matplotlib.pyplot as plt

# 时间序列图
df.plot(x='date', y='sales', figsize=(12, 6))
plt.title('销售趋势')
plt.xlabel('日期')
plt.ylabel('销售额')
plt.savefig('trend.png')
```

## 五、质量检查

- [ ] 数据来源是否可靠
- [ ] 数据清洗是否充分
- [ ] 分析方法是否适当
- [ ] 结论是否有数据支撑
- [ ] 图表是否清晰易懂
