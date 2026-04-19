# 内置搜索池与末级 Native Fallback 设计稿

## 1. 背景

当前项目的 `web_search` 工具完全依赖单一的 SearXNG JSON API：

1. 后端只调用一个 `managed_search_api_url`。
2. 搜索结果只被拼成纯文本，没有结构化结果、缓存、轮询、熔断或预算管理。
3. 德国机上的现网 SearXNG 已验证存在明显质量和稳定性问题：
   - `baidu` 被 CAPTCHA 挂起
   - `duckduckgo` 被 CAPTCHA 挂起
   - `brave` 频繁触发 `Too many requests`
   - `bing` 对中文查询相关性明显偏差
   - `google` / `wikipedia` / `github` 对很多中文查询几乎无召回

用户已经提供了一组新的可用搜索凭据：

1. `Serper`
2. `Tavily`
3. `Exa`
4. `Brave`

同时用户明确要求：

1. 这些 key 不进入 Git 仓库。
2. 但可以像默认渠道的 `managed_client_token.txt` 一样，作为“内置搜索池”随打包产物注入。
3. 前端不做额度面板。
4. 当所有搜索源都不可用时，提示“搜索额度已用尽”。
5. 允许把 `native provider search` 作为最后一级 fallback，但不能把它做成主路径。

## 2. 目标

本次改造目标是：

1. 保持 `web_search(query)` 工具接口不变。
2. 用一组内置搜索 provider 替代当前单一 SearXNG 主路径。
3. 通过分层、加权轮询、缓存、限流和熔断，让免费额度尽可能撑久。
4. 在四家 provider 都不可用时，再尝试 `native provider search`。
5. 若所有路径都不可用，明确返回“当前内置搜索额度已用尽”。
6. 把搜索池 key 存放到你可携带的私有文件里，而不是仓库里。

## 3. 非目标

本次不做：

1. 不做前端额度面板。
2. 不做手动 provider 切换器。
3. 不做复杂账单系统或精准月度统计。
4. 不做数据库级搜索结果持久化。
5. 不让 `native provider search` 参与正常轮询。
6. 不继续把德国机 SearXNG 当主力搜索源。

## 4. 已确认决策

1. `web_search` 工具名和入参保持不变。
2. 搜索主路径采用内置搜索池。
3. 路由采用 `分层 + 加权轮询 + 熔断冷却 + 缓存 + 限流`。
4. provider 分层如下：
   - 主力层：`Serper`、`Brave`
   - 补充层：`Tavily`、`Exa`
   - 末级 fallback：`native provider search`
5. 四家 provider 的 key 不进仓库，只通过私有文件注入打包产物。
6. 当四家 provider 与 native fallback 都不可用时，返回“当前内置搜索额度已用尽”。

## 5. 总体架构

整体架构保持现有工具入口不变，但将底层搜索实现拆分为三个后端模块：

1. `backend/search_pool.py`
2. `backend/search_providers.py`
3. `backend/search_state.py`

`backend/chat.py` 只保留：

1. `web_search` 工具定义
2. `_web_search()` 作为对 `SearchRouter` 的薄封装

这样可以保证：

1. 现有聊天主流程改动最小。
2. 搜索 provider 逻辑与聊天逻辑解耦。
3. 后续替换或下线某个 provider 不会污染主流程。

## 6. 模块设计

### 6.1 `backend/search_pool.py`

职责：

1. 统一入口 `SearchRouter.search(query, ...)`
2. 负责 provider 分层选择
3. 执行同层加权轮询
4. 判断 provider 是否处于冷却
5. 处理 provider 失败后的 fallback
6. 调用 `native provider search`
7. 统一生成返回结构

它不负责：

1. 具体 HTTP 调用
2. provider 响应解析细节
3. 状态文件读写实现细节

### 6.2 `backend/search_providers.py`

职责：

1. 实现四家 provider 的薄适配器：
   - `SerperProvider`
   - `BraveProvider`
   - `TavilyProvider`
   - `ExaProvider`
2. 将各家响应统一映射为结构化结果
3. 将各家错误统一映射为标准错误类型

统一错误类型至少包括：

1. `rate_limited`
2. `quota_exhausted`
3. `auth_failed`
4. `backend_error`
5. `timeout`
6. `empty_result`

### 6.3 `backend/search_state.py`

职责：

1. 管理搜索运行时状态
2. 管理搜索缓存
3. 管理 query 规范化
4. 读写状态文件与缓存文件

这个模块只处理：

1. `search_runtime_state.json`
2. `search_cache.json`

不直接接触聊天或 provider 业务。

## 7. Provider 分层与路由策略

### 7.1 路由层级

固定分成三层：

1. 主力层
   - `Serper`
   - `Brave`
2. 补充层
   - `Tavily`
   - `Exa`
3. 末级 fallback
   - `native provider search`

最终失败时返回：

1. `当前内置搜索额度已用尽，请稍后再试`

### 7.2 为什么不用“用完一个再切下一个”

这种策略会导致：

1. 最好用的 provider 被最先打爆
2. 更容易触发 `429` 或风控
3. 别家额度长期闲置

因此本次不采用“串行耗尽”。

### 7.3 为什么也不用“完全平均轮询”

因为平均轮询会把较差 provider 强行塞进主路径，直接拉低质量。

因此本次不采用“完全公平轮询”。

### 7.4 采用“质量优先的配额感知加权轮询”

初始权重建议如下：

1. `Serper = 5`
2. `Brave = 3`
3. `Tavily = 1`
4. `Exa = 1`

解释：

1. `Serper` 作为主力，免费量大，中文场景预计最稳。
2. `Brave` 作为主力补位，但其 API 需要卡验证且可能限流。
3. `Tavily` 更适合 agent/search 补位。
4. `Exa` 作为 research/search 补位，不承担主力流量。

### 7.5 冷却与熔断

每家 provider 都维护：

1. `consecutive_failures`
2. `cooldown_until`
3. `last_success_at`
4. `last_error_type`

触发冷却的情况：

1. `429`
2. 明确额度耗尽
3. 超时
4. 连续 5xx
5. 连续多次空结果或后端异常

建议冷却策略：

1. 第一次严重失败：`3 分钟`
2. 第二次连续失败：`10 分钟`
3. 进一步连续失败：`30 分钟`

### 7.6 native fallback 触发条件

只有当以下条件同时满足时，才允许触发 native fallback：

1. 四家 provider 全部不可用
2. 当前激活的 provider/model 明确支持 native search
3. 本轮还未触发过 native fallback

native fallback 只允许：

1. 每轮最多触发一次
2. 不参与正常轮询
3. 不因“结果质量不满意”而触发，只因 provider 全失效而触发

## 8. 缓存与限流

### 8.1 query 规范化

用于缓存 key 的 query 规范化至少包含：

1. 去首尾空白
2. 多空格压缩
3. 全角/半角统一
4. 英文小写化
5. 去掉末尾无意义标点

### 8.2 两层缓存

#### 第一层：进程内短缓存

用途：

1. 挡住最频繁的重复查询

建议：

1. key：规范化 query
2. TTL：`6 小时`

#### 第二层：项目级缓存

用途：

1. 同一项目里重复研究同一主题时复用结果

建议：

1. 存放于 `search_cache.json`
2. TTL：`24 小时`

### 8.3 限流策略

为节省免费额度，增加三道限制：

1. 单轮对话最多 `2` 次搜索
2. 单项目 `5 分钟内最多 10 次`
3. 全局 `1 分钟内最多 20 次`

这样可以避免：

1. 模型一轮里反复乱搜
2. 个别项目异常消耗免费额度
3. 全局瞬时流量打爆某家 provider

## 9. 返回结构

为保持兼容，返回结构采用“兼容扩展”：

保留字段：

1. `status`
2. `results`

新增字段：

1. `provider`
2. `cached`
3. `native_fallback_used`
4. `items`
5. `message`

其中 `items` 每条至少包含：

1. `title`
2. `snippet`
3. `url`
4. `domain`
5. `score`

原则：

1. `results` 继续保留为人类可读文本，保证旧流程兼容
2. `items` 供新逻辑、记忆、`fetch_url` 后续使用

## 10. 密钥文件、状态文件与缓存文件

### 10.1 私有密钥源文件

构建机源文件：

1. `managed_search_pool.json`

建议放在仓库根目录，但：

1. 不进入 Git
2. 由打包机持有
3. 是用户以后真正需要“自己带着走”的文件

### 10.2 打包后的内置文件

打包时将 `managed_search_pool.json` 一起打进产物。

对普通用户表现为：

1. 应用内置搜索池开箱即用

这与当前 `managed_client_token.txt` 的处理方式一致。

### 10.3 运行时状态文件

建议路径：

1. `C:\Users\<用户名>\.consulting-report\search_runtime_state.json`

只存动态状态：

1. provider 冷却
2. 最近失败
3. 软限制计数
4. fallback 状态

### 10.4 搜索缓存文件

建议路径：

1. `C:\Users\<用户名>\.consulting-report\search_cache.json`

只存：

1. query
2. provider
3. 结果
4. TTL

### 10.5 文件边界

边界必须明确：

1. `managed_search_pool.json`
   - 静态
   - 可携带
   - 由用户自己保管
2. `search_runtime_state.json`
   - 动态
   - 丢失可重建
3. `search_cache.json`
   - 动态
   - 丢失可重建

## 11. `managed_search_pool.json` 建议结构

该文件建议包含：

1. `version`
2. `providers`
3. `routing`
4. `limits`

### 11.1 `providers`

至少包含以下 provider 节点：

1. `serper`
2. `brave`
3. `tavily`
4. `exa`

每个 provider 至少包含：

1. `enabled`
2. `api_key`
3. `weight`
4. `minute_limit`
5. `daily_soft_limit`
6. `cooldown_seconds`

### 11.2 `routing`

至少包含：

1. 主力层顺序
2. 补充层顺序
3. 是否启用 native fallback

### 11.3 `limits`

至少包含：

1. 单轮搜索上限
2. 单项目分钟限流
3. 全局分钟限流
4. 缓存 TTL

## 12. 打包与注入

打包逻辑应与当前 `managed_client_token.txt` 一致：

1. 新增对 `managed_search_pool.json` 的 bundle 校验
2. 缺失或空文件时，打包直接失败
3. `build.spec` / `consulting_report.spec` 将该文件一起打包
4. 运行时优先从环境变量覆盖，再读包内文件

读取顺序建议：

1. 环境变量覆盖
2. 包内 `managed_search_pool.json`
3. 若都没有，则判定“未配置内置搜索池”

## 13. 测试策略

### 13.1 Provider Adapter 单测

至少覆盖：

1. 各 provider 成功响应解析
2. `429`
3. 配额耗尽
4. 鉴权失败
5. 超时
6. 空结果

### 13.2 Router 单测

至少覆盖：

1. 主力层加权轮询
2. 冷却跳过
3. 主力层失败后切补充层
4. 四家全失败后走 native fallback
5. native 不支持时返回“额度已用尽”

### 13.3 State / Cache 单测

至少覆盖：

1. query 规范化
2. 缓存命中
3. TTL 过期
4. 状态文件写回

### 13.4 Chat Runtime 回归测试

至少覆盖：

1. `web_search` 工具仍可正常调用
2. 搜索后 `fetch_url` 门禁不变
3. 非搜索聊天流程不受影响

### 13.5 打包相关测试

至少覆盖：

1. 缺失 `managed_search_pool.json` 时打包失败
2. 空白 `managed_search_pool.json` 时打包失败
3. spec 能把它打入 bundle
4. 运行时能从包内读取

## 14. 兼容性要求

必须保证：

1. `web_search` 工具名不变
2. `query` 入参不变
3. `fetch_url` 门禁继续保留
4. 前端无需为本轮改动而修改

不能破坏：

1. 现有对话流
2. 现有工具调用流程
3. 现有 `write gate` / `fetch gate` 逻辑

## 15. 风险与规避

### 15.1 密钥泄露风险

风险：

1. 将 key 写进仓库或显式出现在提交中

规避：

1. `managed_search_pool.json` 不进 Git
2. 打包注入前做本地校验

### 15.2 免费额度快速耗尽

风险：

1. 模型乱搜
2. 同 query 重复搜
3. 某家 provider 被打爆

规避：

1. 分层加权轮询
2. 缓存
3. 单轮、单项目、全局限流
4. 冷却熔断

### 15.3 native fallback 行为不稳定

风险：

1. provider/model 不支持
2. 返回结构不一致

规避：

1. 仅作为末级 fallback
2. 每轮最多触发一次
3. 明确 provider-supported only

## 16. 结论

本次最合适的方案不是继续修德国机的 SearXNG，而是：

1. 以内置搜索池替代单一搜索后端
2. 采用 `Serper / Brave` 主力层、`Tavily / Exa` 补充层
3. 使用“质量优先的配额感知加权轮询”
4. 用缓存、限流、熔断尽量延长免费额度寿命
5. 最后再允许 `native provider search` 作为末级 fallback

这个方案最符合当前项目状态，也最符合奥卡姆剃刀：先把最必要、最可控、最不容易炸的部分做好。
