# Cutover Report — W2-B / B3 Admin 面板 + 安全硬化 + custom 模式激活

- 日期：2026-06-23
- 分支：`feat/w2b-b3-admin-security-hardening`（基于 main，B1 + B2 已 merge），HEAD `a1b64e8`
- plan：`docs/superpowers/plans/2026-06-22-w2b-b3-admin-security-hardening.md`（19 task / 6 Phase，Codex plan 4 轮 APPROVED）
- spec：`docs/superpowers/specs/2026-06-21-w2b-multi-tenant-core-design.md`（§7 admin / §8 CSRF/SSRF/CORS / §13 B3 验收门）

## 一句话

给多租户 Web 基座补上后台管理面板（用户列表 / 改密 / 调 cap / 禁用 / 邀请码轮换 / custom 允许域名维护）、CSRF Origin/Referer 中间件 + CORS 收紧、SSRF 域名白名单护栏（叶子模块 `url_guard.py`）、throttle-first per-username 登录限流、`must_change_password` 路由级强制；并**真正激活 custom 模式**（用户自带 OpenAI 兼容 key/base，走 guarded http client，custom 不计入 managed 配额）。

## 验收证据（全量回归 + 安全验收门）

- 后端 `.venv/bin/python -m pytest tests/ -q` → **1419 passed, 4 failed, 1 skipped, 3 deselected**。4 个 failed 全是已知 **mac-realpath 环境差异**（`test_skill_engine.py::test_create_project_defaults_to_managed_workspace_under_projects_root` / `::test_primary_report_path_uses_content_report_draft_v1_only`、`test_workspace_materials.py::test_create_project_stores_workspace_metadata_and_initial_materials` / `::test_import_material_copies_external_file_into_project`），`/var`→`/private/var` symlink 路径比对，Windows 上绿、与 B3 无关。
- 前端 `cd frontend && node --test tests/` → **357 passed, 0 fail**；`npm run build` → 绿（仅 pre-existing chunk-size advisory，非错误）。
- DeepSeek 官渠兼容回归 `-k "deepseek or tool_call or reasoning"` → **18 passed**；`-k compat_helpers_match`（含流式 follow-up 锁定）→ **1 passed**。B3 全程只加 system prompt 文本 / 中间件 / 依赖 / 传输层，不碰 provider message/tool-call/`reasoning_content`/`tool_choice` 序列化，无回归。
- 跨租户隔离：`test_tenant_isolation.py`（复合键/搜索隔离）+ `test_main_api.py::CrossTenantApiTests`（A 用户访问 B 项目 404，按 id 和按名称都不泄漏）→ **2 passed**；CSRF/admin/must_change 引入未破坏 B1 复合键归属。

### spec §13 B3 验收门 5 项 → 测试映射 + pass 证据

| 验收项 | 实现 | 证据测试（pass 数） |
|---|---|---|
| **CSRF**：跨站 Origin 的 POST（含 admin）被 403；同源 / GET 不拦；缺 Origin 退 Referer | `csrf_origin_guard` 中间件（main.py:99） | `tests/test_csrf.py` → **9 passed**（含跨站 403 / 同源放行 / GET 不检 / SSE 同源 / 缺 Origin fresh-client 403） |
| **SSRF**：custom_api_base 私网/loopback/metadata/非 https/非白名单/userinfo/坏端口 → 拒；models/list 同 | `url_guard.validate_custom_api_base` + `_GuardedHTTPTransport` + `assert_public_ip` | `tests/test_url_guard.py` → **17 passed**；`tests/test_models.py -k offlist/guard/base` → **2 passed**；`tests/test_settings_api.py -k custom/offlist/managed_base` → **11 passed** |
| **越权**：非 admin 访问 `/api/admin/*` → 403；A 用户访问 B 项目 → 404 | `Depends(get_current_admin)`（8 端点）+ `require_project` canonical 归属 | `tests/test_admin_api.py` → **16 passed**（含 requires_admin 403）；`test_main_api.py::CrossTenantApiTests` → **2 passed** |
| **custom 激活**：mode=custom + 白名单 https base → 保存成功 + **持久化跨 reload 存活**；legacy 仍强制 managed | `normalize_settings_payload` honor mode（非 legacy）+ `save_settings` 不剔除 `mode` | `tests/test_config.py -k custom/mode/legacy` → **17 passed**；`tests/test_settings_api.py` 上述 11；`test_config.py` 全 **29 passed** |
| **must_change_password**：标志为真时业务/admin 路由 403、me/change-password/logout 可用、改密后解除 | `require_password_current`（web 态 403 / 桌面短路）三层覆盖 | `tests/test_auth_api.py -k must_change` → **3 passed**；`-k throttle` → **7 passed**；`test_auth_api.py` 全 **43 passed** |

逐文件汇总：`test_csrf` 9 / `test_url_guard` 17 / `test_admin_api` 16 / `test_settings_api` 15 / `test_config` 29 / `test_models` 17 / `test_accounts` 28（含 admin 函数 + UsageDaily）/ `test_auth_api` 43 / `test_tenant_isolation` 9。

## 交付清单（5 实施 Phase）

- **Phase 1 — SSRF 护栏 + custom 激活（后端）** commits `eb60eb8` `aa9cd23` `a76f96e` `520a91f` `faf690a` `f3cf2d8` `8de17ae`：
  - 新叶子模块 `backend/url_guard.py`（只 httpx + stdlib，绝不 import chat/skill/main/config/accounts）：`assert_public_ip`（私网/loopback/link-local/multicast/reserved/CGNAT/metadata 拒）+ **三层域名白名单**（`builtin_allowed_hosts` ∪ `env_allowed_hosts`(`CRA_CUSTOM_API_ALLOWED_HOSTS`) ∪ `_RUNTIME_ALLOWED_HOSTS`(app_config，admin 面板增删))+ `validate_custom_api_base`（https + 白名单 + 解析公网 + 拒 userinfo/坏端口）+ `_GuardedHTTPTransport`/`build_guarded_http_client`（`trust_env=False` / `follow_redirects=False`）。
  - `managed_base_url` 改服务端只读（`normalize_settings_payload` 强制回 `DEFAULT_MANAGED_BASE_URL` + 不持久化 + `SettingsUpdate` 字段改 Optional-ignore）。
  - custom 在 `normalize_settings_payload` 解锁（非 legacy honor mode；`config_version < DESKTOP_CONFIG_VERSION` 的 legacy 仍强制 managed 迁移安全）；保存 custom 时端点用 `validate_custom_api_base` 即时校验（400）。
  - chat.py / independent_review.py / `/api/models/list` 三处 OpenAI client 统一走 `build_guarded_http_client`；chat.py `_ensure_public_ip` 委派 url_guard（DRY）。

- **Phase 2 — CSRF / CORS / cookie / 登录限流** commits `92c0cc0` `9d6097e` `c2f80aa` `f28a044` `b86f678`：
  - `csrf_origin_guard` 中间件：web 态（`auth_required`）对 POST/PUT/PATCH/DELETE 校验 Origin（缺失退 Referer）∈ allowlist，不匹配 403；桌面 loopback（`auth_required=False`）跳过；生产（auth + cookie_secure）不信任 loopback 源（CSRF 层运行时收紧 `include_loopback=not is_production`，CORS 维持 import 期 `list(allowed_origins())` 快照）。
  - CORS 从 `allow_origins=["*"]` 收紧到 `list(allowed_origins())`（loopback ∪ `CRA_ALLOWED_ORIGIN`）；web 态 `cookie_secure=True`（`CRA_COOKIE_INSECURE` 可本地 http 调试豁免）。
  - per-username 登录限流（最终 throttle-first，见下）。

- **Phase 3 — accounts admin 函数 + admin API** commits `2be68fd` `9ff080a` `2825111` `768b21e` `9a2c993` `85360ff`：
  - accounts 新增 `list_all_users`（剥 password_hash）/ `admin_reset_password`（改 hash + `must_change_password=1` + 撤销全部会话，同一事务）/ `rotate_invite_code` / `get/set_custom_api_extra_hosts`（app_config）/ `admin_set_user_disabled`（**BEGIN IMMEDIATE 原子守卫**，活跃 admin 绝不归零，禁最后一个活跃 admin → `LastAdminError`）。
  - 8 个 `/api/admin/*` 端点全 `Depends(get_current_admin)`：GET users（带今日花费/有效 cap）/ invite-code / allowed-hosts（builtin/env/extra 三类）；POST users/{uid}/password / cap / disabled、invite-code/rotate、allowed-hosts（即时刷新 `set_runtime_allowed_hosts`，无需重启）。
  - 启动加载：`init_db` 后把 app_config 白名单（过滤非法历史项 `is_valid_hostname`）注入 `url_guard.set_runtime_allowed_hosts`。

- **Phase 4 — must_change_password 路由级强制** commits `69cafda` `99a53e0` `52e2966`：
  - `require_password_current`（web 态 must_change_password → 403、桌面短路）；三层覆盖：`require_project` 默认依赖（所有 path-param `{project_id}` 端点）+ `get_current_admin` 入参（所有 `/api/admin/*`）+ **显式 8 端点**（settings GET/POST、models/list、projects GET/POST、chat、chat/stream、桌面桥 select-workspace-folder/files）；豁免集精确 {me, change-password, logout, health}。

- **Phase 5 — 前端** commits `9563a2c` `16c8a78` `e08574d` `40e3bbf` `a1b64e8`：
  - `utils/adminApi.js`（`capPayload` 送字符串对齐后端 `AdminCapBody: str|None` / `validateNewPassword` / `summarizeUser`）；`AdminPanel.jsx`（用户表 + 改密/cap/禁用/邀请码轮换 + 允许域名 textarea）+ Sidebar `is_admin` 入口；`ForcePasswordChange.jsx` + App.jsx gating；SettingsModal 去 `managed_base_url` + custom UI 确认；ChatPanel / IndependentReviewDrawer raw fetch 补 `credentials: 'include'`。

## 红队双轨修复叙事（逐 Phase，Codex spec + quality 独立轨）

> 体例：每 Phase 实施后走 Codex 双轨独立 review，「审 → 修 → 再审」直到 APPROVED；编排者（我）对几处 plan 盲点 / agent 提交前断点做了核实后的人工补完。

**Phase 1（SSRF + custom 激活）**：
- **编排者发现的 plan 盲点（`f3cf2d8`）**：`save_settings` 历史把 `mode` 当运行时派生字段剔除 → custom 选择存盘即丢、下一个请求 load 回落 managed → **custom 从未端到端生效**。一个活不过一个请求的「激活」不算激活，而 B3 核心是「custom 是主路径」。核实后授权补 `mode` 持久化（从剔除清单移除）。
- **Codex 双轨 NIT 修复（`8de17ae`）**：`validate_custom_api_base` 拒 userinfo（防 httpx 注入 `Authorization: Basic` 覆盖用户 Bearer key → 静默坏掉 custom 调用）/ 拒坏端口；`managed_base_url` 不落盘。
- 实施者还揪出并修正了 NIT 测试的 false-green：reject 测试初版靠沙箱 DNS 拦截碰巧通过，改为强 mock `socket.getaddrinfo` + 断言拒绝原因。

**Phase 2（CSRF/CORS/登录限流）**：
- CSRF 中间件上线后既有大量不带 Origin 的 POST 测试会全 403 → 测试基类 TestClient 设默认 Origin、夹具迁移；缺-Origin 用例用 fresh `TestClient`（httpx 合并默认头不删）。
- **登录限流经历 verify-first → throttle-first 的反复**：实施者先按 plan 做 per-username 限流；quality 红队挖出 4 BLOCKER（check-then-record 竞态、账户锁定 DoS、限流 key casefold 与大小写敏感账号查找不匹配、store 无界 O(N)）。编排者一度判断改 verify-first（正确密码不被锁，`f28a044`）；红队复验指出 **verify-first 架空撞库防护**（密码仍被验、猜对即登入、限流只改错误码）。**最终用户拍板：throttle-first（`b86f678`）**——撞库防护真实有效优先，接受被攻击时该用户 5min 临时锁定（自动恢复，业界标准）。最终态 `_reserve_login_attempt`：reserve-before-verify、单锁原子（prune-this-key + 判上限 + append）、精确 username key（对齐大小写敏感账号查找）、有界 store（`deque(maxlen=_LOGIN_MAX_FAILS)` + `_MAX_TRACKED_LOGIN_KEYS=4096` + 增量 prune）；桶满直接 429 不验密 = 真封顶撞库。双轨 APPROVED。

**Phase 3（admin 函数 + API）**：
- quality 红队 2 BLOCKER：① **防锁死 TOCTOU**——main.py 层 count 检查与 `set_user_disabled` 分两事务、并发互禁可致 0 admin → 修为 accounts 层 `admin_set_user_disabled` 的单个 `BEGIN IMMEDIATE` 写事务原子守卫（第二个请求重读看到只剩 1 活跃 admin → `LastAdminError`）；② **cap 解析崩 500**（`1e1000000`/`1e308` 过 `is_finite` 但乘法/SQLite 绑定溢出）→ 限长 + ¥1e6 上限 + guard → 400 不 500。NIT：白名单 TLD label 限 63 字符。
- **编排者核实后的人工补完（`85360ff`）**：实施者 agent 提交前 API 超时——accounts 原子函数 + 测试已写，但 disable 端点还没接上原子函数（仍 racy）。核实后手工补完端点接线（删 racy 计数检查、改调 `admin_set_user_disabled` + catch `LastAdminError`），跑全量绿后提交。双轨 APPROVED。

**Phase 4（must_change_password 强制）**：
- R1-BLOCKER3：body-project 路由（chat / chat_stream / models_list）是「先 `Depends(get_current_uid)` 再手动调 `require_project`」，改 `require_project` 默认依赖**覆盖不到**它们 → 必须显式逐路由串 `require_password_current`。NIT（`99a53e0`）：桌面桥 2 端点（select-workspace-folder/files）也收紧到精确豁免集。双轨 + agent 自发红队 exhaustive grep（真 grep 确认零遗漏 + 死锁端到端走通 + 启动态无反转）APPROVED。

**Phase 5（前端）**：
- 双轨 APPROVED；NIT 修复（`a1b64e8`）：`initializeApp` gate 在 `!must_change_password`（避免登录后闪 403 错误弹窗）、AdminPanel 挂载加 `is_admin` 兜底、credentials 测试强化。

## 已知限制（本期接受）

1. **DNS rebinding TOCTOU 未彻底防**：白名单 = 安全边界（只有 admin 批准的主机能被连接）+ 请求时 public-IP 校验 = 第二道防线（拦白名单主机被误配/投毒到私网），但 `_GuardedHTTPTransport` **未在连接层 pin IP**，对「攻击者控制白名单内域名 + 解析后到连接前翻转到私网」的 rebinding 仍有 TOCTOU。pinned-IP-with-SNI transport 为后置增强（spec §8.3 R3-NIT3 明确允许白名单为 B3 终态）。
2. **`_LOGIN_FAILS` + `_RUNTIME_ALLOWED_HOSTS` 单进程**：进程内状态；多 worker 部署需共享存储 / 广播刷新（部署期事，非 B3 blocker）。
3. **账户锁定 DoS**：throttle-first per-username 限流被攻击时锁受害者账户 ≤5min（用户接受的取舍——撞库防护优先；CAPTCHA/MFA 为后续彻底解，记 backlog）。
4. **FIFO eviction（best-effort）**：5min 内 >4096 个不同用户名 flood 可挤掉受害者桶、计数复位。throttle-first 下桶满受害者也被锁，但 flood 复位反而短暂让其能登——这是成本护栏的 best-effort 行为，如实记录；非支付/认证级强保证。
5. **custom_api_key 明文存 per-uid `config.json`**（既有设计，custom 现激活使其生效——客户端可控密钥落 per-user 配置）。
6. **软帽（soft cap）非原子**（B2 沿用，spec §11 接受，并发略超一轮容忍）。

## DeepSeek 官渠兼容

B3 全程只加 system prompt 文本 / Starlette 中间件 / 依赖 / httpx 传输层（注入 `http_client`），**不碰** provider message / tool-call / `reasoning_content` / `tool_choice` 序列化。`test_chat_runtime.py` DeepSeek/tool-call follow-up 用例 + `test_deepseek_compat_helpers_match_chat_helpers` 全绿。

## 回归测试清单

后端：`test_url_guard` / `test_csrf` / `test_admin_api` / `test_accounts`(admin 函数 + UsageDaily) / `test_auth_api`(throttle + must_change) / `test_settings_api` / `test_config`(custom mode 持久化跨 reload) / `test_models`(offlist) / `test_tenant_isolation` + `test_main_api.py::CrossTenantApiTests`。
前端：`adminApi` / `adminPanel.source` / `forcePasswordChange.source` / `settingsModal.source` / `chatPanelCredentials.source` / `appInitGating.source`。

## 线程

Codex plan review 线程 `019eef21-100e-7952-87f0-8f46aa6ca006`（spec + quality 合并轨，4 轮含 3 轮红队对抗）；各 Phase 实施期双轨独立 review 全 APPROVED。详见 plan 的 Self-Review / 变更记录段。
