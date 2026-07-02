# opencode SSE 规范化反代 部署说明

`opencode_proxy/` 是 new-api ↔ opencode 之间的薄 SSE 规范化反向代理，部署在 jp-app-01
（与 `managed_proxy`、new-api 同机）。

> **状态：✅ 2026-07-03 已上线 jp-app-01。** 容器 `opencode-sse-normalizer`（`/opt/opencode-sse-normalizer`，
> compose，`newapi_default` 网络，`restart: unless-stopped`）；new-api 渠道 61 base_url→
> `http://opencode-sse-normalizer:18732`、group→`default,ds`；DB 备份 `one-api.db.bak-ocnorm-20260703`。
> 端到端门禁全过：ds专用 token 经**薄网关**全链实测 8/8 响应带 `prompt_cache_hit_tokens>0`，
> new-api 渠道 61 `local_count=0`、cache>0（修复前 90/90 local_count、cache=0）。下面步骤为部署记录/复现用。

## 为什么需要它

opencode.ai/zen 在 **2026-07-01→07-02** 间把流式响应改成了非标准形态：把 `usage`
挂在带 `finish_reason` 的正文块（`choices` 非空）上，而不是 OpenAI 规范要求的"单独一个
`choices:[]` 空块"；其真正的空块里装的是私有字段（`x-opencode-type=inference-cost`），
且 `data: [DONE]` 之后还多发一块。new-api 的流式取 usage 逻辑只从"空 choices 的末块"
里找 usage → 抓不到 → 回退本地估 token（`local_count_tokens=true`）→ cache 归 0 →
下游 CRA 按最贵的"未命中档"（deepseek-v4-pro 3.0 元/百万，比命中价贵 120 倍）计费。

new-api 侧无 bug（同一实例对官渠 57、以及 **07-01 的 opencode 流量**都能正确读 cache）；
纯上游格式回归。相关上游/网关 issue：new-api #3309、#3389，opencode #24189。

## 排查与验证证据（2026-07-02，均只读 / 未接生产）

- new-api 日志：ch61 在 07-01 15:00–16:00 有 34 条流式请求 cache 正确入账（`local_count=0`）；
  07-02 起 90 条全部 `local_count=1`、cache=0。**变盘点在 opencode，不在 new-api（容器 11 天未重启）。**
- 直接打 opencode：物理缓存真实存在（同一长 prompt 第 2 次 `prompt_cache_hit_tokens=896/990`），
  usage 字段完整，只是流式里挂错了块。
- normalizer 实机验证：把 opencode 畸形流还原后 usage 落到 `choices:[]` 空块、缓存命中数如实保留。

## 规范化逻辑（`normalizer.py`）

- **字节级 SSE 组帧**（`_SseEventFramer`）：从原始字节按 SSE 规范组帧，只按 `\r`/`\n`/`\r\n` 切行、
  空行分事件——` ` 等 Unicode 行边界字符留在正文里不触发切分（httpx/requests 的 `iter_lines`
  会误切、断开正文 JSON，故必须自建）。非法 UTF-8 / 单事件超上限 → corrupt fail-closed。
- **usage 候选**：只有**终态块**（choices 空、或非空但每个 choice 带 finish_reason）上的 usage 作候选；
  正文增量块上的 usage 快照被**剥离但不作候选**。多个终态 usage 取**最后一个**（含 `null/{}`，会清掉更早
  候选）；正文块的 usage 一律剥除后透传。
- **可计费门槛（贴合 CRA metering 语义，防少计费）**：prompt/completion 为有限非负整数；hit/miss ≤ prompt；
  **miss 存在时 hit 必在且 `hit+miss==prompt`**；嵌套 `prompt_tokens_details.cached_tokens` 须一致。
  满足才在收到 `[DONE]` 后作为**唯一** `choices:[]` 空块发出并补 `[DONE]`。
- **fail-closed**：上游截断（未见 `[DONE]`）、畸形事件、或不可计费 usage → **不发 usage、不补 `[DONE]`**
  （下游走无 usage / 本地估算 / 全 miss 保守路径，绝不促成少计费）。
- opencode 私有块（含 `cost` 且键 ⊆ `{choices,cost,normalizedUsage,x-opencode-type}`）→ 丢弃；
  `[DONE]` 后内容忽略；**未知 / `{"error":...}` 对象 → 透传**（绝不静默吞成功）。
- 输出帧 `ensure_ascii=True`：不把行边界字符传给下游 new-api。请求体 / Authorization 逐字转发、不跟随
  重定向、忽略环境代理；非流式与 4xx/5xx（含 SSE 错误体）响应逐字透传。

## 部署（会动生产；已由 Codex 双轨审 APPROVED 后执行）

1. 传 `opencode_proxy/` 到服务器，如 `/opt/opencode-sse-normalizer/`。
2. 确认 new-api 容器所在 docker 网络：
   `docker inspect new-api -f '{{json .NetworkSettings.Networks}}'`。
3. 用如下 compose（把 `<newapi_network>` 换成上一步的网络名）起服务：

   ```yaml
   services:
     opencode-sse-normalizer:
       build: .
       image: opencode-sse-normalizer:latest
       container_name: opencode-sse-normalizer
       restart: unless-stopped
       networks: [<newapi_network>]
       environment:
         OPENCODE_UPSTREAM_BASE_URL: "https://opencode.ai/zen/go"
         OPENCODE_NORMALIZER_HOST: "0.0.0.0"
         OPENCODE_NORMALIZER_PORT: "18732"
   networks:
     <newapi_network>:
       external: true
   ```
   `docker compose up -d --build`。

4. **接入渠道 61**（改前先做 WAL 安全备份）：new-api 的 SQLite 开着 WAL，`cp` 单文件
   拿不到完整状态。用在线一致快照：
   `sqlite3 /opt/newapi/data/one-api.db ".backup '/opt/newapi/data/one-api.db.bak-ocnorm-<date>'"`
   （或停 new-api 容器后连 `one-api.db`/`-wal`/`-shm` 一起复制）。
   然后把渠道 61 的 base_url 由 `https://opencode.ai/zen/go` 改为
   `http://opencode-sse-normalizer:18732`（同网络容器名），并把 ds 分组加回渠道 61。
5. **上线门禁（不是普通验证，不过则回滚）**：打一条走渠道 61 的**流式** managed 请求，端到端确认：
   - new-api 日志该条 `local_count_tokens` 不为真、`cache_tokens > 0`；
   - 二次相同前缀请求缓存命中上升；
   - CRA 侧 `usage_daily.cache_hit_tokens` 按预期增长（而非全进 miss）。
   任一不满足即视为"new-api 未把还原后的空块解析为真实 cache"，立即按下方回滚，勿放量。

## 回滚

渠道 61 base_url 改回 `https://opencode.ai/zen/go`（并按需摘回 ds 分组）即恢复原状；
sidecar 容器可留可停。normalizer 幂等，opencode 若日后修回标准格式可从容下线本代理。

## 注意

- 本服务不鉴权，只面向内部（new-api 同网络）——**绝不对公网暴露**。opencode key 由 new-api
  每请求经 Authorization 透传，本服务不落盘不缓存。
- 回归测试：`tests/test_opencode_normalizer.py`（normalizer 纯函数 + app 流式规范化/透传/错误/鉴权头）。
