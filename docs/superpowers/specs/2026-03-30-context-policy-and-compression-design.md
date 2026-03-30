# 上下文策略与压缩改造设计稿

## 1. 背景

当前客户端的上下文处理有三个根问题：

1. 后端把 `context_window=128000` 和 `compress_threshold=60000` 写死，所有模型都按同一档处理。
2. 前端直接显示这个写死值，导致用户看到的 `128k` 不是模型真实能力，也不是产品真实策略。
3. 对话压缩触发和压缩结果都建立在粗糙估算上，但界面没有明确告诉用户“这是估算值”。

这会直接造成两个后果：

1. 默认通道 `gemini-3-flash` 明明是大上下文模型，却被客户端错误地按 `128k` 管理。
2. 自定义 API 的上下文预算完全不可信，用户无法判断压缩是否合理。

## 2. 目标

本次改造只解决一件事：把“模型理论上限”“客户端有效上限”“压缩阈值”“展示口径”拆清楚，并让默认通道和常见自定义模型都走一套可解释、可维护的上下文策略。

成功标准：

1. 默认通道 `gemini-3-flash` 按 `1M` 理论上限识别，但客户端有效上限固定为 `500k`。
2. 压缩阈值不再写死，而是按有效上限动态计算。
3. 前端显示的上下文用量来自运行时解析后的真实策略值，不再显示全局写死 `128k`。
4. 自定义 API 支持常见模型自动识别；识别不到时保守回退，并允许用户手动覆盖。
5. UI 明确区分 `估算` 和 `实际`，不再把估算值伪装成真实 token。

## 3. 非目标

本次不做：

1. 不接入各家原生 tokenizer API 来追求发送前“绝对精确”的 token 计算。
2. 不维护一张覆盖所有供应商、所有版本别名的庞大模型百科。
3. 不改变现有 `conversation.json` 的持久化机制。
4. 不在这次改造里顺手解决输入框、阶段同步、文件落盘等其他 bug。

## 4. 核心设计

### 4.1 运行时上下文策略对象

后端每轮请求都解析一个运行时策略对象 `ResolvedContextPolicy`，至少包含：

- `normalized_model`
- `provider_context_limit`
- `effective_context_limit`
- `compress_threshold`
- `reserved_output_tokens`
- `usage_mode`
- `matched_by`

其中：

- `provider_context_limit` 表示模型理论上限。
- `effective_context_limit` 表示客户端本轮最多愿意喂给模型多少上下文。
- `compress_threshold` 表示达到该阈值后开始压缩旧历史。
- `usage_mode` 只允许 `estimated` 或 `actual`。
- `matched_by` 用于调试和说明本轮是“精确命中”“家族命中”还是“回退值”。

### 4.2 模型映射策略

采用三段式解析：

1. 归一化模型名
2. 先查精确命中表
3. 再查家族 fallback
4. 都识别不到则回退 `128k`

归一化规则：

1. 全部转小写
2. 去掉厂商前缀，例如 `moonshotai/`
3. 不额外裁剪 `-thinking`、`-image`、`-beta` 之类后缀，避免误杀

### 4.3 第一批内置档位

不为每个模型维护独立策略，而是归入少量档位：

- `tier_1m`
  - `provider_context_limit = 1_000_000`
  - `default_effective_context_limit = 500_000`
- `tier_400k`
  - `provider_context_limit = 400_000`
  - `default_effective_context_limit = 320_000`
- `tier_256k`
  - `provider_context_limit = 256_000`
  - `default_effective_context_limit = 200_000`
- `tier_200k`
  - `provider_context_limit = 200_000`
  - `default_effective_context_limit = 180_000`
- `tier_128k`
  - `provider_context_limit = 128_000`
  - `default_effective_context_limit = 110_000`

### 4.4 默认通道策略

默认通道当前模型是 `gemini-3-flash`，按 `tier_1m` 识别：

- 理论上限：`1_000_000`
- 客户端有效上限：`500_000`

这不是在否认模型更大的理论能力，而是在明确产品策略：对内部同事默认限制在更稳妥的区间内，避免“上下文太大反而发傻”。

### 4.5 自定义 API 策略

自定义 API 默认按模型名自动识别。

如果模型识别失败：

- 理论上限回退到 `128_000`
- 有效上限回退到 `110_000`

同时允许用户在连接设置中手动填写一个“有效上下文上限覆盖值”。该字段为空时表示自动识别；填写后表示覆盖默认策略。

这里不额外引入“自动识别开关”布尔值。为空即自动识别，填写即覆盖，已经足够。

## 5. 压缩与预算规则

### 5.1 预算规则

请求发送前先构建完整 provider conversation，然后做预算估算。

计算规则：

1. `reserved_output_tokens = min(8192, floor(effective_context_limit * 0.2))`
2. `compress_threshold = min(floor(effective_context_limit * 0.9), effective_context_limit - reserved_output_tokens)`

例子：

- `500k` 有效上限 -> 阈值约 `450k`
- `110k` 有效上限 -> 阈值约 `99k`

### 5.2 压缩规则

压缩仍然保留现有总体思路：保留系统消息、保留最近消息、把旧历史折叠成摘要。

但要改成：

1. 基于运行时 `compress_threshold` 决定是否压缩
2. 压缩一次后重新估算
3. 如果仍超阈值，则继续收紧旧历史
4. 如果超限主要来自“本轮材料过大”，则直接报错，不再无限压缩

### 5.3 图片估算

当前实现把图片以 base64 data URL 直接塞进估算，误差很大。

本次改造不追求跨模型精确图片 token 计数，但要避免继续用 base64 长度充当上下文成本。

简化策略：

1. 文本仍按 tokenizer 或字符估算
2. 图片按固定保守成本估算，或者单独标记“图片未精确计入”
3. 不再直接拿 base64 字符串长度参与预算

## 6. 前后端接口调整

### 6.1 后端 usage 载荷

将当前 usage 数据扩展为：

- `current_tokens`
- `effective_max_tokens`
- `provider_max_tokens`
- `compressed`
- `usage_mode`

其中：

- `current_tokens` 若无上游真实 usage，则仍为估算值
- `usage_mode=actual` 仅在上游明确返回 usage 时使用

### 6.2 前端展示

聊天底部上下文条显示为：

- `上下文估算 132k / 500k`
- 或 `上下文用量 118k / 500k`

并显示小标签：

- `估算` / `实际`
- `已压缩`

默认通道可额外显示只读说明：

- 理论上限：`1,000k`
- 当前客户端有效上限：`500k`

## 7. 兼容性

### 7.1 配置兼容

旧版本配置文件里没有上下文覆盖字段时，应自动补默认值，不得导致启动失败。

本次只新增一个需要持久化的高级配置字段：

- `custom_context_limit_override`

为空表示自动识别；有值表示覆盖。

### 7.2 旧字段处理

旧的：

- `context_window`
- `compress_threshold`

可以继续保留在 `Settings` 中用于兼容读取，但运行时不应再把它们视为唯一真相。真正的请求预算应由 `ResolvedContextPolicy` 每轮动态计算。

## 8. 风险与约束

1. OpenAI 兼容接口通常不会返回模型上下文元数据，所以“自动识别”只能依赖内置映射和保守 fallback。
2. 部分网关模型名是别名，无法保证精确匹配官方命名，因此必须允许手动覆盖。
3. 前置预算永远是估算，不可能在兼容模式下做到发送前绝对精确。

## 9. 决策摘要

本次设计的关键判断是：

1. 不去追求玄学自动探测，而是用“小型模型目录 + 家族 fallback + 手动覆盖”解决大部分真实场景。
2. 不再把模型理论上限和产品策略混为一谈。
3. 不再把估算值伪装成真实 usage。
4. 默认通道 `gemini-3-flash` 明确按 `500k` 有效上限运行。
