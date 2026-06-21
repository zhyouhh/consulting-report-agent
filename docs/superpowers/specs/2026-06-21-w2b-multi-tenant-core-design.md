# W2-B 多租户核心 — 设计

最后更新：2026-06-21
分支：`feat/w2b-multi-tenant-core`
状态：设计稿，待用户 review → writing-plans

## 1. 背景与目标

桌面版只 Windows 分发，同事多用 Mac 用不了。领导要求迁服务器、做成网页、加用户系统。W2 拆成 W2-A（N7 审查统一，已完成并 merge main）/ **W2-B（本 spec，多租户核心）** / W2-C（部署 + 去 Windows 化）。

**本 spec 目标**：把现有单用户应用改成**真正的多用户 Web 产品** —— 每个用户独立注册、独立密码、各自工作区与 API 配置完全隔离，并对 managed 通道做 per-user 成本控制。做完即：熟人凭邀请码注册、各自登录、数据互不可见、你能在后台面板管账号与花费。

**非目标（本 spec 不做）**：部署上线、HTTPS/域名、去 Windows 化（`export_draft.ps1`→Python、Linux pandoc）—— 全归 W2-C。浏览器原生文件上传重做、N5 custom 模式深化 —— 见 §14。

## 2. 范围

**In-scope**
- 账号系统：注册（邀请码）/登录/登出/改密码、用户名+密码、服务端会话。
- 数据多租户隔离：`<data-root>/users/<uid>/...`，per-uid 引擎实例 + 归属校验。
- per-user managed 成本配额：金额制 ¥/天，精确按 deepseek 缓存分档计费。
- 后台管理面板（完整版）：用户列表 + 今日花费 + 改密码 + 调配额 + 禁用 + 轮换邀请码。
- 安全红线：归属校验、custom 模式 SSRF 防护、CORS 收紧、cookie 安全标志、登录限流。
- 前端：登录/注册页、左下角账号块（登出 + 管理入口）、401 自动跳登录、配额显示。
- 运行模式：web 强制登录；桌面模式保留隐式本地用户不崩（零投入）。

**Out-of-scope**（明确排除，见 §14）：部署/域名/HTTPS、去 Windows 化、浏览器上传重做、邮件服务、N5 custom 深化、按月配额、跨设备会话同步。

## 3. 已定决策汇总（brainstorm + 上机实测拍板）

| # | 决策 | 依据 |
|---|---|---|
| D1 | 一份「多租户核心」spec，web-IO/N5/部署另列 | 体量适中、不稀释 review |
| D2 | Web 为主、**无条件登录**；桌面版不再维护（隐式本地用户不崩、零投入） | 用户拍板 |
| D3 | 数据布局 `<data-root>/users/<uid>/...` | per-uid 子树 |
| D4 | per-uid 引擎实例工厂 + 归属靠 registry 天然实现 + 依赖层薄兜底；**锁仍按 project_id 键化不改双键**（UUID 全局唯一） | 大幅降改造量 |
| D5 | 服务端 session + httpOnly cookie；用户名+密码、无邮件 | 同源 SPA 最稳；范围内不接邮件 |
| D6 | 注册=自助 + 简单邀请码（admin 可轮换），无审批、无邮件 | 用户拍板 |
| D7 | 默认 managed + per-user 日配额；custom 可选、用自己 key 不计费 | 用户拍板 |
| D8 | **配额=金额制 ¥5/天/人**，精确按缓存分档计费（命中 0.025 / 未命中 3 / 输出 6 元每百万 token） | **上机实测确认可拿缓存明细**（见 §6.1） |
| D9 | 搜索配额**不新增**（既有 per-turn/分钟限额足够，LLM 门禁天然兜住搜索） | 用户拍板 |
| D10 | 后台管理面板做**完整版** | 用户拍板 |

## 4. 架构总览

### 4.1 数据布局

现状所有数据挤在单一 `~/.consulting-report/`（`config.py:get_user_config_dir()`）。改造在中间插一层 `users/<uid>/`：

```
<data-root>/                         ← 默认 ~/.consulting-report，新增 env CRA_DATA_ROOT 可覆盖（服务器用）
  app.db                             ← SQLite：users / sessions / usage_daily / app_config（全局元数据）
  search_runtime_state.json          ← 搜索池状态/限额计数：保持全局（见 §4.2；D9 不做 per-user 搜索配额）
  search_cache.json                  ← 搜索结果缓存：全局共享（跨用户复用更省）
  users/
    <uid>/                           ← uid = 注册时生成的 uuid（非用户名，用户名可改）
      config.json                    ← 该用户 Settings（mode + custom api 配置）
      projects/registry.json         ← 该用户项目注册表
      projects/<project_id>/...      ← 该用户全部项目工作区
```

- 新增 `backend/tenant.py`（叶子模块）：`data_root()`、`user_dir(uid)`、`user_projects_dir(uid)`、`user_config_path(uid)`。集中 per-uid 路径拼接，替散落在 `config.py` 的全局路径。
- 搜索状态/缓存（`search_*.json`）保持全局（`data_root()/`，随 data-root 走但不进 uid 层，见 §4.2）。
- `managed_search_pool.json` / `managed_client_token` / `skill_dir` 仍走 `get_base_path()`（构建期/全局资源，非用户数据），不进 uid 层。

### 4.2 per-uid 引擎工厂与归属

核心思想：登录后从 cookie 认出 uid，给该请求一个**绑定到该 uid 数据根的引擎实例**；用户的引擎只在自己的 registry 里查 → 别人的 project_id 天然查不到 → 404。归属校验几乎白送。

`backend/main.py` 现有全局单例改工厂（按 uid 缓存、guard 保护）：

```python
_skill_engines: dict[str, SkillEngine] = {}            # uid -> engine
def get_skill_engine(uid: str) -> SkillEngine:
    with _engines_guard:
        e = _skill_engines.get(uid)
        if e is None:
            e = SkillEngine(user_projects_dir(uid), settings.skill_dir)
            _skill_engines[uid] = e
        return e

def get_chat_handler(uid: str, project_id: str) -> ChatHandler:   # 现签名 (project_id) → (uid, project_id)
    ...缓存键 (uid, project_id)，绑定 get_skill_engine(uid) + 该 uid 的 Settings...
```

**锁不改双键（D4）**：`_PROJECT_REQUEST_LOCKS` / `_INDEPENDENT_REVIEW_LOCKS` / `_CONVERSATION_STATE_LOCKS` / `ReviewSessionStore._records` 仍按 `project_id` 键化。理由：project_id 是全局唯一 UUID、一个项目只属于一个 uid，故 project_id 单键永不跨用户碰撞，现有并发语义仍正确。**只要保证归属校验在前**（拿不到别人的 project_id），这些键就安全。

`SearchRouter` 保持**全局单例**（`_SEARCH_ROUTER_SINGLETON` 不改）：D9 不做 per-user 搜索配额，且 `global_minute_limit` 本就是池子级全局限额、`search_cache` 跨用户共享更省 —— 按 uid 拆会破坏全局限额语义。`project_minute_limit` 按 project_id 计（UUID 唯一）仍正确。搜索状态/缓存文件落 `data_root()/`（全局）。

**归属兜底**：所有项目级接口统一 `uid = Depends(get_current_uid)` → `engine = get_skill_engine(uid)` → `engine.get_project_record(project_id)` 为 None 即 404。绝不信任客户端传来的 uid。

### 4.3 运行模式

- **Web 模式**（`run_web.py`，生产 + mac 开发）：鉴权中间件生效，无条件要登录。
- **桌面模式**（`app.py` + PyWebView，已不再维护）：注入一个隐式本地用户 `uid="local"`（鉴权中间件在 desktop 模式放行、固定 uid=local），数据落 `<data-root>/users/local/`。零投入、不崩即可，不保证 PyWebView 下登录体验。模式判定走一个显式开关（启动入口设置，如 `app.state.auth_required`）。

## 5. 鉴权与账号

### 5.1 会话

- 服务端 session：登录成功在 `sessions` 表建一条（随机 opaque `session_id` + uid + 过期时间），`session_id` 写 httpOnly cookie。
- cookie 标志：`HttpOnly`、`SameSite=Lax`、`Secure`（生产 https 时；由配置开关，dev http 关）。
- 「记住我」：长有效期（默认 30 天）；可选滚动续期（访问时延期）。v1 先固定有效期。
- 登出：删 `sessions` 行 + 清 cookie。**只影响登录态，绝不动任何用户数据**。

### 5.2 注册

- `POST /api/auth/register {username, password, invite_code}`。
- 校验：邀请码匹配 `app_config.invite_code`（admin 可轮换）；用户名唯一、长度/字符约束；密码强度下限。
- 生成 uid（uuid）、`password_hash`，建 `users` 行；创建 `<data-root>/users/<uid>/` 目录骨架。
- 无邮件、无审批。

### 5.3 登录 / 登出 / 改密码

- `POST /api/auth/login {username, password}` → 校验 hash → 建会话 + set-cookie。失败计数限流（§8）。
- `POST /api/auth/logout` → 删会话 + 清 cookie。
- `POST /api/auth/change-password {old, new}`（登录态）→ 校验旧密码 → 更新 hash → 可选吊销其它会话。
- `GET /api/auth/me` → `{uid, username, is_admin, today_cost_yuan, daily_cap_yuan}`（前端起手判断登录态 + 配额显示）。

### 5.4 数据模型（SQLite `app.db`）

```sql
users(
  uid TEXT PRIMARY KEY,                 -- uuid
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  is_admin INTEGER NOT NULL DEFAULT 0,
  daily_cost_cap_yuan REAL,             -- NULL = 用全局默认
  disabled INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
)
sessions(
  session_id TEXT PRIMARY KEY,
  uid TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
)
usage_daily(
  uid TEXT NOT NULL,
  day TEXT NOT NULL,                    -- YYYY-MM-DD，按 Asia/Shanghai
  cost_yuan REAL NOT NULL DEFAULT 0,
  cache_hit_tokens INTEGER NOT NULL DEFAULT 0,
  cache_miss_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (uid, day)
)
app_config(key TEXT PRIMARY KEY, value TEXT)   -- invite_code、global_daily_cap 等 admin 可改项
```

- 访问层 `backend/accounts.py`（DB 封装，纯函数 + 一个连接管理；不反向 import chat/skill）。SQLite WAL + 短事务；`usage_daily` 累加用 `INSERT ... ON CONFLICT(uid,day) DO UPDATE`（原子）。
- 密码哈希：`argon2-cffi`（或 `passlib[bcrypt]`）—— 新依赖，写进 `requirements.txt`。绝不存明文。

### 5.5 鉴权中间件 / 依赖

- FastAPI 依赖 `get_current_uid(request)`：读 cookie → 查 sessions（未过期、用户未 disabled）→ 返回 uid；否则 401。
- `get_current_admin`：`get_current_uid` + `users.is_admin`，否则 403。
- 放行白名单：`/api/auth/login`、`/api/auth/register`、`/api/health`、静态 SPA 资源（登录页是 SPA 的一部分）。
- 其余所有 `/api/*` 必经 `get_current_uid`。桌面模式下中间件固定返回 `uid="local"`（见 §4.3）。

### 5.6 管理员引导（bootstrap）

- 启动时若 env `CRA_BOOTSTRAP_ADMIN_USERNAME` + `CRA_BOOTSTRAP_ADMIN_PASSWORD` 存在且该用户不存在 → 建一个 `is_admin=1` 账号（幂等）。
- `invite_code` 首次启动从 env `CRA_INVITE_CODE` 播种进 `app_config`，之后 admin 面板可轮换。

## 6. 配额与计费

### 6.1 计费口径（上机实测确认）

2026-06-21 在 jp-app-01 实测 deepseek-v4-pro 经真实路径（公网 `newapi.z0y0h.work/client/v1` → 薄网关 → new-api → 官渠）返回的 `usage`：

```
非流式冷:  prompt_cache_hit_tokens=0,    prompt_cache_miss_tokens=1289, completion=16
非流式热:  prompt_cache_hit_tokens=1280, prompt_cache_miss_tokens=9,    completion=16
流式(include_usage): 同上，缓存明细完整      ← 流式也拿得到，官渠未拒 include_usage
```

结论：缓存命中/未命中**端到端可得**（薄网关透传 usage），流式加 `stream_options.include_usage` 安全。故金额制按三档精确计费可行：

```
单次成本(元) = cache_hit_tokens × 单价_命中/1e6
             + cache_miss_tokens × 单价_未命中/1e6
             + completion_tokens × 单价_输出/1e6
```

`completion_tokens` 已含 `completion_tokens_details.reasoning_tokens`（reasoner 思考 token 计输出价），无需另算。

### 6.2 单价与默认上限（配置，可改）

`backend/config.py` 新增（env 可覆盖）：
- `PRICE_CACHE_HIT_INPUT_PER_M = 0.025`
- `PRICE_CACHE_MISS_INPUT_PER_M = 3.0`
- `PRICE_OUTPUT_PER_M = 6.0`
- `DEFAULT_DAILY_COST_CAP_YUAN = 5.0`

单价是用户当前所报值、留配置随时改；per-user 上限 `users.daily_cost_cap_yuan` 覆盖全局默认。

### 6.3 用量累计与门禁

- **门禁（turn 起手）**：managed 模式下，开新一轮前查 `usage_daily(uid, 今日).cost_yuan >= cap` → 不调 LLM，回**友好提示**（含「今日已用 ¥X / 上限 ¥Y，明日 0 点恢复」），不是冷报错。不打断进行中的 turn（只挡下一轮）。
- **累计（每次 LLM 响应后）**：从 usage 取三档 token → 算成本 → `usage_daily` 原子累加（cost + 三类 token）。
- **日界**：`day` 按 Asia/Shanghai；跨日自然写新行、旧行不动 = 自动重置。
- managed 流式请求**加 `stream_options.include_usage=true`**（仅 managed；custom 不加，避未知 provider 兼容问题）。

### 6.4 降级路径

万一某次 usage 缺失（理论上不应发生，实测稳定）：本地 `tiktoken` 数 token 估算，**输入按未命中价保守计**（宁高估不超支），记一条 warning。这是兜底保险，非主路径。

### 6.5 计费覆盖面

- 覆盖**所有 managed LLM 调用**：主聊天（`chat.py`）+ 独立审查（`independent_review.py`）。两处都接门禁 + 累计。
- **custom 模式不计费**（花用户自己的 key）；custom 模式也不受 ¥ 门禁限制（但仍受既有 stream/搜索限额）。

## 7. 后台管理面板（完整版）

- 入口：左下角账号块内，`is_admin` 用户可见「👤 用户管理」。
- 接口（全部 `Depends(get_current_admin)`）：
  - `GET /api/admin/users` → `[{uid, username, created_at, today_cost_yuan, daily_cap_yuan, disabled, is_admin}]`
  - `POST /api/admin/users/{uid}/password {new_password}` — 帮用户改密码（吊销其会话）
  - `POST /api/admin/users/{uid}/cap {daily_cost_cap_yuan|null}` — 调配额上限
  - `POST /api/admin/users/{uid}/disabled {disabled: bool}` — 禁用/启用（禁用即时失效会话）
  - `GET /api/admin/invite-code` / `POST /api/admin/invite-code/rotate` — 看/轮换邀请码
- 前端 `AdminPanel`：用户表（用户名/注册时间/今日 ¥已用·上限/状态）+ 行内操作（改密码、调上限、禁用、轮换邀请码）。

## 8. 安全红线（web 比桌面多出的威胁面）

1. **归属校验**：per-uid 引擎天然 + 依赖层薄兜底（A 拿到 B 的 project_id 也 404）。所有项目级接口走统一 uid 解析，绝不信任客户端 uid。
2. **custom 模式 SSRF**：校验用户填的 `custom_api_base` —— 仅 http(s)；解析 host 的 IP（含 DNS 解析结果，防 rebinding）禁私网段/loopback/link-local/`169.254.169.254` 云元数据。新增 `backend/url_guard.py`。
3. **CORS 收紧**：SPA 由 FastAPI 同源托管，`allow_origins=["*"]` 改为配置化白名单（生产填前端域名）；`allow_credentials=true` 不能配 `*`。
4. **cookie 安全**：HttpOnly + SameSite=Lax + Secure（prod）。
5. **登录限流**：登录失败按 username/IP 计数退避（slowapi 现按 IP，补 per-username 计数）；注册按 IP 限流。
6. **密码**：argon2/bcrypt 哈希；改密码/禁用即时吊销会话。
7. **沿用既有**：N6 附件 trust boundary + `material_limits` 大小/类型；N7 报告内容 trust boundary。

## 9. 前端改造

- **登录态门**：`App.jsx` 加载先 `GET /api/auth/me`；401 → 渲染 `Login` 视图；成功 → 渲染主界面。
- **`Login` / `Register` 组件**（新）：用户名+密码（注册多一个邀请码）。
- **左下角账号块**（`Sidebar.jsx` 底部，现放 `⚙ 连接设置` 处）：显示当前用户名 + 「登出」；`is_admin` 多「👤 用户管理」。配额「今日 ¥已用 / 剩余」显示在此。
- **API client**：所有 fetch 带 `credentials: 'include'`；统一 401 拦截 → 跳登录。
- **`SettingsModal`**：custom API 配置自然 per-user（后端按 uid 存），UI 基本不动；可加今日配额展示。
- **`AdminPanel`**（新）：见 §7。
- 隐藏 web 模式下的桌面专用「从工作区选择材料」按钮（`select-from-workspace` web 下 503）。
- 无 jsdom：登录态/配额/admin 表的纯逻辑抽 `utils/`（如 `authState.js`、`quotaFormat.js`）单测 + 组件 source-guard。

## 10. 后端改造影响面

**改**
- `config.py`：数据根 `CRA_DATA_ROOT`、单价/配额常量、Settings 读写改 per-uid（`user_config_path(uid)`）。
- 新 `backend/tenant.py`（路径）、`backend/accounts.py`（DB）、`backend/url_guard.py`（SSRF）。
- `main.py`：CORS 收紧；全局 `skill_engine`/`_chat_handlers` 改 uid 工厂；23 接口加 `Depends(get_current_uid)` + 归属；新增 `/api/auth/*`、`/api/admin/*`；鉴权中间件/依赖；bootstrap。建议顺手把路由按域拆 `APIRouter`（auth/projects/chat/admin），降 758 行单文件复杂度（受现状所限的目标性改善，不做无关重构）。
- `chat.py`：managed 流式加 `include_usage`；接配额门禁 + usage 累计；`_get_search_router` 保持全局单例（仅搜索状态文件路径随 data-root）；`_build_system_prompt` 等不动。**DeepSeek 官渠兼容不破**（只加已实测安全的 `stream_options.include_usage`，不碰 tool_choice/reasoning_content/provider message 序列化）。
- `independent_review.py`：接配额门禁 + usage 累计；DeepSeek 兼容 helpers 与 chat 保持锁定一致（既有 `test_deepseek_compat_helpers_match_chat_helpers`）。

**复用不动（~80%）**：`SkillEngine`/`ChatHandler`/`IndependentReviewAgent`/`SearchRouter`/`MaterialConverter` 内部业务逻辑；阶段机 S0–S7；报告写入工具链；trust boundary。

## 11. 待验证 / 风险

- ✅ 流式 `include_usage` + 缓存明细端到端可得 —— **本 session 已实测确认**（§6.1），非待验证。
- 并发：per-uid 引擎 dict 用 guard；`usage_daily` upsert 原子；门禁读-判-调之间有微 TOCTOU（两请求同时起手都通过），对「软成本帽」可接受（最多超一轮），不加重锁。
- 数据迁移：服务器全新起、无历史用户数据要迁。桌面隐式 `local` 用户落 `users/local/`，开发机既有 `~/.consulting-report/projects` 如需沿用可手动挪进 `users/local/`（不写迁移脚本，桌面不再维护）。
- 时区：日界固定 Asia/Shanghai；服务器时区无关（代码内转换）。

## 12. 测试策略

- 后端 `unittest`+`pytest`、mock 外部 HTTP：
  - 账号：注册（邀请码对/错、用户名重复、弱密码）、登录（成功/失败/限流）、登出、改密码、`me`。
  - 会话中间件：无 cookie/过期/disabled → 401；白名单放行；桌面模式 uid=local。
  - 隔离：A 建项目、B 访问 A 的 project_id → 404（归属天然）；per-uid 路径/Settings/搜索状态隔离。
  - 配额：三档成本计算（缓存命中/未命中/输出，含实测数值用例）、累计、门禁拦截、跨日重置、custom 不计费、usage 缺失降级。
  - admin：`is_admin` 门禁、改密码吊销会话、调 cap、禁用即时失效、轮换邀请码。
  - SSRF：私网/loopback/元数据/非 http(s) 拒、正常放行。
  - DeepSeek 兼容：`include_usage` 加入后 chat/review 既有官渠用例不回归。
- 前端 `node:test`：`utils/authState`、`quotaFormat`、登录/admin 组件 source-guard。

## 13. 实施切分提示（给 writing-plans）

TDD、后端先于前端、只读/基础设施先于鉴权：
1. `tenant.py` 路径层 + per-uid 引擎/handler/search 工厂（默认 uid，无鉴权）。
2. `accounts.py`（users/sessions/usage/app_config）+ 密码哈希 + `/api/auth/*` 接口。
3. 鉴权中间件/依赖 + 全接口 `Depends(uid)` + 归属兜底 + bootstrap。
4. 配额：`include_usage` + 成本计算 + 累计 + 门禁（chat + review）。
5. `/api/admin/*` 接口。
6. 前端：登录态门 + Login/Register + 账号块 + 401 拦截。
7. 前端：AdminPanel + 配额显示。
8. 安全：SSRF + CORS + cookie 标志 + 登录限流。
9. 回归 + cutover report。

## 14. 不在本 spec（后续）

- **W2-C**：部署（venv+uvicorn）、域名/HTTPS、去 Windows 化（`export_draft.ps1`→Python、Linux pandoc）。
- **浏览器上传重做**：web 上传接口已存在（`/materials/upload`），本 spec 仅隐藏桌面专用按钮；原生选择器替代另议。
- **N5 custom 深化**：per-user custom 高级项、多 provider 预设。
- 邮件服务（找回密码自助化）、按月配额、滚动会话续期、跨设备会话同步。
