# W2-B 多租户核心 — 设计

最后更新：2026-06-21（已纳入 Codex 第 1 轮 review：8 BLOCKER 全修，见各节 ✦ 标记 + §15 变更记录）
分支：`feat/w2b-multi-tenant-core`
状态：设计稿，Codex review 中（审→修→再审）→ 待 APPROVED → writing-plans

## 1. 背景与目标

桌面版只 Windows 分发，同事多用 Mac 用不了。领导要求迁服务器、做成网页、加用户系统。W2 拆成 W2-A（N7 审查统一，已完成并 merge main）/ **W2-B（本 spec，多租户核心）** / W2-C（部署 + 去 Windows 化）。

**本 spec 目标**：把现有单用户应用改成**真正的多用户 Web 产品** —— 每个用户独立注册、独立密码、各自工作区与 API 配置完全隔离，并对 managed 通道做 per-user 成本控制。做完即：熟人凭邀请码注册、各自登录、数据互不可见、你能在后台面板管账号与花费。

**非目标（本 spec 不做）**：部署上线、HTTPS/域名、去 Windows 化 —— 全归 W2-C。浏览器原生文件上传重做、N5 custom 深化 —— 见 §14。

## 2. 范围

**In-scope**：账号系统（注册=邀请码 / 登录 / 登出 / 改密码、用户名+密码、服务端会话）；数据多租户隔离（`<data-root>/users/<uid>/...` + per-uid 引擎 + 统一归属卡点）；**服务端分配工作区的项目创建闭环**（✦ web 拒收客户端路径）；per-user managed 成本配额（金额制 ¥/天，中央计费、按模型计价）；后台管理面板（完整版）；安全红线（归属、CSRF、SSRF、CORS、cookie、限流）；前端（登录/注册页、左下角账号块、401 跳登录、配额显示、项目创建去目录依赖）；运行模式（web 无条件登录；桌面隐式 local 用户硬绑 loopback）。

**Out-of-scope**（见 §14）：部署/域名/HTTPS、去 Windows 化、浏览器上传重做、邮件服务、N5 custom 深化、按月配额、跨设备会话同步。

## 3. 已定决策汇总（brainstorm + 上机实测 + Codex R1）

| # | 决策 | 依据 |
|---|---|---|
| D1 | 一份「多租户核心」spec；**实施拆 B1/B2/B3 三 plan**（§13） | 体量适中、降回归风险 |
| D2 | Web 为主、**无条件登录**；桌面版不再维护（隐式 local 用户、✦ 硬绑 loopback） | 用户拍板 + Codex R1-B7 |
| D3 | 数据布局 `<data-root>/users/<uid>/...` | per-uid 子树 |
| D4 ✦ | per-uid 引擎实例 + 归属靠 registry 天然实现 + **统一卡点 `require_project`**；进程级锁/store **改 `(uid, project_id)` 复合键**（不依赖 project_id 全局唯一——现 id 仅 `proj-`+12hex） | Codex R1-B2/B3 |
| D5 | 服务端 session + httpOnly cookie；用户名+密码、无邮件；✦ 加 CSRF Origin/Referer 校验 | 同源 SPA 最稳 + Codex R1-B4 |
| D6 | 注册=自助 + 简单邀请码（admin 可轮换），无审批、无邮件 | 用户拍板 |
| D7 | 默认 managed + per-user 日配额；custom 可选、用自己 key 不计费 | 用户拍板 |
| D8 ✦ | **配额=金额制 ¥5/天/人**，**中央计费客户端**覆盖所有 managed 调用、按模型计价、整数微元存储 | 用户拍板 + 上机实测(§6.1) + Codex R1-B5 |
| D9 | 搜索配额**不新增**（既有 per-turn/分钟限额足够） | 用户拍板 |
| D10 | 后台管理面板做**完整版** | 用户拍板 |
| D11 ✦ | 项目创建：web 服务端分配工作区、拒收客户端 `workspace_dir`/本地材料路径 | Codex R1-B1 |

## 4. 架构总览

### 4.1 数据布局与项目创建闭环

现状所有数据挤在单一 `~/.consulting-report/`（`config.py:get_user_config_dir()`）。改造在中间插一层 `users/<uid>/`：

```
<data-root>/                         ← 默认 ~/.consulting-report，新增 env CRA_DATA_ROOT 可覆盖（服务器用）
  app.db                             ← SQLite：users / sessions / usage_daily / app_config（全局元数据）
  search_runtime_state.json          ← 搜索池状态/限额计数：保持全局（见 §4.2；D9 不做 per-user 搜索配额）
  search_cache.json                  ← 搜索结果缓存：全局共享（跨用户复用更省；✦ NIT4 仅服务端内部、绝不回显跨用户）
  users/
    <uid>/                           ← uid = 注册时生成的 uuid（非用户名，用户名可改）
      config.json                    ← 该用户 per-user Settings（mode + custom api 配置）
      projects/registry.json         ← 该用户项目注册表
      projects/<project_id>/...      ← 该用户全部项目工作区
```

- 新增 `backend/tenant.py`（叶子模块）：`data_root()`、`user_dir(uid)`、`user_projects_dir(uid)`、`user_config_path(uid)`。集中 per-uid 路径拼接。
- 搜索状态/缓存（`search_*.json`）保持全局（`data_root()/`，随 data-root 走但不进 uid 层，见 §4.2）。
- `managed_search_pool.json` / `managed_client_token` / `skill_dir` 仍走 `get_base_path()`（构建期/全局资源），不进 uid 层。

**✦ 项目创建闭环（Codex R1-B1，真安全洞）**：现状 `ProjectInfo.workspace_dir` 必填、前端走桌面目录选择器、后端 `SkillEngine.create_project(info)` 直接写该路径（`backend/models.py`、`backend/main.py:209`）—— **web 下等于任意服务器路径写入面**。改造：
- web 模式 `POST /api/projects`：**服务端在 `user_projects_dir(uid)/<project_id>` 下分配工作区**，**忽略/拒收客户端传入的 `workspace_dir` 与本地 `initial_material_paths`**（传了即 400）。
- 桌面 `uid=local` 保留旧「选目录」语义作兼容分支（仅 desktop 入口、loopback）。
- 前端新建表单去掉「选择工作目录」硬依赖（§9）。

### 4.2 per-uid 引擎工厂、复合键锁与归属卡点

登录后从 cookie 认出 uid，给该请求一个**绑定到该 uid 数据根的引擎实例**；用户的引擎只在自己的 registry 里查 → 别人的 project_id 天然查不到 → 404。

`backend/main.py` 全局单例改 per-uid 工厂（guard 保护）：

```python
_skill_engines: dict[str, SkillEngine] = {}            # uid -> engine
def get_skill_engine(uid: str) -> SkillEngine: ...      # 缺则 SkillEngine(user_projects_dir(uid), skill_dir)
def get_chat_handler(uid: str, project_id: str) -> ChatHandler: ...   # 缓存键 (uid, project_id)
```

**✦ 进程级锁/store 改复合键 `(uid, project_id)`（Codex R1-B2）**：`_PROJECT_REQUEST_LOCKS`、`_INDEPENDENT_REVIEW_LOCKS`、`_CONVERSATION_STATE_LOCKS`、`ReviewSessionStore._records`、`_chat_handlers` 一律按 `f"{uid}:{project_id}"` 键化。原因：现 project_id 仅 `proj-`+12hex、per-uid registry 不保证跨用户唯一，单键会让两用户撞同 id 时跨租户互锁、污染审查 tombstone/discard。复合键是廉价字符串拼接、机械改动，彻底消除「依赖全局唯一」的隐含前提。键里 uid 由服务端鉴权得来（绝不取客户端值）。

**✦ 统一归属卡点 `require_project`（Codex R1-B3）**：定义单一依赖

```python
def require_project(uid, project_id) -> ProjectScope:   # ProjectScope = {engine, settings, project_record, lock_key}
    rec = get_skill_engine(uid).get_project_record(project_id)
    if rec is None: raise HTTPException(404)
    return ProjectScope(...)
```

**硬规则**：任何对某项目的 lock / chat_handler / review store / 文件路径 访问，**必须先过 `require_project` 拿到 `ProjectScope`**，再用其中的 `lock_key`/`engine`。特别地，**body 里带 project_id 的接口**（`/api/chat`、`/api/chat/stream`、独立审查）必须**先 `require_project` 校验、再 `get_chat_handler`/取锁**，不得先建 handler。`lock_key` 只能来自 `ProjectScope`，禁止任何地方自行 `f"{uid}:{pid}"` 拼键绕过。

**接口归类表（迁移真值，23 接口逐项）**：

| 类别 | 接口 | 守门 |
|---|---|---|
| public | `GET /api/health`、`POST /api/auth/register`、`POST /api/auth/login`、SPA 静态 | 无（白名单） |
| uid-scoped | `GET/POST /api/settings`、`POST /api/models/list`、`GET/POST /api/projects`、`GET /api/auth/me`、`POST /api/auth/logout`、`POST /api/auth/change-password` | `Depends(get_current_uid)`（创建/设置见 D11、§8.2） |
| require_project | `materials`(list/upload/delete)、`/api/chat`、`/api/chat/stream`、`files`(GET/GET{path}/POST{path})、`workspace`、`independent-review/stream`、`independent-review/discard`、`export-draft`、`DELETE /api/projects/{id}`、`checkpoints/{name}`、`conversation`(GET/DELETE) | `Depends(require_project)`（拿锁/handler/store 前） |
| admin | `/api/admin/*` | `Depends(get_current_admin)` |
| desktop-only | `select-workspace-folder/files`、`materials/select-from-workspace` | 仅桌面（web 503）；前端隐藏 |

### 4.3 运行模式与桌面放行边界（✦ Codex R1-B7）

- **Web 模式**（`run_web.py`）：鉴权中间件生效，`auth_required=true` **恒定**，无条件登录。
- **桌面模式**（`app.py`，已不再维护）：注入隐式 `uid="local"`、`<data-root>/users/local/`。**安全边界硬约束**：① `auth_required=false` 只能由 `app.py` 入口设置；② 桌面入口强制 host=`127.0.0.1`；③ **启动断言：若 `auth_required=false` 且 host 非 loopback → 拒绝启动**（防误绑 `0.0.0.0` 变无鉴权全数据入口）。

`backend/search_pool.py` 的 `SearchRouter` 保持**全局单例**（D9 不做 per-user 搜索配额、`global_minute_limit` 本就池子级、缓存跨用户共享更省）；`project_minute_limit` 按 project_id 计仍正确。

## 5. 鉴权与账号

### 5.1 会话
- 登录建会话：随机 opaque `session_token`，DB 仅存其 **hash**（✦ NIT1，泄库不可直接复用）+ uid + 过期 + `created_ip/user_agent/last_seen`。`session_token` 写 httpOnly cookie。
- cookie：`HttpOnly`、`SameSite=Lax`、`Secure`（prod https，配置开关）。
- 「记住我」：默认 30 天固定有效期（v1 不做滚动续期）。
- 登出：删会话行 + 清 cookie。**只影响登录态，绝不动用户数据**。

### 5.2 注册
`POST /api/auth/register {username, password, invite_code}`：校验邀请码=`app_config.invite_code`（admin 可轮换）、用户名唯一与字符约束、密码强度下限 → 生成 uid + `password_hash` + 建 `users` 行 + 创建 `users/<uid>/` 骨架。无邮件、无审批。按 IP 限流。

### 5.3 登录 / 登出 / 改密码 / me
- `POST /api/auth/login` → 校验 hash → 建会话 + set-cookie；✦ 失败按 username+IP 退避限流（§8.5）。
- `POST /api/auth/logout` → 删会话 + 清 cookie。
- `POST /api/auth/change-password {old,new}` → 校验旧 → 更新 hash → **吊销其它会话**。
- `GET /api/auth/me` → `{uid, username, is_admin, today_cost_yuan, daily_cap_yuan}`。

### 5.4 数据模型（SQLite `app.db`）
```sql
users(uid TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
      is_admin INTEGER NOT NULL DEFAULT 0, daily_cost_micro_yuan INTEGER,   -- NULL=用全局默认
      must_change_password INTEGER NOT NULL DEFAULT 0, disabled INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL)
sessions(token_hash TEXT PRIMARY KEY, uid TEXT NOT NULL, created_at TEXT NOT NULL,
         expires_at TEXT NOT NULL, created_ip TEXT, user_agent TEXT, last_seen TEXT)
usage_daily(uid TEXT NOT NULL, day TEXT NOT NULL,            -- YYYY-MM-DD @ Asia/Shanghai
            cost_micro_yuan INTEGER NOT NULL DEFAULT 0,      -- ✦ 整数微元(1e-6元)，非 REAL
            cache_hit_tokens INTEGER NOT NULL DEFAULT 0, cache_miss_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(uid, day))
app_config(key TEXT PRIMARY KEY, value TEXT)                 -- invite_code、global_daily_cap_micro_yuan 等
```
- 访问层 `backend/accounts.py`（DB 封装，不反向 import chat/skill）。SQLite WAL + 短事务；`usage_daily` 累加用 `INSERT … ON CONFLICT(uid,day) DO UPDATE SET cost=cost+?`（原子）。
- 密码哈希：✦ **argon2id 固定参数**（`argon2-cffi`，新依赖）；绝不存明文。

### 5.5 鉴权中间件 / 依赖
- `get_current_uid(request)`：读 cookie → 查 `sessions`（hash 匹配、未过期、用户未 disabled）→ 返回 uid；否则 401。桌面模式固定返回 `local`（§4.3）。
- `require_project`（§4.2）、`get_current_admin`（uid + is_admin，否则 403）。
- 放行白名单：`/api/auth/login`、`/api/auth/register`、`/api/health`、SPA 静态。其余 `/api/*` 必经鉴权。

### 5.6 管理员引导
- 启动时若 env `CRA_BOOTSTRAP_ADMIN_USERNAME`+`CRA_BOOTSTRAP_ADMIN_PASSWORD` 存在且该用户不存在 → 建 `is_admin=1` 账号（幂等，✦ `must_change_password=1` 强制首登改密，NIT3）。
- `invite_code` 首次启动从 env `CRA_INVITE_CODE` 播种进 `app_config`，之后 admin 面板可轮换。

## 6. 配额与计费（✦ Codex R1-B5 重写：中央计费）

### 6.1 计费口径（上机实测确认）
2026-06-21 jp-app-01 实测 deepseek-v4-pro 经真实路径（公网 `newapi.z0y0h.work/client/v1`→薄网关→new-api→官渠）返回 `usage`：非流式冷 `hit=0/miss=1289`、非流式热 `hit=1280/miss=9`、**流式(`stream_options.include_usage`) 同样带完整缓存明细且官渠未拒**。故按三档精确计费可行：
```
单次成本(微元) = round( hit×单价_命中 + miss×单价_未命中 + completion×单价_输出 )   # 单价 = 元/token × 1e6
```
`completion_tokens` 已含 `reasoning_tokens`，不另算。

### 6.2 中央计费客户端（覆盖所有 managed 调用）
**不在 chat/review 两处零散插桩**，而是建中央 `backend/metering.py:MeteredManagedClient` —— **所有 managed provider 调用唯一出口**，每次调用必带 `uid` + `model`：
- 覆盖面：主聊天工具循环每次 iteration、post-turn compaction 摘要、**视觉转写**（`MaterialConverter` 的 Qwen3-VL）、独立审查每轮流式。任何新 managed 调用都必须走它（source-guard 测试锁死「禁止直接 new OpenAI client 发 managed 请求」）。
- 按模型计价：config 存**每模型单价表**（deepseek-v4-pro 三档；vision model 单价占位、可后填）。custom 模式不经此客户端、不计费。
- 流式：managed 请求注入 `stream_options.include_usage`（仅 managed），末包取 usage。

### 6.3 门禁、累计与降级
- **门禁（reserve）**：每次 managed 调用前查 `usage_daily(uid, 今日).cost ≥ cap` → 拒绝并回友好提示（「今日已用 ¥X/上限 ¥Y，明日 0 点(Asia/Shanghai)恢复」），不打断进行中调用、只挡下一次。
- **累计（settle）**：调用拿到 usage 后按 §6.1 算微元，`usage_daily` 原子累加。
- **✦ usage 缺失 = fail-closed**：流式中断/多模态等拿不到 usage 时，**保守封顶**（按该模型上下文长度上限 × 未命中价估上界并计入），**不**用 tiktoken 乐观低估放行；连续失败则拒绝继续。tiktoken 仅作非多模态文本调用的兜底估算、且按未命中保守。
- 跨日：`day` 按 Asia/Shanghai，新日写新行 = 自动重置。

### 6.4 覆盖与隔离
- 计费 = 所有 managed LLM/vision 调用（chat 工具循环/压缩/视觉/审查）。custom 模式（用户自带 key）不计费、不受 ¥ 门禁（仍受既有 stream/搜索限额）。
- 金额全程整数微元；展示层 ÷1e6 转 ¥。

## 7. 后台管理面板（完整版）
- 入口：左下角账号块内，`is_admin` 可见「👤 用户管理」。
- 接口（全部 `Depends(get_current_admin)` + ✦ CSRF 校验 §8.1）：
  - `GET /api/admin/users` → `[{uid, username, created_at, today_cost_yuan, daily_cap_yuan, disabled, is_admin}]`
  - `POST /api/admin/users/{uid}/password {new_password}`（改密码 + 吊销其会话 + 置 must_change_password）
  - `POST /api/admin/users/{uid}/cap {daily_cost_yuan|null}`（内部转微元）
  - `POST /api/admin/users/{uid}/disabled {disabled: bool}`（即时失效会话）
  - `GET /api/admin/invite-code`、`POST /api/admin/invite-code/rotate`
- 前端 `AdminPanel`：用户表 + 行内操作。

## 8. 安全红线
1. **归属校验**：统一 `require_project`（§4.2）+ 复合键锁；绝不信任客户端 uid/路径。
2. ✦ **CSRF（Codex R1-B4）**：所有状态变更（POST/DELETE）接口除 SameSite=Lax 外，**强制 Origin/Referer 校验**（不匹配配置允许源即 403）；admin/删项目/checkpoint 等副作用接口须有「跨站请求被拒」回归测试。**CORS** `allow_origins` 改精确域名白名单，`allow_credentials=true` 下禁 `*`。
3. ✦ **SSRF（Codex R1-B6，请求时挡）**：新增 `backend/url_guard.py` + **守门 HTTP 传输层**——校验**不在保存时一次性做**，而在**每次实际外连时解析并把连接 pin 到已校验的公网 IP**（防 DNS rebinding 二次解析）。覆盖 `POST /api/settings`（custom_api_base）、`POST /api/models/list`、chat/review client 构建。仅 http(s)；禁私网段/loopback/link-local/`169.254.169.254`。**managed_base_url 改服务端只读**（不进 per-user settings、用户不可覆盖）。
4. **cookie**：HttpOnly + SameSite=Lax + Secure(prod)；session 存 hash（§5.1）。
5. ✦ **限流**：登录失败按 username+IP 退避；注册按 IP 限流（slowapi 现按 IP，补 per-username 计数）。
6. **密码**：argon2id 哈希；改密码/禁用即时吊销会话。
7. **沿用既有**：N6 附件 trust boundary + `material_limits`；N7 报告内容 trust boundary；DeepSeek 官渠兼容（只加已实测安全的 `include_usage`）。

## 9. 前端改造
- **登录态门**：`App.jsx` 起手 `GET /api/auth/me`；401 → `Login` 视图；成功 → 主界面。
- **`Login`/`Register`**（新）：用户名+密码（注册多邀请码）。
- **左下角账号块**（`Sidebar.jsx` 底部现 `⚙ 连接设置` 处）：用户名 + 登出；`is_admin` 多「👤 用户管理」；今日 ¥已用/剩余。
- **API client**：fetch 带 `credentials:'include'` + ✦ 状态变更请求带 CSRF 满足（同源即满足 Origin 校验）；统一 401 → 跳登录。
- **项目创建去目录依赖**（✦ D11）：新建表单移除「选择工作目录」，web 由服务端分配。
- `SettingsModal`：custom API per-user（后端按 uid 存），UI 基本不动。
- `AdminPanel`（新）：§7。隐藏 web 下桌面专用按钮。
- 无 jsdom：登录态/配额/admin 纯逻辑抽 `utils/`（`authState.js`/`quotaFormat.js`）单测 + 组件 source-guard。

## 10. 后端改造影响面
**改/新增**
- `config.py`：`CRA_DATA_ROOT`；✦ **拆分**全局服务器配置 vs per-user LLM 配置（NIT5，避免 per-user settings 误用成全局单例）；managed_base_url 归全局只读；每模型单价表 + 配额常量；per-user Settings 读写走 `user_config_path(uid)`。
- 新 `backend/tenant.py`（路径）、`backend/accounts.py`（DB）、`backend/metering.py`（中央计费）、`backend/url_guard.py`（SSRF + 守门传输）。
- `main.py`：CORS 收紧 + CSRF 校验；全局单例→uid 工厂；`require_project` + 接口归类表逐项接入（§4.2）；✦ 项目创建闭环（拒客户端路径）；`/api/auth/*`、`/api/admin/*`；鉴权中间件 + 桌面 loopback 启动断言；建议按域拆 `APIRouter`（auth/projects/chat/admin）。
- `chat.py`：managed 调用改走 `MeteredManagedClient`（含工具循环/压缩）；接门禁；`_get_search_router` 全局单例不变；DeepSeek 官渠兼容不破。
- `independent_review.py`：managed 调用走 `MeteredManagedClient` + 门禁；`ReviewSessionStore`/锁改复合键；兼容 helpers 与 chat 锁定一致。
- `skill.py`/`material_conversion.py`：视觉转写经 `MeteredManagedClient`；项目创建 web 分支按 uid 分配工作区。

**复用不动（~80%）**：`SkillEngine`/`ChatHandler`/`IndependentReviewAgent`/`SearchRouter`/`MaterialConverter` 内部业务逻辑、S0–S7 阶段机、报告写入工具链、trust boundary。

## 11. 待验证 / 风险
- ✅ 流式 `include_usage` + 缓存明细端到端可得 —— 本 session 已实测（§6.1），非待验证。
- 并发：per-uid 引擎 dict + 复合键锁；`usage_daily` upsert 原子；门禁 reserve→settle 间微 TOCTOU（同一 uid 并发多调用可能略超一轮）对「软成本帽」可接受，不加重锁；如需严格可改 reserve 预扣。
- SSRF IP-pin 传输层需自定义 httpx transport（resolve→校验→连 pinned IP），实施期单列验证。
- 数据迁移：服务器全新起；桌面隐式 `local` 落 `users/local/`，开发机既有 `~/.consulting-report/projects` 如需沿用手动挪进 `users/local/`（不写迁移脚本）。

## 12. 测试策略
后端 `unittest`+`pytest`、mock 外部 HTTP：
- 账号：注册（邀请码对/错、重名、弱密码、IP 限流）、登录（成功/失败退避）、登出、改密码吊销会话、`me`；session 存 hash。
- 中间件/卡点：无 cookie/过期/disabled→401；白名单放行；桌面 uid=local + ✦ 非 loopback 拒启动；`require_project` 对他人 project_id→404；body-project_id 接口先校验后拿 handler/锁。
- 隔离：A 建项目 B 访问→404；复合键锁两用户同 project_id 不互锁；per-uid 路径/Settings 隔离。
- ✦ 项目创建：web 传 `workspace_dir`/本地材料路径→400/忽略、工作区落 `users/<uid>/`。
- 配额：三档成本（含实测数值）、中央客户端覆盖（vision/压缩/审查都计）、门禁拦截、跨日重置、custom 不计、✦ usage 缺失 fail-closed 保守封顶、微元整数累计无漂移。
- ✦ CSRF：跨站 Origin 的 POST/DELETE（含 admin）被拒。
- ✦ SSRF：私网/loopback/元数据/非 http(s) 拒；rebinding（解析变私网）连接层挡；`/api/models/list` 与 settings 都覆盖。
- admin：is_admin 门禁、改密码吊销、调 cap、禁用即时失效、轮换邀请码。
- DeepSeek 兼容：加 `include_usage` 后 chat/review 既有官渠用例不回归。
前端 `node:test`：`utils/authState`/`quotaFormat`、登录/admin 组件 source-guard。

## 13. 实施切分（✦ Codex R1-B8：一份 spec、拆 3 plan，各自可验收）
- **B1 租户基座 + 鉴权 + 创建闭环**：`tenant.py` 路径层 + per-uid 引擎/handler 工厂 + 复合键锁；`accounts.py`(users/sessions/app_config) + argon2 + `/api/auth/*`；鉴权中间件 + `require_project` + 23 接口归类接入 + bootstrap + 桌面 loopback 断言；项目创建闭环（拒客户端路径）。**验收门**：跨租户隔离回归全绿（A 不可触 B）。
- **B2 中央计费 + 配额**：`metering.py` + `include_usage` + 每模型计价 + 门禁/累计/fail-closed + `usage_daily`。**验收门**：配额覆盖面回归（vision/压缩/审查/中断）+ custom 不计。
- **B3 admin + 安全硬化**：`/api/admin/*` + 前端 AdminPanel；CSRF + SSRF(IP-pin) + CORS 收紧 + 登录限流。**验收门**：CSRF/SSRF/越权 red-team 回归。
- 前端登录态门 + 账号块随 B1，AdminPanel 随 B3。
每 plan 独立 TDD、后端先于前端、cutover report；plan 间有隔离/CSRF/配额回归门。

## 14. 不在本 spec（后续）
W2-C（部署、域名/HTTPS、去 Windows 化）；浏览器上传重做（仅隐藏桌面按钮）；N5 custom 深化；邮件服务；按月配额；滚动会话续期；跨设备会话同步。

## 15. 变更记录
- 2026-06-21 初稿（brainstorm + 上机实测拍板）。
- 2026-06-21 Codex R1（CHANGES-REQUESTED 8 BLOCKER）全修：B1 项目创建闭环(§4.1/D11)、B2 复合键锁(§4.2/D4)、B3 `require_project`+归类表(§4.2)、B4 CSRF(§8.2)、B5 中央计费(§6/§10)、B6 请求时 SSRF+IP-pin(§8.3)、B7 桌面 loopback 断言(§4.3)、B8 拆 3 plan(§13)；NIT1-5 纳入(§5.1/§5.4/§5.6/§4.1/§10)。
