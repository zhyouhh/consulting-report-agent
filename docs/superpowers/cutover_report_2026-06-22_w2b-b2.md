# Cutover Report — W2-B / B2 中央计费 + per-user 配额

- 日期：2026-06-22
- 分支：`feat/w2b-b2-billing`（基于 main `373cb28`，B1 已 merge）
- plan：`docs/superpowers/plans/2026-06-22-w2b-b2-central-billing-quota.md`（14 task，Codex 5 轮 APPROVED）
- spec：`docs/superpowers/specs/2026-06-21-w2b-multi-tenant-core-design.md`（§5.4 / §6 / §7 me / §10 / §13 B2 验收门）

## 一句话

所有 managed LLM/视觉调用经单一 `MeteredManagedClient` 出口，按 deepseek 缓存三档精确计价、整数微元累计进 SQLite `usage_daily`、以 per-user ¥5/天金额配额做门禁（reserve→settle、usage 缺失 fail-closed）；`/api/auth/me` 透出今日花费/上限，前端账号块显示额度。

## 验收证据

- 后端 `.venv/bin/python -m pytest tests/` → **1342 passed, 1 skipped, 4 failed**。4 个 failed 全是已知 **mac-realpath 环境差异**（`test_skill_engine.py` / `test_workspace_materials.py` 的 `/var`→`/private/var` symlink 路径比对，Windows 上绿，非 B2 引入 —— 已对其中一例确认 traceback 为 `'/private/var/...' is not in the subpath of '/var/...'`）。
- 前端 `node --test tests/` → **342 passed**；`npm run build` → 绿。
- 定向回归 `pytest test_metering test_chat_runtime test_independent_review test_tenant_isolation test_main_api test_auth_api test_accounts` → **774 passed**（计费覆盖面 + 跨租户隔离 + DeepSeek 兼容全绿）。
- DeepSeek 官渠兼容回归 `-k "deepseek or tool_call or reasoning"` → 绿；`test_deepseek_compat_helpers_match_chat_helpers`（含流式 follow-up）→ 绿。

## 22 commit 一览（5 实施簇 + 收尾，每簇双轨 Codex review）

- **簇A metering 叶子模块（Task 1–7）**：`0a23e3e` 单价常量+`price_micro_yuan` · `7c8be50` `extract_billing_usage` · `12acd7f` `usage_daily` 表+`today_shanghai` · `bcc248f` 非流式 reserve/settle/fail-closed · `df93e3a` 流式透传+末包结算 · `556be93` 连续缺失暂停 · `ec7c758` `wrap_client_for_billing` 工厂+source-guard · 红队修复 `d0bfc30`/`539dd5c`/`8929362`（usage 防御性 fail-closed）。
- **簇B 接线 ChatHandler（Task 8–9）**：`6518e4a` 包 client+友好配额事件 · `860f30f` 压缩/视觉计费实证+DeepSeek 绿 · `ef23e18` 行为 close+settle 测试+真 reserve 集成测试+close 失败记日志。
- **簇C 接线 IndependentReviewAgent（Task 10）**：`7208b0f` uid+包 client+include_usage · `f0eef2b` 精确计账+端点 uid 回归守卫。
- **簇D main.py 端点（Task 11–12）**：`82c60d9` /api/chat 预检 · `eb5bbc3` /api/auth/me cost · `0dbe3b0` 预检 gate-on-managed 重排+不建 handler 断言。
- **簇E 前端额度（Task 13）**：`6116987` Sidebar 显额度 · `03a94d1` quotaRatio 非 finite 防护 · `004b4d3` local 也显额度（与登出门解耦）。
- **收尾（Task 14）**：`0a4b9be` 模块限定 metering 引用（reload 安全）+ 本报告 + 文档同步。

## 关键设计 / 对 plan 的偏离（实施期 Codex 双轨 review 驱动）

- **被动计费 wrapper**：managed 模式把 `ChatHandler.self.client` / `IndependentReviewAgent._build_client()` 整体换成 `metering.MeteredManagedClient`（镜像 `.chat.completions.create`），5 个调用点（主流式/sync/压缩/视觉/审查）调用语法零改动；custom 走裸 client。wrapper **不注入** `include_usage`，由调用点自报（managed-only），保留 chat.py 既有 include_usage 失败回退逻辑。
- **`finally` 同步结算 + `response.close()`**：流式 wrapper 在 `finally` 恰好结算一次（自然结束/provider 异常/GeneratorExit 一律 fail-closed）；chat.py 与 independent_review.py 在消费 `for chunk in response:` 后 `finally: response.close()`，避免提前 break/return 把结算延到 GC（致下次 reserve 在结算前发生、少算）。
- **fail-closed**：usage 缺失/畸形 → 按该模型上下文上限 × p_miss 保守封顶 + per-(uid,model,**day**) 缺失计数 +1，连续 3 次 → `ModelPausedError`；视觉模型 `MANAGED_FAILCLOSED_CEILING`（Qwen3-VL=32768）显式锚，其余回落 `resolve_context_policy(model).effective_context_limit`（deepseek=256k）。**usage 防御性净化**（簇A 红队 4 轮）：present-but-malformed（非数值/bool/inf/nan/负/超 `_MAX_PLAUSIBLE_TOKENS`=1e9）一律 → None → fail-closed，**不静默归零**（归零会假记 0 费用并复位缺失计数、绕过暂停保护）。
- **日界 Asia/Shanghai**：`today_shanghai()` UTC+8 固定偏移，新日新行 = 自动重置；day 入缺失计数键 → 暂停次日自动清零（避「暂停后 reserve 先拦致永不清零」）。
- **cap 解析优先级**：user override（`users.daily_cost_micro_yuan`）→ 全局 `app_config.global_daily_cap_micro_yuan` → 默认 5_000_000 微元（¥5）。
- **模块限定 metering 引用（收尾修，`0a4b9be`）**：chat.py / independent_review.py 用 `from . import metering` + `metering.QuotaExceededError`（而非 `from .metering import` 拷贝名）。根因：`importlib.reload(metering)` 在同一模块对象内重建异常类，拷贝名变陈旧、与 wrapper 实抛的活类 isinstance 失配 → 配额异常漏成 "API调用失败"。生产不 reload、不触发，但模块限定访问使测试套件顺序无关。

## 验收门对账（spec §13 B2）

| 验收项 | 实现 | 测试 |
|---|---|---|
| 三档精确计价（命中0.025/未命中3/输出6 元每百万token） | `price_micro_yuan` | `test_metering.py::PriceTests`（精确数值 6867/3059/3000） |
| 中央 client 唯一出口 + 覆盖 chat/压缩/视觉/审查 | wrapper + 5 调用点 + source-guard | `SourceGuardTests`（两条真测）、`B2BillingSettleTests`（压缩/视觉真 settle）、`B2ReviewBillingTests`（审查 run() 精确计账） |
| 流式 include_usage（仅 managed） | managed-only gate | `test_managed_stream_requests_include_usage` / `test_custom_stream_omits_include_usage` |
| reserve/settle/fail-closed/连续3缺失暂停/跨日 | `_reserve`/`_settle`/`_miss_counter` | `MeteredNonStream/Stream/MissCounterTests`、`B2BillingWiringTests` 真 reserve 集成 |
| **中断都计** | 流式 `finally` + GeneratorExit fail-closed | `test_stream_interrupt_before_usage_fail_closed` / `test_stream_provider_error_midstream_fail_closed` |
| usage_daily 整数微元原子累加 | `ON CONFLICT DO UPDATE` | `test_accounts.py::UsageDailyTests` |
| me 返回 today_cost/daily_cap | `/api/auth/me` 两分支 `**cost_fields` | `test_auth_api.py::MeCostFieldsTests` |
| custom 不计费不门禁 | 工厂 mode 分支 + 端点 gate-on-managed | `WrapFactoryTests` / `test_custom_client_is_raw`（**单元级**：见下「已知限制」） |

## 已知限制（本期接受，多数 B3 处理）

1. **软帽（soft cap）**：`reserve` 只做 `used >= cap` 读检查、非原子预留（spec §11 接受）；同 uid 并发至多略超一轮。¥5/天单账号桌面源/小型 web 单进程部署下不引入原子预留账本。
2. **从未消费的流不结算**：`create()` 返回的流若从不被迭代则不结算（reserve 已跑挡下次）；真实调用点一律 `for chunk in response:` 至少进入一次，不存在该生产路径。
3. **settle 失败 @ app-initiated break**：流式提前 break 经 `response.close()`→GeneratorExit 触发 settle；若此刻 settle（SQLite 写）抛错，只 `logger.warning` 记录、不遮蔽 GeneratorExit、该流漏计一次（极罕见，favors「不因 DB 抖动崩聊天轮」）。
4. **`.responses` 透传不计费**：wrapper `__getattr__` 透传非 `.chat.completions.create` 调用面（如原生搜索 `self.client.responses.create`）；仅 custom-OpenAI（`api.openai.com`+`gpt-*`）才走该路径，**managed 模式不走** → B2 无实际计费缺口；B3 custom 真激活时处理。
5. **单进程 miss-counter**：`_miss_counter` 进程级 + day 入键（单进程部署成立；多 worker 需迁 DB，B3/部署期）。
6. **custom 生产不可达（managed-forced）**：`config.normalize_settings_payload` 在 load/save 路径无条件 `mode="managed"`，故 B1/B2 production custom 不可达。本期 custom 分支与其单元测试是**对 wrapper 工厂逻辑的单元验证 + B3 custom 激活预留**，非 production 验收。
7. **桌面 local 受 ¥5/天默认 cap**：桌面 uid="local" 经 managed 计费、`get_effective_daily_cap_micro("local")` 回落默认 ¥5/天、**会被 reserve 拦到「额度已用尽」**。前端已对 local 显示额度（被拦时不致一头雾水）。**⚠️ 待用户决策**：若不希望桌面同事被 ¥5/天限制，需单独配置（给 local 设高 cap 或豁免 local metering）——属配置/产品决策，非本期代码改动。
8. **/api/auth/me 多次查 DB**：每次 me 查 usage+cap+user；me 在加载/登录时调、非高频轮询，合并查询属过早优化（B3 若高频再优化）。
9. **review 无 include_usage 拒绝回退**：independent_review 不像 chat 有 `_should_retry_stream_without_usage` 回退；jp-app-01 实测 deepseek 官渠**接受** `stream_options.include_usage`，managed 支持已验证；B3 custom 真开放时再补回退。
10. **`int(float)` 截断**：`extract_billing_usage` 接受 int/float，小数 float 经 `int()` 截断（亚 token 级、provider 返回整数，可忽略）。

## 未做（B3 scope-out）

- admin 调 cap / 轮换全局 cap 的端点（本期只读 `users.daily_cost_micro_yuan` + `app_config.global_daily_cap_micro_yuan`）。
- custom 模式真激活（+SSRF 硬化）+ per-uid custom-bypass「不触碰 accounts/meters」回归测试。
- 原子 reserve 账本（若产品改判需硬配额）。
- CSRF/SSRF/CORS 硬化、`must_change_password` 路由强制、per-username 限流、admin 面板。

## 红队 review 实证价值（被 Codex 双轨挖出并修复的真问题摘录）

- **簇A**（quality 轨 4 轮）：负/falsey 非数值/inf/超大 usage 会绕过暂停计数（假记 0+复位）；流式 settle 抛错遮蔽 provider/GeneratorExit 异常 + 底层流不关闭。→ 统一 `_coerce_token` fail-closed 契约 + 嵌套 finally + `sys.exc_info()` 保留在途异常。
- **簇B**：`response.close()` silent pass → 改 `logger.warning`；加真 reserve 路径集成测试（原测试 patch create 绕过了真 reserve）。
- **簇C**：billing 集成测试断言收紧为精确计账（cache_miss==50/output==25/create×5）；加端点 uid 回归守卫。
- **簇D**：custom bypass 先读 DB 再判 mode → 重排为先判 mode（custom 不被坏 cap/DB 异常波及）；预检测试加 `get_chat_handler.assert_not_called()`。
- **簇E**：`quotaRatio(NaN,5)` 返 NaN 违反 clamp 契约 → `Number.isFinite` 归一；**local 看不到额度**（额度行被 `uid!=='local'` 整块吃掉，而 local 真受 cap）→ 与登出门解耦。
- **收尾（cluster F 全量回归）**：跨文件 `importlib.reload(metering)` 致 wrapper 抛活类、except 持陈旧拷贝类 → isinstance 失配、配额异常漏成 generic error。→ 模块限定 metering 引用 + 确定性 reload 回归守卫。**per-cluster 隔离跑照不出，收尾全量回归才暴露。**

详见 plan 的 Self-Review 段（Codex R1–R4 修复对账）。
