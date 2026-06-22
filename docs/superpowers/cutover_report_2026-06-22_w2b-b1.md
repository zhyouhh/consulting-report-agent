# Cutover Report — W2-B / B1 多租户基座 + 鉴权 + 项目创建闭环

- **日期**：2026-06-22
- **分支**：`feat/w2b-multi-tenant-core`（HEAD = 本 cutover commit；**未 merge / 未 push**，等用户验收）
- **spec**：`docs/superpowers/specs/2026-06-21-w2b-multi-tenant-core-design.md`（4 轮 Codex APPROVED）
- **plan**：`docs/superpowers/plans/2026-06-21-w2b-b1-tenant-base-auth.md`（18 TDD task，5 轮含 2 轮红队 APPROVED）
- **实施方式**：subagent-driven（Claude opus/sonnet 逐 task 实施）+ **每 commit 后 Codex spec/quality 双轨独立 review（不合并），审→修→再审到 APPROVED**

## 一句话

把单用户桌面应用的引擎改成「登录后每个用户各自工作区完全隔离」的多租户 Web 基座：用户名+密码、邀请码自助注册、httpOnly cookie 会话、`<data-root>/users/<uid>/` 分目录、统一归属卡点 `require_project`、复合键 `(uid, project_id)` 贯彻全部进程内锁/store/搜索状态。**做完即「A 用户碰不到 B 用户的任何数据」**，并经真实 HTTP 端到端 smoke 验证。

## 验收证据

- **后端**：`pytest tests/` → **1272 passed / 1 skipped / 4 failed**。4 failed 全是 **macOS `tempfile` realpath/symlink 环境差异**（`/var` vs `/private/var`），干净 HEAD 即 fail、Windows 上通过，非本次引入：`test_skill_engine.py::{test_create_project_defaults_to_managed_workspace_under_projects_root, test_primary_report_path_uses_content_report_draft_v1_only}` + `test_workspace_materials.py::{test_create_project_stores_workspace_metadata_and_initial_materials, test_import_material_copies_external_file_into_project}`。
- **前端**：`node --test tests/` → **334 passed / 0 failed**；`npm run build` → OK。
- **真实 HTTP 端到端 smoke（web 模式、临时 data root + 邀请码、port 8899）全通过**：
  - 邀请门：错码 `403`、对码注册 `200`；登录 `200` + httpOnly cookie；`/me` 返回真实 uid + 用户名。
  - 建项目 `200`（服务端分配工作区，返回 `proj-…`）；本人列表 count=1；本人 workspace `200`。
  - **隔离**：第二用户 bob 列表 count=0；bob 按 **project_id** 访问 alice 项目 `404`；bob 按 **项目名称** 访问 `404`（名称不泄漏存在性）；未登录访问 `401`；登出 `200`。

## 18 task / 19 commit 一览

| Task | commit | 交付 |
|---|---|---|
| T1 | `9d3dc8d` | argon2-cffi 依赖 |
| T2 | `a64e347` | `backend/tenant.py`（叶子：路径助手 + `tenant_project_key` 无损可逆转义、`_safe_path_component` 拒路径穿越含 Windows 盘符）+ `config.data_root()` + search 路径接 data_root |
| T3 | `4fb4040` | `backend/accounts.py` users（argon2、`_db()` 提交+关闭上下文管理器、公共 getter 剥 password_hash） |
| T4 | `ae8e89d` | accounts sessions（sha256 token、过期/吊销、`create_session` 原子 fail-closed 拒停用用户、停用与撤销同一事务） |
| T5 | `7232a7c` | accounts app_config（邀请码 upsert/seed） |
| T6 | `bdea771` | 复合键：请求锁/会话锁 + `SkillEngine.uid`/`ChatHandler.uid` + `record_stage_checkpoint` 请求锁复合（**审查侧键迁移延后到 T11**，见下「关键决策」） |
| T7 | `929d9dd` | main.py per-uid 工厂 `get_skill_engine`/`get_chat_handler(uid,pid)` + `get_current_uid`/`get_current_admin`/`ProjectScope`/`require_project` + 邀请码 fail-closed 随机种子 |
| T8 | `c9bb25a` | per-uid settings 隔离存储 + `/api/settings` 端点 per-uid + `save_settings` 原子写 |
| T9 | `0f01a96` | `/api/auth/{register,login,logout,me,change-password}`（邀请门、httpOnly cookie、logout 幂等、改密保当前会话撤其它、合成 local /me） |
| T10 | `7afbe18` | 项目创建闭环：web 服务端分配工作区、按字段存在拒收客户端 `workspace_dir`/`initial_material_paths` |
| T11a | `9ca22f7` | 17 个非审查端点接 `require_project`/uid + 4 个 get_chat_handler 调用点 |
| T11b | `5107b1a` | 审查侧键迁移：审查端点 + chat 读 + checkpoint 门 → 复合键；`IndependentReviewAgent.run(store_key=...)` 分离 store key 与 canonical project id |
| T11c | `fd8f1c2` | 全部端点测试迁移到租户作用域 + `CrossTenantApiTests` 跨租户 404 |
| T12 | `703d025` | bootstrap admin（env，must_change_password=True）+ `assert_safe_startup`（桌面须 loopback、web 须 CRA_INVITE_CODE）+ 邀请码 env 权威 |
| T13 | `298b9f0` | 搜索 cache + project-minute 配额按复合键隔离（global 配额仍共享） |
| T14 | `fc904cb` | 前端 axios `withCredentials` + 401 拦截 + `authState` reducer |
| T15 | `d649db1` | 前端创建表单去 `workspace_dir`/材料选择器 |
| T16 | `b7894d7` | 前端登录/注册页 + App 登录态门（替换 init effect 为 auth-gated） |
| T17 | `a47f406` | 前端左下角账号块（用户名 + 登出；桌面 local 不显示） |
| T18 | 本 commit | 全量回归 + smoke + cutover |

## 关键设计 / 对 plan 的偏离（实施期 Codex 双轨 review 驱动）

- **审查侧键迁移整体延后 T11（Option C，T6 review 红队驱动）**：plan 把 chat 读 + record_stage_checkpoint 审查锁检查放 T6 转复合键，但审查端点 worker 在 main.py（plan 划 T11）仍裸键——standalone T6 会让 `uid="local"` 下「端点写 tombstone（裸）vs chat 读（复合）」不一致、单用户审查流断、`review_passed_at` 门可绕过。改为 **T6 只做请求锁/会话锁复合（各自与共享方一致），整个审查侧（端点 + chat 读 + 门禁检查 + ~30 端点测试）原子迁移留 T11**。每个 commit 内自洽。
- **`IndependentReviewAgent.run(store_key)` 分离（T11b review CRITICAL）**：`run()` 内部用同一 `project_id` 参数既做 store 写（需复合键）又做文件访问（需 canonical id）。加 `store_key`（默认=project_id 向后兼容）分离二者，否则端点 claim（复合）与 agent commit（canonical）键不一致 → 审查保存失败、tombstone 写错键。
- **邀请码 fail-closed + env 权威（T7+T12 review）**：env 未设 → 随机码（注册实质锁死，非可猜的 `change-me`）；env 已设 → `set_config` upsert 每次启动生效（修「随机码持久后运维设 env 也不生效」footgun）。web 启动 `assert_safe_startup` 硬要求 CRA_INVITE_CODE。
- **`require_project` 自然隔离**：canonicalize 到 `rec["id"]`、查不到即 404；非属主用户自己的引擎 registry 里没有该项目 → 天然 404（按 id 和按名称都不泄漏）。
- **CORS/CSRF 当前态**：沿用 `allow_origins=["*"]`（既有债）+ `SameSite=Lax` cookie 作 B1 CSRF 基线（挡跨站 POST）。完整 CSRF/CORS 硬化 = B3。

## 部署 / 运维须知（重要）

- **环境变量**（web 部署）：
  - `CRA_DATA_ROOT`：数据根（`<root>/app.db` + `<root>/users/<uid>/`）。不设则默认 `~/.consulting-report`。
  - `CRA_INVITE_CODE`：**web 模式必设**，否则 `run_web.py` 拒启动（注册会被随机码锁死）。**注意：你 mac 本地跑 `run_web.py` 现在也要带它**，例如 `CRA_INVITE_CODE=devcode .venv/bin/python run_web.py`。
  - `CRA_BOOTSTRAP_ADMIN_USERNAME` + `CRA_BOOTSTRAP_ADMIN_PASSWORD`：首启自动建管理员（`must_change_password=True`）。幂等（已存在不覆盖）。
- **启动安全门**：桌面（`app.py`，auth off）强制 loopback host；web（`run_web.py`，auth on）强制 CRA_INVITE_CODE。直连 `uvicorn backend.main:app` 默认 auth on（安全）。
- **custom 模式仍不可用**：`normalize_settings_payload` 仍强制 `mode="managed"`；per-uid settings 只隔离**存储**，custom 模式**激活 + SSRF 防护**归 B3。
- **桌面行为不变**：`app.py` 设 `auth_required=False`、uid 硬绑 `"local"`、loopback；现有桌面用户无感。

## 衔接 B2 / B3（已识别、本期 scope-out）

- **B2 中央计费**：`MeteredManagedClient` + per-user ¥/天金额配额（按 deepseek 缓存三档精确计费）。**本期未做**。
- **B3 admin 面板 + 安全硬化**：
  - admin 管理面板（用户列表/今日花费/改密/调配额/禁用/轮换邀请码）。
  - **CSRF/SSRF/CORS 硬化**：替换 wildcard CORS、auth 写操作加 Origin/Referer 校验。
  - **custom 模式激活 + SSRF 防护**（自填 API base 堵 SSRF）。
  - **`must_change_password` 强制**：当前仅 `/me` 透出 flag，无后端路由级强制改密；admin 面板期做。
  - **per-username 登录限流**（现仅 IP 级 10/min）。
  - **复用 data root 时直连 uvicorn 的邀请码协调**（Codex 备忘：旧 data root 保留既有 invite，不受新 env 影响——admin 轮换覆盖）。
  - HTTPS/TLS termination 下的 cookie `secure` 策略。
- **去 Windows 化剩项**（W2-C）：`export_draft.ps1` 改 Python、Linux pandoc（独立于 B1）。

## 红队 review 实证价值（本期被 Codex 双轨挖出并修复的真问题摘录）

T2 路径穿越（含 Windows 盘符）+ 复合键无损化、T3 password_hash 外泄 + 连接生命周期、T4 停用用户可签发会话 + 撤销非原子、T6 审查侧键 standalone 不一致（→Option C）、T7 `change-me` 开放注册洞、T8 save 半截读、T9 logout 不幂等 + 超大密码 argon2 DoS、T11a `/api/chat` 404→500 吞契约、**T11b `store_key`/`project_id` 混用致审查保存失败（深 CRITICAL）**、T12 邀请码随机码持久 footgun、T16 登录双击重复提交、T17 桌面登出困死。
