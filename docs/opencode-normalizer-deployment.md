# opencode SSE 规范化反代 部署说明

`opencode_proxy/` 是 new-api ↔ opencode 之间的薄 SSE 规范化反向代理，部署在 jp-app-01
（与 `managed_proxy`、new-api 同机）。

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

- `usage` + `choices` 非空（opencode 畸形）→ 拆成"去 usage 的正文块" + "标准 `choices:[]` usage 空块"。
- `usage` + `choices` 空（已标准 / opencode 若修回）→ 原样透传（**幂等**，不重复注入）。
- 无 usage 的空块（opencode 私有块）→ 丢弃；`[DONE]` 后内容自然丢弃。
- 请求体 / Authorization 不改动，逐字转发；非流式 / 错误响应逐字透传。

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

4. **接入渠道 61**（改前先备份）：
   `cp /opt/newapi/data/one-api.db /opt/newapi/data/one-api.db.bak-ocnorm-<date>`。
   把渠道 61 的 base_url 由 `https://opencode.ai/zen/go` 改为
   `http://opencode-sse-normalizer:18732`（同网络容器名），并把 ds 分组加回渠道 61。
5. 打一条流式请求走渠道 61，查 new-api 日志确认 `cache_tokens > 0` 且无 `local_count_tokens`。

## 回滚

渠道 61 base_url 改回 `https://opencode.ai/zen/go`（并按需摘回 ds 分组）即恢复原状；
sidecar 容器可留可停。normalizer 幂等，opencode 若日后修回标准格式可从容下线本代理。

## 注意

- 本服务不鉴权，只面向内部（new-api 同网络）——**绝不对公网暴露**。opencode key 由 new-api
  每请求经 Authorization 透传，本服务不落盘不缓存。
- 回归测试：`tests/test_opencode_normalizer.py`（normalizer 纯函数 + app 流式规范化/透传/错误/鉴权头）。
