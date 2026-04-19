# 真实 Token 统计与回合后自动压缩设计稿

## 1. 背景

当前项目的 token 使用显示与上下文压缩触发逻辑，主要依赖本地估算：

1. 后端优先读取 `response.usage.total_tokens`，拿不到时就回退到 `tiktoken` / 字符长度估算。
2. 前端只显示一个 `current_tokens / max_tokens`，并用 `usage_mode=estimated` 或 `actual` 粗区分。
3. 压缩触发发生在请求发送前，依据的是本地估算后的对话长度，而不是 provider 真正返回的 usage。
4. `gemini-3-flash` 当前在本项目里的有效上限被设成 `500k`，明显偏高，容易掩盖真实逼近上限的风险。

这带来几个直接问题：

1. 用户看到的 token 数经常偏低，难以信任。
2. 压缩是否触发、何时触发，与前端显示口径不一致。
3. 后端无法区分“provider 真没给 usage”和“我们自己只是在估算”。
4. 压缩行为缺乏真实 usage 支撑，容易要么压得太早，要么压得太晚。

用户这轮的核心目标非常明确：

1. 显示层必须尽量只认 provider 真实 usage，不再把估算值伪装成真实值。
2. `gemini-3-flash` 的默认有效上限改回 `200k`。
3. 自动压缩改为“本轮结束后，根据真实 usage 判断是否触发”，而不是发送前靠估算猜。
4. 整体方案应保持中等复杂度，不引入 Claude Code 那种重型 session memory / forked compact 系统。

## 2. 目标

本次改造目标是：

1. 把主对话链路、流式链路、压缩链路的 token 统计统一到一套“provider 真实 usage 优先”的后端结构。
2. 把 `gemini-3-flash` 的默认有效上限从 `500k` 改回 `200k`。
3. 让前端只显示真实 usage 已知字段；未知字段明确标记为“未提供”，不再用估算值补位。
4. 在每一轮回复完成后，依据真实 usage 判断是否达到自动压缩阈值，并立即执行 compact，供下一轮继续使用。
5. 保持现有聊天、SSE、设置与工作流逻辑兼容，不引入新的重型上下文系统。

## 3. 非目标

本次不做：

1. 不引入 Claude Code 风格的 session memory compaction。
2. 不引入 forked compact agent、prompt cache sharing、post-compact file reinjection 等重系统。
3. 不把“工具输出单独占用多少 token”的估算值混进主 UI 指标。
4. 不要求在发送前就 100% 精确预测本轮会占用多少 token。
5. 不改现有工作流门禁逻辑（如 write gate / fetch gate）的业务规则。

## 4. 关键设计决策

### 4.1 真实 usage 优先，拒绝把估算值伪装成真实值

统一原则：

1. 主显示只认 provider 返回的真实 usage。
2. 如果 provider 缺少某个字段，就明确标记为 `None` / `unavailable`，不再补估算值冒充真实值。
3. 本地估算仍可保留在内部调试或兼容路径中，但不能作为主显示值返回给前端。

### 4.2 自动压缩改为回合结束后触发

新规则：

1. 本轮请求先正常完成。
2. 从 provider 响应中提取真实 usage。
3. 用真实 usage 中最适合表示“上下文已使用量”的字段计算占用比例。
4. 若达到阈值，则在本轮结束后立即做 compact。
5. compact 结果用于下一轮对话，不影响本轮已经返回给用户的文本。

这里需要额外明确两层压缩：

1. `发送前安全兜底压缩`
   - 继续保留
   - 只用于避免请求在物理上直接超过 provider 上限
   - 可以继续使用本地估算
   - 不参与前端主 token 显示
   - 不算作“真实 usage 驱动的 auto-compact 成功”
2. `回合结束后的真实 usage auto-compact`
   - 本轮新增主路径
   - 只在拿到真实 usage 后触发
   - 是用户可见的“系统已自动整理上下文”来源

### 4.3 `gemini-3-flash` 有效上限改回 200k

现有：

1. provider 上限：`1_000_000`
2. effective 上限：`500_000`

调整后：

1. provider 上限仍可保留为 `1_000_000`
2. effective 上限改为 `200_000`

这样可以：

1. 继续保留“provider 真正物理上限”和“本项目保守有效上限”的区分。
2. 让 UI 与 auto-compact 逻辑都对齐到更保守、更可信的 `200k`。

## 5. 后端设计

### 5.1 引入统一 usage 归一化结构

新增一个内部 usage 结构，建议字段如下：

1. `usage_source`
   - `provider`
   - `provider_partial`
   - `unavailable`
2. `context_used_tokens`
   - 用于上下文占用判断的真实 token 值
3. `input_tokens`
4. `output_tokens`
5. `total_tokens`
6. `cache_read_tokens`
7. `cache_write_tokens`
8. `reasoning_tokens`
9. `effective_max_tokens`
10. `provider_max_tokens`
11. `preflight_compaction_used`
12. `post_turn_compaction_status`
   - `not_needed`
   - `completed`
   - `failed`
   - `skipped_unavailable`
13. `raw_usage`
   - 原样保留 provider usage dump，便于诊断
14. `max_tokens`
   - 兼容别名，语义等同 `effective_max_tokens`

兼容策略：

1. `max_tokens` 本轮继续保留，用于兼容现有前端与测试。
2. 但新逻辑与新 UI 应优先读 `effective_max_tokens`。

### 5.2 上下文占用字段的取值规则

`context_used_tokens` 的取值顺序必须固定：

1. 优先取 provider 真实输入侧字段：
   - `prompt_tokens`
   - `input_tokens`
   - 其他兼容 provider 的同义字段
2. 若输入侧字段缺失，但 `total_tokens` 存在，则退一步使用 provider 的 `total_tokens`
3. 若连这些都没有，则：
   - `context_used_tokens=None`
   - `usage_source=unavailable`

这里的“退一步”仍然属于 provider 真值，不是本地估算。

### 5.3 非流式与流式链路都要接 usage 归一化

非流式 `chat()`：

1. 现有 `response.usage.total_tokens` 读取逻辑要替换为完整归一化函数。
2. 返回给前端的 `token_usage` 统一使用新结构。

一个 turn 可能包含多次 provider 调用（如工具轮次），这轮必须把主口径定死：

1. 主 `token_usage` 只代表“本 turn 最后一次产出最终 assistant 文本的 provider 调用”。
2. 工具轮次中的中间 provider 调用不混入主 UI token 数。
3. compact 调用的 usage 也不混入主 UI token 数。
4. 如果后端需要更细调试，可额外保留内部日志或附加结构，但不进入本轮主显示。

流式 `chat_stream()`：

1. 现有实现基本只在结尾发一条 `usage` 事件，并且固定走估算。
2. 需要改成：
   - 尝试从流式响应中读取真实 usage
   - 如果当前 provider / SDK 支持 `stream_options.include_usage`，则在流式完成时解析它
   - 如果流式链路拿不到 usage，则标记为 `provider_partial` 或 `unavailable`，不要伪造成 estimated
3. SSE `usage` 事件的数据结构与非流式返回保持一致。

### 5.4 compact 自己也要单独记 usage

compact/summarize 调用本身也属于模型请求，应单独记录 usage：

1. 主回复 usage 与 compact usage 不混合。
2. 本轮返回给前端的主 `token_usage` 以“主回复 usage”为主。
3. 可额外在内部记录一份 `compact_usage`，供日志或后续调试使用。

本轮前端不强制展示 compact usage，但后端结构要预留好。

### 5.4.1 compact 结果的持久化落点

回合后 auto-compact 若要对下一轮生效，必须持久化到项目目录，而不能只留在本轮内存里。

本轮建议新增一个 sidecar 文件，例如：

1. `conversation_compact_state.json`

该文件至少包含：

1. `summary_text`
2. `last_compacted_at`
3. `source_message_count`
   - 表示这份 summary 覆盖了 `conversation.json` 开头多少条可见历史消息
4. `post_turn_compaction_status`
5. `trigger_usage`

下一轮构建 provider conversation 时：

1. 先读 `conversation_compact_state.json`
2. 若存在有效 compact summary，则在 `system prompt` 之后、历史消息之前注入一条内部 compact summary 消息
3. 同时只保留 `conversation.json` 中“未被 summary 覆盖的尾部消息”进入 provider conversation
   - 即：跳过前 `source_message_count` 条历史消息
4. `conversation.json` 仍保留真实 user/assistant 可见聊天记录，不直接改写成摘要，避免污染前端聊天展示

这样 `summary + 未压缩尾部` 才会真正缩短上下文；绝不能变成 `summary + 全量历史`。

这样可以保证：

1. 下一轮 provider 真正吃到 compact summary
2. 前端历史区不需要展示自动生成的 compact 摘要
3. 不需要引入更重的 session memory 系统

### 5.4.2 sidecar 生命周期

`conversation_compact_state.json` 的生命周期必须明确：

1. `clear conversation`
   - 删除 `conversation.json` 时，必须同步删除 `conversation_compact_state.json`
2. `delete project`
   - 删除项目目录时自然一起删除 sidecar
3. `compact success`
   - 用最新 summary 原子替换旧 sidecar
4. `compact failed`
   - 保留上一份成功 sidecar，不写入失败状态污染文件
5. `history shorter than source_message_count`
   - 构建 provider conversation 时若发现 `conversation.json` 长度异常短于 sidecar 记录，视为 sidecar 失效并丢弃

sidecar 文件写入要求：

1. `conversation_compact_state.json` 应尽量通过“先写临时文件，再原子替换”的方式落盘
2. 避免流式结束瞬间被下一轮读到半写入内容

### 5.5 自动压缩触发规则

建议新增明确阈值：

1. `AUTO_COMPACT_TRIGGER_RATIO = 0.9`

触发逻辑：

1. 本轮主回复结束后，拿到归一化 usage。
2. 仅当 `context_used_tokens` 存在且 `effective_max_tokens > 0` 时才计算比例。
3. 若 `context_used_tokens / effective_max_tokens >= 0.9`，则执行 compact。
4. compact 失败时：
   - 不影响本轮回复结果
   - 只记录日志与状态
   - 下轮仍允许继续，但 UI 可以提示本轮未完成自动整理

新增明确状态语义：

1. `preflight_compaction_used`
   - `True` 表示本轮在发送前为了物理安全做过兜底压缩
2. `post_turn_compaction_status`
   - `not_needed`: usage 未到阈值
   - `completed`: usage 达阈值且 compact 已完成持久化
   - `failed`: usage 达阈值但 compact 失败
   - `skipped_unavailable`: provider 未提供足够真实 usage，无法判定是否触发

### 5.6 与旧估算逻辑的关系

本轮不再把估算 token 直接回给前端。

允许保留的估算用途：

1. 发送前物理安全兜底压缩，可内部继续使用估算。
2. 极少数内部保护逻辑仍需要一个本地近似值时，可内部使用。
3. 但这些估算值不得再进入公开 API 的 `token_usage` 主字段。

如果完全不再需要现有 `_estimate_tokens()` 参与显示或 compact 判定，可以逐步弱化其角色，只保留在异常兜底、预算异常报错等少数场景里。

## 6. 前端设计

### 6.1 现有显示问题

当前前端在 [contextUsage.js] 中把 usage 简化为：

1. `current_tokens`
2. `max_tokens`
3. `usage_mode` = `actual/estimated`
4. `compressed`

这会造成：

1. 信息过粗
2. “estimated” 和真实值并排时容易误导
3. 无法清楚说明 provider 到底给了哪些真实字段

### 6.2 新的展示结构

前端建议拆成两层：

1. 核心摘要
   - `上下文已用: context_used_tokens / effective_max_tokens`
   - `使用率: xx%`
   - `来源: provider真实统计 / provider部分提供 / 未提供`
   - `当前有效上限: 200k`
2. 明细
   - `input_tokens`
   - `output_tokens`
   - `total_tokens`
   - `cache_read_tokens`
   - `cache_write_tokens`
   - `reasoning_tokens`

规则：

1. 有值就显示。
2. 没值就显示 `未提供`。
3. 不再显示 `estimated` 作为主模式标签。

### 6.3 自动压缩的用户提示

当回合结束后触发 auto-compact：

1. UI 不表现为异常。
2. 而是显示温和系统提示，例如：
   - `本轮上下文占用已达阈值，系统已自动整理上下文。`
   - `本轮上下文占用已达阈值，但自动整理失败。`

前端不再只看一个布尔值，而是读取：

1. `preflight_compaction_used`
2. `post_turn_compaction_status`

从而准确区分：

1. 未触发
2. 触发并成功
3. 触发但失败
4. 仅发生了发送前安全兜底压缩

### 6.4 SSE 完成时序

为了保证“compact 后的结果供下一轮立即使用”，流式链路必须定死结束顺序：

1. assistant 文本块流式输出完成
2. 后端归一化真实 usage
3. 若达到阈值，则同步执行 compact 并完成持久化
4. 发送最终 `usage` SSE 事件
5. 最后才发送 `[DONE]`

这样前端虽然会在最后收尾阶段多等一小段时间，但一旦用户看到本轮真正结束，下一轮就一定已经基于最新 compact 状态。

## 7. 测试策略

采用 TDD，至少覆盖以下矩阵：

### 7.1 context policy

1. `gemini-3-flash` 的 effective 上限改为 `200_000`
2. provider 上限仍保留 `1_000_000`

### 7.2 usage 归一化

1. 非流式响应包含 `prompt_tokens/completion_tokens/total_tokens`
2. 只有 `input_tokens/output_tokens/total_tokens`
3. 包含 cache / reasoning 细字段
4. 只有 `total_tokens`
5. 完全没有 usage

断言：

1. `usage_source`
2. `context_used_tokens`
3. 所有真实字段都被正确映射
4. 无字段时不回退估算给前端

### 7.3 回合后 auto-compact

1. usage 未达到阈值时不 compact
2. usage 达到阈值时 compact
3. compact 失败时不影响主回复
4. `post_turn_compaction_status` 正确
5. compact summary 已正确持久化，且下一轮 provider conversation 会读取它

### 7.4 流式链路

1. SSE `usage` 事件结构与非流式对齐
2. 流式有真实 usage 时前端收到真实值
3. 流式拿不到 usage 时明确标记 `provider_partial` / `unavailable`
4. `[DONE]` 只在 compact 持久化完成后发出

### 7.5 前端展示

1. `context_used_tokens / effective_max_tokens` 正确显示
2. `未提供` 展示逻辑正确
3. auto-compact 状态提示正确

## 8. 风险与权衡

### 8.1 部分 provider 可能不给完整 usage

这轮策略不是“估算补齐”，而是明确暴露“未提供”。

好处：

1. 用户终于能相信显示出来的值是真的

代价：

1. 某些 provider 上看到的信息会变少

### 8.2 回合后才压缩意味着下一轮前才真正生效

这符合用户要求，也更自然，但意味着：

1. 当前轮结束时上下文可能已经很高
2. 只有在本轮完成后才立即整理

这是可接受权衡。

### 8.3 工具输出占用无法做到完全真实拆分

provider usage 一般不会按 tool 粒度拆账。
因此本轮不把工具占用拆分放进主 UI，是正确取舍。

## 9. 最终建议

采用“中量一体版”：

1. 真实 usage 归一化
2. `gemini-3-flash` 默认有效上限改回 `200k`
3. 回合结束后基于真实 usage 自动 compact
4. 前端只显示 provider 真值，不再把估算值冒充真实值

这样可以一次性解决“token 显示不可信、压缩触发不可信、默认上限过高”这三个同源问题，同时避免引入过重的新上下文系统。
