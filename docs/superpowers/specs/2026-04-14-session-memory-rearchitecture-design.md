# 会话记忆重构设计稿

## 1. 背景

当前项目的会话层只有一层真正持久化的事实源：

1. `conversation.json`
2. 内容基本只包含 `user/assistant` 的可见聊天文本
3. `attached_material_ids` 只记录“本轮带了哪些材料”，不记录“读出了什么内容”

这套结构有一个很直接的缺陷：

1. 模型本轮调用了 `read_material_file`、`read_file`、`fetch_url`，读到的事实并不会在下一轮稳定回注
2. 下一轮上下文主要还是靠聊天文本回放
3. 读材料、看网页、写文件这些真正有价值的工作轨迹，没有变成结构化记忆

结果就是：

1. 模型容易忘记自己已经读过什么
2. 长对话里的 token 用量增长不符合真实工作量
3. 继续写作、继续分析时，经常像“重新开局”

对比已经分析过的 `opencode` 与 `claude-code-reverse`，共同点很明确：

1. 它们都不是单纯回放聊天文本
2. 它们都有结构化 session / memory / compaction 机制
3. 它们会保留“继续工作真正需要的东西”，而不是无脑保留全部原文

本次改造的目标，就是做一个 Python 版的最小可行 `mini-opencode`：

1. 保留现有可见聊天层
2. 新增结构化 `events/memory` 层
3. 固定新的上下文拼装顺序
4. 做轻量压缩，而不是引入新的重系统

## 2. 目标

本次改造只解决一个问题：让模型在多轮工作中，对“读过的材料、读过的网页、写过的文件、形成的结论”有稳定工作记忆。

成功标准：

1. 成功的 `read_material_file`、`read_file`、`fetch_url`、`write_file` 会落成结构化事件
2. 下一轮构造 provider conversation 时，不再只回放聊天文本，而是回放：
   - `system prompt`
   - `compact summary`
   - `recent memory`
   - `recent visible messages`
   - `current turn`
3. 老项目不迁移也能继续工作
4. 前端现有聊天展示与接口协议不需要大改
5. 记忆层损坏或缺失时，系统仍能退化为旧行为而不是直接崩

## 3. 非目标

本次不做：

1. 不把整个会话系统重写成 `opencode` 的 `message parts + db + processor`
2. 不把 `web_search` 结果直接作为长期记忆持久化
3. 不重做前端聊天 UI
4. 不顺手处理“继续扩写被误拦”或“web_search 质量差”这两个独立问题
5. 不引入复杂的向量库、嵌入检索或数据库依赖

## 4. 核心设计决策

### 4.1 保留 `conversation.json`，新增单一 sidecar

现有 `conversation.json` 继续作为前端可见聊天的唯一持久化文件，不改语义。

新增一个 sidecar：

1. `conversation_state.json`

它统一承载三类额外状态：

1. `events`
2. `memory_entries`
3. `compact_state`

不拆成多份 sidecar，不引入数据库。一个文件足够。

### 4.2 只记录“有价值的成功工具事件”

持久化事件范围固定为：

1. `read_material_file`
2. `read_file`
3. `fetch_url`
4. `write_file`

不记录失败事件，不记录纯噪音事件，不把 `web_search` 原始结果纳入长期记忆。

原因很简单：

1. `web_search` 当前质量不稳定，容易把垃圾结果带进长期上下文
2. 真正对后续工作有帮助的，往往是“读了哪篇网页正文、读了哪份材料、写了哪个正式文件”

### 4.3 记忆不是原文堆积，而是“事件 + 提炼”

每次成功读取或写入，不做“把整段内容塞回历史”的蠢事，而是拆成两层：

1. `event`
   - 记录发生了什么
   - 保留来源、定位信息、必要短摘录
2. `memory_entry`
   - 提炼这一事件对后续工作的意义
   - 供下一轮直接注入上下文

这就是这次设计的关键：

1. `event` 是证据骨架
2. `memory_entry` 是工作记忆

### 4.4 固定五层上下文组装顺序

provider conversation 的组装顺序固定为：

1. `system prompt`
2. `compact summary`
3. `recent memory`
4. `recent visible messages`
5. `current turn`

这是硬规则，不允许再退回“系统提示 + 全量聊天历史 + 当前轮”这种薄上下文。

## 5. 数据结构设计

### 5.1 `conversation_state.json`

建议结构：

```json
{
  "version": 1,
  "events": [],
  "memory_entries": [],
  "compact_state": null
}
```

规则：

1. 文件不存在时按空状态处理
2. 文件损坏时自动备份为 `.broken-<timestamp>` 并重建空状态
3. 后续演进靠 `version` 做兼容

### 5.2 `event` 结构

建议字段：

```json
{
  "id": "evt_20260414_001",
  "kind": "read_material",
  "created_at": "2026-04-14T23:00:00",
  "source_ref": {
    "material_id": "mat_xxx",
    "file_path": null,
    "url": null
  },
  "title": "供应链访谈纪要.pdf",
  "summary": "材料包含当前项目的客户背景、渠道结构与三项核心痛点。",
  "excerpt": "客户当前主要依赖省代模式，库存周转慢于行业平均。",
  "metadata": {
    "truncated": false
  }
}
```

说明：

1. `kind` 用更稳定的业务分类，避免调用名耦合太死
2. `summary` 是事件级摘要
3. `excerpt` 只保留必要短摘录，不保留大段原文
4. `source_ref` 只放一个主要来源定位

`kind` 固定枚举：

1. `read_material`
2. `read_file`
3. `fetch_url`
4. `write_file`

### 5.3 `memory_entry` 结构

建议字段：

```json
{
  "id": "mem_20260414_001",
  "created_at": "2026-04-14T23:00:00",
  "category": "evidence",
  "source_key": "material:mat_xxx",
  "source_event_ids": ["evt_20260414_001"],
  "text": "已从供应链访谈纪要确认：客户以省代分销为主，库存周转偏慢，后续大纲需要单列渠道与库存诊断。"
}
```

规则：

1. 一个事件至少可以生成零到一条 `memory_entry`
2. 真正没有新信息的读取，不强行生成记忆
3. `write_file` 生成的记忆重点记录“已经落盘了什么”，防止下一轮忘记已写成果

`category` 最小集合：

1. `evidence`
2. `workspace`

`source_key` 是这次去重和覆写的关键字段：

1. 材料读取：`material:<material_id>`
2. 项目文件读取：`file:<normalized_path>`
3. 网页抓取：`url:<final_url>`
4. 文件写入：`write:<normalized_path>`

这次不搞更多分类和权重字段，够用就行。

默认类别映射也固定下来：

1. `read_material_file` -> `evidence`
2. `fetch_url` -> `evidence`
3. `read_file` -> `workspace`
4. `write_file` -> `workspace`

### 5.4 `compact_state` 结构

建议字段：

```json
{
  "summary_text": "……",
  "covered_visible_message_count": 18,
  "covered_memory_entry_count": 12,
  "last_compacted_at": "2026-04-14T23:10:00"
}
```

说明：

1. 之前的 `conversation_compact_state.json` 合并进 `conversation_state.json`
2. `covered_visible_message_count` 表示前多少条可见聊天已经被摘要覆盖
3. `covered_memory_entry_count` 表示前多少条记忆已经被摘要覆盖

加载 `compact_state` 时必须校验：

1. `covered_visible_message_count <= len(conversation.json)`
2. `covered_memory_entry_count <= len(memory_entries)`

任一条件不成立时，说明 sidecar 与可见聊天已经漂移：

1. 丢弃当前 `compact_state`
2. 保留 `events/memory_entries`
3. 退化回“无 compact summary”模式继续运行

宁可少用摘要，也不要静默丢上下文。

## 6. 事件与记忆生成规则

### 6.1 `read_material_file`

持久化规则：

1. 成功读取时创建 `event`
2. `title` 优先取材料显示名
3. `summary` 使用读取结果的前置信息或首段内容生成短摘要
4. 如果内容很长，只截取短摘录

记忆规则：

1. 仅当读到的新信息足以影响后续分析、计划或写作时，生成 `memory_entry`
2. 同一轮重复读取同一材料，不重复写同义记忆

### 6.2 `read_file`

持久化规则：

1. 成功读取项目文件时创建 `event`
2. `source_ref.file_path` 记录规范化后的相对路径
3. 对正文、计划、分析文件优先生成记忆

记忆规则：

1. 对正式工作流文件，生成“当前文件状态”型记忆
2. 对噪音文件或重复读取，允许只记事件不记记忆

### 6.3 `fetch_url`

持久化规则：

1. 成功抓取并抽取正文时创建 `event`
2. `source_ref.url` 记录最终 URL
3. `title` 优先取网页标题
4. `summary` 记录页面核心信息

记忆规则：

1. 只有抓到了有效正文，才允许生成 `memory_entry`
2. 403、空页面、重定向壳等失败结果不入长期记忆

### 6.4 `write_file`

持久化规则：

1. 成功写入时创建 `event`
2. `source_ref.file_path` 记录写入路径
3. `summary` 记录“写了什么文件、是什么性质的内容”

记忆规则：

1. 正式工作流文件写入后，生成 `workspace` 类记忆
2. 这条记忆用于下一轮提醒模型：哪些成果已经真实落盘

### 6.5 同源记忆的 upsert / 去重规则

这是必须写死的规则，不然后面一定长出一堆过期记忆。

规则：

1. `memory_entries` 不做“只追加不修改”
2. 新记忆生成前，先按 `category + source_key` 查找现存记忆
3. 若已存在同源记忆：
   - 用新内容覆写旧记忆
   - 更新 `created_at`
   - 把新的 `source_event_ids` 替换进去
4. 若不存在同源记忆，再新建一条

这样处理后：

1. 同一路径的 `write_file` 只会保留一条最新“当前状态”
2. 同一 URL / 材料 / 文件的重复读取，不会让上下文越来越脏

## 7. 上下文构建规则

### 7.1 新的 provider conversation 顺序

固定顺序：

1. `system prompt`
2. `compact summary`
3. `recent memory`
4. `recent visible messages`
5. `current turn`

其中：

1. `compact summary` 不是必有项
2. `recent memory` 不是全量记忆，而是“摘要未覆盖的最近记忆”
3. `recent visible messages` 不是全量历史，而是“摘要未覆盖的最近可见聊天”
4. `current turn` 永远单独放最后，不参与历史裁剪

这五层顺序只适用于“每轮初始 provider conversation 组装”。

同一轮内发生工具调用后：

1. 继续沿用现有 `assistant tool_call -> tool_result -> assistant续跑` 机制
2. 不要求把五层重新拼一遍

### 7.1.1 `recent memory` 的注入形式

`recent memory` 必须以单独的一条内部 assistant message 注入，不能散落进普通聊天消息堆。

固定格式：

```text
[工作记忆]
- [evidence] ...
- [workspace] ...
```

规则：

1. 组内按时间正序排列，旧的在前，新的在后
2. 这条 memory block 在 provider conversation 中独立占位
3. 后续预算裁剪时，必须把它当成一个单独 segment 处理，而不是摊平进 recent messages

### 7.2 `recent memory` 的选取

最小策略：

1. 优先取未被 `compact_state` 覆盖的尾部记忆
2. 按时间倒序选最近条目
3. 以 token 预算为上限，而不是固定死条数

初版建议：

1. 先保守取最近 `8-12` 条
2. 超预算时继续截尾

不需要更复杂。实现口径固定为：

1. 先按倒序挑出“最近哪些记忆应该入选”
2. 最终注入 memory block 时，再按正序输出

### 7.3 `recent visible messages` 的选取

规则：

1. 若存在 `compact_state`，跳过已覆盖的前缀消息
2. 只保留尾部最近若干条可见聊天
3. 继续使用现有预算裁剪逻辑兜底

但兜底逻辑必须升级成“分层裁剪”，不能再把所有内容压平后统一压缩。

### 7.4 `current turn` 的处理

当前轮用户输入单独构造成 provider message：

1. 不写回历史裁剪逻辑
2. 不参与 `compact_state.covered_visible_message_count`
3. 当前轮结束后，才会和 assistant 回复一起进入可见聊天持久化

## 8. 压缩与裁剪策略

### 8.1 轻量原则

这次不引入新的 compaction agent 系统，只复用现有总结能力，做最小升级：

1. 压缩对象从“只有可见聊天”扩展为“可见聊天 + 结构化记忆”
2. 压缩产物仍然是一段 `summary_text`
3. 原始大段摘录可以删，但来源与结论要保住

### 8.1.1 分层预算裁剪规则

现有 `_fit_conversation_to_budget` / `_compress_conversation` 的“压平后再压缩”路径，必须改成分层处理。

新规则：

1. 先按五层顺序组装 segment，而不是先组装成一串普通消息
2. `system prompt` 与 `current turn` 不可裁掉
3. `compact summary` 可保留或整体移除，但不能和 `recent memory`、`recent visible messages` 混成一团
4. `recent visible messages` 是第一优先裁剪对象
5. `recent memory` 是第二优先裁剪对象
6. 若还超预算，才允许重新触发更强的摘要化处理

硬约束：

1. 任何预算兜底都不能打乱五层顺序
2. 任何预算兜底都不能把 `recent memory` 摊平成普通 recent messages

### 8.2 压缩后的保留原则

压缩完成后：

1. `compact_state.summary_text` 保留阶段性总结
2. 被覆盖的旧 `memory_entries` 不需要继续完整注入上下文
3. 被覆盖的旧 `events` 可以只保留轻量骨架：
   - `id`
   - `kind`
   - `source_ref`
   - `title`

也就是说：

1. 记忆可以压缩
2. 证据指针不能彻底丢

### 8.3 sidecar 体积控制

`conversation_state.json` 自己也必须受控，不能一边修上下文，一边把 sidecar 养成新的上下文炸弹。

最小控制策略：

1. 最近一段未压缩窗口内的 `events/memory_entries` 保留完整内容
2. 已被 `compact_state` 覆盖的旧 `memory_entries` 可以直接删除
3. 已被覆盖的旧 `events` 只保留骨架字段
4. 若 sidecar 仍明显过大，优先继续裁掉旧 `excerpt`

不要引入更复杂的二级压缩系统。

### 8.4 退化行为

如果压缩失败：

1. 不阻断当前聊天
2. 保留原始 `events/memory_entries`
3. 下一轮继续按未压缩状态工作

## 9. 兼容与迁移

### 9.1 老项目兼容

老项目只有 `conversation.json` 时：

1. 正常加载聊天历史
2. `conversation_state.json` 缺失时按空状态处理
3. 从下一次成功工具调用开始逐步积累结构化记忆

不做一次性历史回填，不值得。

若老项目已经存在 `conversation_compact_state.json`：

1. 首次加载时优先尝试把其中的 `summary_text` 与覆盖计数迁入新的 `conversation_state.json`
2. 迁移成功后删除旧文件
3. 迁移失败时保留旧文件并退化到空 `compact_state`

这能避免把现有已生效的 compact 成果直接弄丢。

### 9.2 清空会话

清空项目会话时，必须同步清理：

1. `conversation.json`
2. `conversation_state.json`
3. 遗留的 `conversation_compact_state.json`

不能再出现“聊天清空了，但旧记忆还在”的脏状态。

### 9.3 文件损坏恢复

`conversation_state.json` 若损坏：

1. 自动改名备份
2. 重建空状态
3. 当前请求继续执行

这类文件绝不能把整个聊天链路拖死。

## 10. 后端实现边界

本次实现应尽量收敛在 `backend/chat.py` 内部完成，必要时只补少量 helper。

建议最小改动面：

1. 新增状态读写与原子保存函数
2. 在工具执行成功后追加事件与记忆
3. 重写 provider conversation 组装逻辑
4. 升级现有 compaction 逻辑以覆盖记忆层
5. 清空会话接口同步删除 sidecar

不应该为了这次需求新建一堆抽象类和模块。

## 11. 测试要求

至少覆盖以下场景：

1. 老项目没有 `conversation_state.json` 时，聊天仍正常
2. 成功 `read_material_file` 后，会写入 `event` 与 `memory_entry`
3. 成功 `fetch_url` 后，下一轮 provider conversation 能看到对应记忆
4. 成功 `write_file` 后，下一轮能知道该文件已落盘
5. 有 `compact_state` 时，上下文组装顺序严格为：
   - `system prompt`
   - `compact summary`
   - `recent memory`
   - `recent visible messages`
   - `current turn`
6. 预算超限时，仍保持五层顺序，只允许按规则裁掉 `recent visible messages` / `recent memory`
7. 同一路径连续多次 `write_file` 后，只保留一条最新 `workspace` 记忆
8. `covered_visible_message_count` 或 `covered_memory_entry_count` 漂移时，会自动丢弃失效 `compact_state`
9. 清空会话时，`main.py` 的清空接口会同时删除新旧 sidecar
10. `conversation_state.json` 损坏时，系统能自愈
11. 流式 `chat_stream()` 与非流式 `chat()` 都覆盖新上下文构建逻辑

## 12. 风险与取舍

### 12.1 风险

最大风险不是“记忆不够强”，而是“把噪音错误地持久化成长期记忆”。

所以这次必须克制：

1. 不记失败事件
2. 不记 `web_search` 噪音
3. 不把大段原文长期留在记忆层

### 12.2 取舍

这套方案不是 `opencode` 的完全复刻，但核心机制对齐了：

1. 有结构化记忆层
2. 有压缩后继续工作的能力
3. 有“最近工作记忆”与“最近可见聊天”的明确分层

它牺牲的是：

1. 没有 `message parts`
2. 没有数据库级 session graph
3. 没有更复杂的 replay / prune 系统

这是刻意的。现在真正要修的是“记忆太薄”，不是“造一个新框架”。
