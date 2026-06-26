import asyncio
import json
import logging
import os
import secrets
import shutil
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import decimal
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal, Optional

import uvicorn
from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.responses import JSONResponse
from urllib.parse import urlparse

from .chat import (
    ChatHandler,
    LEGACY_EMPTY_ASSISTANT_FALLBACKS,
    _strip_legacy_stage_ack,
    strip_tool_log_comments,
)
from .config import Settings, get_base_path, heal_stale_managed_model, load_settings, save_settings
from .context_policy import clamp_custom_context_limit_override
from .independent_review import (
    IndependentReviewAgent,
    _REVIEW_SESSION_STORE,
    get_independent_review_lock,
)
from .models import ChatRequest, ChatResponse, ProjectInfo
from .report_tools import export_reviewable_draft
from .material_limits import MAX_HEAVY_MATERIAL_BYTES
from .skill import SkillEngine, StaleFileError, UserWriteForbiddenError
from .tenant import user_projects_dir, ensure_user_dirs, tenant_project_key
from . import accounts
from . import url_guard


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="咨询报告写作助手")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# —— CSRF / CORS 允许源 ——
# 必须定义在 CORS middleware 注册之前：CORS 的 allow_origins=list(allowed_origins())
# 在 import 期求值。
_LOOPBACK_ORIGINS = {
    "http://127.0.0.1:8080", "http://localhost:8080",
    "http://127.0.0.1:8888", "http://localhost:8888",
    "http://localhost:3000", "http://127.0.0.1:3000",  # vite dev
}


def allowed_origins(include_loopback: bool = True) -> set[str]:
    # include_loopback: 生产 web 态（CSRF 中间件运行时判定）不信任 dev/loopback 源，传 False。
    # CORS 在 import 期调用（app.state 未就绪）→ 用默认 True 静态快照；真正的生产收紧在 CSRF 层。
    origins = set(_LOOPBACK_ORIGINS) if include_loopback else set()
    raw = (os.environ.get("CRA_ALLOWED_ORIGIN") or "").strip()
    if raw:
        origins |= {o.strip().rstrip("/") for o in raw.split(",") if o.strip()}
    return origins


def _origin_from_referer(referer):
    if not referer:
        return None
    p = urlparse(referer)
    if p.scheme and p.netloc:
        return f"{p.scheme}://{p.netloc}"
    return None


# CSRF Origin/Referer 中间件：web 态（auth_required）对所有状态变更方法校验同源。
# 桌面 loopback（auth_required=False）跳过；GET/OPTIONS（含 preflight）不检查。
# 在 CORS 之前注册 → CORS 作为最外层 middleware（Starlette 按反向 add 顺序包裹）。
# 生产收紧（codex 红队 NIT）：auth_required 且 cookie_secure（https 部署）= 生产 web 态，
# 不把 loopback/dev 源纳入允许集——只信任 CRA_ALLOWED_ORIGIN 配的站点源。
@app.middleware("http")
async def csrf_origin_guard(request, call_next):
    if (request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and getattr(request.app.state, "auth_required", True)):
        is_production = bool(getattr(request.app.state, "cookie_secure", False))
        allowed = allowed_origins(include_loopback=not is_production)
        origin = request.headers.get("origin") or _origin_from_referer(request.headers.get("referer"))
        if not origin or origin.rstrip("/") not in allowed:
            return JSONResponse({"detail": "跨站请求被拒绝"}, status_code=403)
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=list(allowed_origins()),
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)

settings = load_settings()

# 启动时若 managed_model 已不在网关白名单（例如老用户升级 exe 后保留的旧模型名），
# 自动切到网关 /v1/models 的第一项并持久化。Best-effort：网络失败一律不影响启动。
try:
    _healed_settings, _heal_msg = heal_stale_managed_model(settings)
    if _heal_msg:
        settings = _healed_settings
        save_settings(settings)
        logger.warning(_heal_msg)
except Exception:
    logger.exception("heal_stale_managed_model failed unexpectedly; continuing with stored settings")

accounts.init_db()
_env_invite = (os.environ.get("CRA_INVITE_CODE") or "").strip()
if _env_invite:
    accounts.set_config("invite_code", _env_invite)   # 运维显式设的邀请码每次启动都生效（权威）
else:
    accounts.seed_config_if_absent("invite_code", secrets.token_urlsafe(12))  # 未设→随机锁死(fail-closed)
    logger.warning(
        "CRA_INVITE_CODE 未设置：已随机生成一次性邀请码并写入 app_config（注册实质锁死）。"
        "请设置 CRA_INVITE_CODE 后重启，或经后续管理面板轮换邀请码。"
    )


def _bootstrap_admin():
    u = os.environ.get("CRA_BOOTSTRAP_ADMIN_USERNAME")
    p = os.environ.get("CRA_BOOTSTRAP_ADMIN_PASSWORD")
    if u and p and accounts.get_user_by_username(u) is None:
        ensure_user_dirs(accounts.create_user(u, p, is_admin=True, must_change_password=True))


_bootstrap_admin()


# 启动时把 app_config 白名单注入 url_guard（admin 之前存的允许域名进程起来即生效）。
# NIT R3：顺手过滤历史非法项（accounts 层只存不校验），避免坏数据进运行时白名单。
url_guard.set_runtime_allowed_hosts(
    [h for h in accounts.get_custom_api_extra_hosts() if url_guard.is_valid_hostname(h)]
)


# host 在 import 期未知，无法集中化；必须由受支持的入口（run_web.py / app.py）显式调用作早退检查。
def assert_safe_startup(auth_required: bool, host: str) -> None:
    if not auth_required and host not in {"127.0.0.1", "::1", "localhost"}:
        raise SystemExit(f"拒绝启动：auth 关闭时 host 必须 loopback，当前 {host!r}")
    if auth_required and not (os.environ.get("CRA_INVITE_CODE") or "").strip():
        raise SystemExit(
            "拒绝启动：web 模式（鉴权开启）必须显式设置 CRA_INVITE_CODE 环境变量"
            "（否则邀请码被随机锁死、无人能注册）。示例：CRA_INVITE_CODE=你的邀请码"
        )


_engines: dict[str, SkillEngine] = {}
_engines_guard = threading.Lock()
_chat_handlers: dict[tuple, ChatHandler] = {}
_settings_lock = threading.Lock()
_desktop_bridge = None
SESSION_COOKIE = "cra_session"


# —— per-username 登录限流（反撞库；补 per-IP slowapi，IP 轮换绕不过）——
# 设计：throttle-first（reserve-before-verify，用户拍板）。每个 username 5 分钟滑窗内最多
# _LOGIN_MAX_FAILS 次尝试；桶满后直接 429、**不再跑 verify_user_password**——这是真封顶：
# 攻击者每用户名每窗口只能试 MAX 个密码，IP 轮换绕不过 per-IP slowapi 也绕不过这层。
# 取舍：桶被攻击者填满时该用户正确密码也会 429（5 分钟窗口滚动后自动恢复）——业界标准的
# 连续失败锁定，用户明确选择「撞库防护真实有效」优先；彻底解法 = 未来给热路径加 CAPTCHA /
# 邮箱验证作摩擦（backlog，非 B3）。
# 关键约束（codex 红队）：
#  - 限流 key = 登录请求里的原始 username（精确、不 casefold/trim）。账号查找是大小写敏感
#    精确匹配（accounts._get_user_row: WHERE username=?，无 COLLATE NOCASE），桶必须与登录
#    主体 1:1 对齐，否则 Alice/alice 这类可独立注册的账号会共享桶、放大锁定面。
#  - reserve 必须锁内原子（_reserve_login_attempt）：单锁内 prune + 判上限 + append，
#    并发错误密码请求无法一波越过帽（达 MAX 后 reserve 返 True、不再 append、调用方直接 429）。
#  - store 有界：登录是 pre-auth，攻击者可灌任意用户名；per-key deque(maxlen) + 全局键数上限，
#    且只 prune 触及的键、不每请求 O(N) 全表扫描。
import time as _time
from collections import deque

_LOGIN_FAILS: "dict[str, deque[float]]" = {}
_LOGIN_FAILS_LOCK = threading.Lock()
_LOGIN_WINDOW_SEC = 300.0
_LOGIN_MAX_FAILS = 10
_MAX_TRACKED_LOGIN_KEYS = 4096   # 全局键数上限，防 pre-auth 灌任意用户名撑爆内存


def _prune_key(key: str, now: float) -> int:
    """drop 该 key 已过期的时间戳（持锁内调用）；返回窗口内剩余计数。空则删键。"""
    bucket = _LOGIN_FAILS.get(key)
    if bucket is None:
        return 0
    while bucket and now - bucket[0] >= _LOGIN_WINDOW_SEC:
        bucket.popleft()
    if not bucket:
        del _LOGIN_FAILS[key]
        return 0
    return len(bucket)


def _evict_if_over_cap() -> None:
    """全局键数超上限时，按插入顺序（FIFO）丢弃最老的键直到回到上限内（持锁内调用）。
    仅在超限时触发，不每请求全表扫描。"""
    while len(_LOGIN_FAILS) > _MAX_TRACKED_LOGIN_KEYS:
        oldest = next(iter(_LOGIN_FAILS))
        del _LOGIN_FAILS[oldest]


def _login_throttled(username: str) -> bool:
    now = _time.monotonic()
    with _LOGIN_FAILS_LOCK:
        return _prune_key(username, now) >= _LOGIN_MAX_FAILS


def _reserve_login_attempt(username: str) -> bool:
    """throttle-first：原子地为本次登录尝试占一个槽。返回 True=已达上限（调用方直接 429、
    且不再验密）；False=未超限、已 append 占槽（调用方继续验密，验对则清桶）。
    锁内 prune-this-key + 判上限 + append，消除 TOCTOU；桶满时不 append、不验密 → 真封顶。"""
    now = _time.monotonic()
    with _LOGIN_FAILS_LOCK:
        _prune_key(username, now)
        bucket = _LOGIN_FAILS.get(username)
        if bucket is None:
            bucket = _LOGIN_FAILS[username] = deque(maxlen=_LOGIN_MAX_FAILS)
        if len(bucket) >= _LOGIN_MAX_FAILS:
            return True          # 已达上限：不 append、不验密，调用方直接 429
        bucket.append(now)
        _evict_if_over_cap()
        return False


def _clear_login_fails(username: str) -> None:
    with _LOGIN_FAILS_LOCK:
        _LOGIN_FAILS.pop(username, None)


def register_desktop_bridge(bridge):
    global _desktop_bridge
    _desktop_bridge = bridge


def get_skill_engine(uid: str) -> SkillEngine:
    with _engines_guard:
        eng = _engines.get(uid)
        if eng is None:
            ensure_user_dirs(uid)
            eng = SkillEngine(user_projects_dir(uid), settings.skill_dir, uid=uid)
            _engines[uid] = eng
        return eng


def get_chat_handler(uid: str, project_id: str) -> ChatHandler:
    # Use the module-level load_settings (imported at top); do NOT re-import inside the function,
    # or tests that patch backend.main.save_settings/load_settings won't intercept it.
    with _settings_lock:
        key = (uid, project_id)
        if key not in _chat_handlers:
            _chat_handlers[key] = ChatHandler(load_settings(uid), get_skill_engine(uid), uid=uid)
        return _chat_handlers[key]


def get_current_uid(request: Request) -> str:
    if not getattr(request.app.state, "auth_required", True):
        return "local"
    token = request.cookies.get(SESSION_COOKIE)
    uid = accounts.get_session_uid(token) if token else None
    if not uid:
        raise HTTPException(status_code=401, detail="未登录")
    return uid


def require_password_current(uid: str = Depends(get_current_uid)) -> str:
    # B3 Task 14：must_change_password 为真时，对业务路由 403，前端据此弹强制改密屏。
    # 桌面 local（auth off）无此约束；豁免集（me/change-password/logout/health）不串本依赖。
    if not getattr(app.state, "auth_required", True):
        return uid   # 桌面 local 无此约束
    user = accounts.get_user_by_uid(uid)
    if user and user.get("must_change_password"):
        raise HTTPException(status_code=403, detail="must_change_password")
    return uid


def get_current_admin(uid: str = Depends(require_password_current)) -> str:
    rec = accounts.get_user_by_uid(uid)
    if not rec or not rec["is_admin"]:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return uid


@dataclass
class ProjectScope:
    uid: str
    project_id: str          # canonical rec["id"]
    engine: SkillEngine
    project_record: dict
    lock_key: str


def require_project(project_id: str, uid: str = Depends(require_password_current)) -> ProjectScope:
    eng = get_skill_engine(uid)
    rec = eng.get_project_record(project_id)   # accepts id OR name alias
    if rec is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    cid = rec["id"]
    return ProjectScope(uid=uid, project_id=cid, engine=eng, project_record=rec,
                        lock_key=tenant_project_key(uid, cid))


app.state.auth_required = True


def require_desktop_bridge():
    if _desktop_bridge is None:
        raise HTTPException(status_code=503, detail="桌面文件选择器尚未就绪")
    return _desktop_bridge


@app.get("/api/health")
async def health():
    return {"status": "ok"}


class RegisterPayload(BaseModel):
    username: str = Field(..., min_length=3, max_length=40)
    password: str = Field(..., min_length=6, max_length=200)
    invite_code: str

class LoginPayload(BaseModel):
    username: str = Field(..., min_length=3, max_length=40)
    password: str = Field(..., min_length=6, max_length=200)

class ChangePwPayload(BaseModel):
    old_password: str = Field(..., min_length=6, max_length=200)
    new_password: str = Field(..., min_length=6, max_length=200)


@app.post("/api/auth/register")
@limiter.limit("10/minute")
def auth_register(request: Request, payload: RegisterPayload):
    if not secrets.compare_digest(payload.invite_code, accounts.get_config("invite_code", "")):
        raise HTTPException(status_code=403, detail="邀请码无效")
    try:
        uid = accounts.create_user(payload.username, payload.password)
    except accounts.UsernameTakenError:
        raise HTTPException(status_code=409, detail="用户名已被占用")
    ensure_user_dirs(uid)
    return {"status": "ok"}


@app.post("/api/auth/login")
@limiter.limit("10/minute")
def auth_login(request: Request, payload: LoginPayload, response: Response):
    # throttle-first（用户拍板，撞库防护优先）：先原子占槽，桶满直接 429 且**不跑 verify**——
    # 这是真封顶，攻击者每用户名每 5min 只能试 MAX 个密码、IP 轮换绕不过。代价：桶被攻击者
    # 填满时该用户正确密码也会 429（5min 自动恢复），是用户明确选择的取舍、正确行为。
    if _reserve_login_attempt(payload.username):
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请 5 分钟后再试。")
    if not accounts.verify_user_password(payload.username, payload.password):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    # 密码正确：清桶，合法用户不累积失败计数。
    _clear_login_fails(payload.username)
    rec = accounts.get_user_by_username(payload.username)
    if rec["disabled"]:
        raise HTTPException(status_code=403, detail="账号已停用")
    token = accounts.create_session(rec["uid"], ip=request.client.host if request.client else "",
                                    ua=request.headers.get("user-agent", ""))
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax",
                        secure=bool(getattr(request.app.state, "cookie_secure", False)),
                        max_age=30 * 24 * 3600, path="/")
    return {"status": "ok"}


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        accounts.delete_session(token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"status": "ok"}


@app.get("/api/auth/me")
def auth_me(request: Request, uid: str = Depends(get_current_uid)):
    from backend import metering
    used = accounts.get_usage_today(uid, metering.today_shanghai())["cost_micro_yuan"]
    cap = accounts.get_effective_daily_cap_micro(uid)
    cost_fields = {"today_cost_yuan": round(used / 1_000_000, 4),
                   "daily_cap_yuan": round(cap / 1_000_000, 4)}
    if uid == "local" and not getattr(request.app.state, "auth_required", True):
        return {"uid": "local", "username": "本地用户", "is_admin": False,
                "must_change_password": False, **cost_fields}
    rec = accounts.get_user_by_uid(uid)
    if not rec:
        raise HTTPException(status_code=401, detail="未登录")
    return {"uid": uid, "username": rec["username"], "is_admin": rec["is_admin"],
            "must_change_password": rec["must_change_password"], **cost_fields}


@app.post("/api/auth/change-password")
def auth_change_password(request: Request, payload: ChangePwPayload, uid: str = Depends(get_current_uid)):
    rec = accounts.get_user_by_uid(uid)
    if not rec:
        raise HTTPException(status_code=400, detail="当前账号不支持改密")
    if not accounts.verify_user_password(rec["username"], payload.old_password):
        raise HTTPException(status_code=401, detail="原密码错误")
    accounts.set_user_password(uid, payload.new_password)
    cur = request.cookies.get(SESSION_COOKIE)
    if cur:
        accounts.delete_other_user_sessions(uid, cur)   # keep current session, revoke others
    else:
        accounts.delete_user_sessions(uid)
    return {"status": "ok"}


@app.get("/api/admin/users")
def admin_list_users(admin_uid: str = Depends(get_current_admin)):
    from backend import metering   # 模块限定（B2 铁律，避 reload 异常身份失配）
    day = metering.today_shanghai()
    out = []
    for u in accounts.list_all_users():
        used = accounts.get_usage_today(u["uid"], day)["cost_micro_yuan"]
        cap = accounts.get_effective_daily_cap_micro(u["uid"])
        out.append({
            "uid": u["uid"], "username": u["username"],
            "is_admin": bool(u["is_admin"]), "disabled": bool(u["disabled"]),
            "created_at": u["created_at"],
            "today_cost_yuan": round(used / 1_000_000, 4),
            "daily_cap_yuan": round(cap / 1_000_000, 4),
        })
    return out


@app.get("/api/admin/invite-code")
def admin_get_invite_code(admin_uid: str = Depends(get_current_admin)):
    return {"invite_code": accounts.get_config("invite_code") or ""}


@app.get("/api/admin/allowed-hosts")
def admin_get_allowed_hosts(admin_uid: str = Depends(get_current_admin)):
    return {
        "builtin_hosts": sorted(url_guard.builtin_allowed_hosts()),   # 内置默认（只读）
        "env_hosts": sorted(url_guard.env_allowed_hosts()),           # env 注入（只读）
        "extra_hosts": accounts.get_custom_api_extra_hosts(),         # app_config（admin 可编辑）
    }


class AdminPasswordBody(BaseModel):
    new_password: str = Field(min_length=8, max_length=256)

class AdminCapBody(BaseModel):
    daily_cost_yuan: Optional[str] = None   # 字符串入参，Decimal 解析免 float 漂移；null=回退全局

_MAX_DAILY_CAP_YUAN = Decimal(1_000_000)   # 产品级日额上限（¥/天）：超此视为误填 → 400（防整数溢出 500）

class AdminDisabledBody(BaseModel):
    disabled: bool

class AllowedHostsBody(BaseModel):
    hosts: list[str] = Field(default_factory=list)


@app.post("/api/admin/users/{uid}/password")
def admin_set_password(uid: str, body: AdminPasswordBody, admin_uid: str = Depends(get_current_admin)):
    if accounts.get_user_by_uid(uid) is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    accounts.admin_reset_password(uid, body.new_password)
    return {"status": "ok"}


@app.post("/api/admin/users/{uid}/cap")
def admin_set_cap(uid: str, body: AdminCapBody, admin_uid: str = Depends(get_current_admin)):
    if accounts.get_user_by_uid(uid) is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    raw = (body.daily_cost_yuan or "").strip()
    if raw == "":
        micro = None
    else:
        # 先限输入长度：挡超长数字串（如 "9"*40），避免 Decimal 解析巨数（codex 红队 BLOCKER2）。
        if len(raw) > 32:
            raise HTTPException(status_code=400, detail="额度格式非法")
        try:
            yuan = Decimal(raw)
        except (InvalidOperation, ValueError, decimal.DecimalException):
            raise HTTPException(status_code=400, detail="额度格式非法")
        if not yuan.is_finite():   # 拒 NaN/Infinity（"1e1000000" 等过解析但乘法溢出，is_finite 也拦不住下面靠上限）
            raise HTTPException(status_code=400, detail="额度格式非法")
        if yuan < 0:
            raise HTTPException(status_code=400, detail="额度不能为负")
        # 产品级合理上限（¥1,000,000/天）：超此即视为误填，挡住 "1e308" 这类天文值（→ SQLite 整数溢出 500）。
        if yuan > _MAX_DAILY_CAP_YUAN:
            raise HTTPException(status_code=400, detail="额度过大")
        try:
            micro = int(yuan * 1_000_000)
        except (decimal.DecimalException, OverflowError, ValueError):
            raise HTTPException(status_code=400, detail="额度格式非法")
    accounts.set_user_daily_cap_micro(uid, micro)
    return {"status": "ok"}


@app.post("/api/admin/users/{uid}/disabled")
def admin_set_disabled(uid: str, body: AdminDisabledBody, admin_uid: str = Depends(get_current_admin)):
    target = accounts.get_user_by_uid(uid)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    # 防锁死自禁（非 racy：actor_uid 是调用者本身、不会变）留在端点层。
    if body.disabled and uid == admin_uid:
        raise HTTPException(status_code=400, detail="不能禁用当前登录的管理员")
    # last-admin 守卫 + 撤会话在 accounts 层单事务（BEGIN IMMEDIATE）原子完成，
    # 消除并发互禁 TOCTOU（旧的 main.py 计数检查 + 另起事务 set_user_disabled 已删）。
    try:
        accounts.admin_set_user_disabled(uid, body.disabled)
    except accounts.LastAdminError:
        raise HTTPException(status_code=400, detail="不能禁用最后一个管理员")
    return {"status": "ok"}


@app.post("/api/admin/invite-code/rotate")
def admin_rotate_invite(admin_uid: str = Depends(get_current_admin)):
    return {"invite_code": accounts.rotate_invite_code()}


@app.post("/api/admin/allowed-hosts")
def admin_set_allowed_hosts(body: AllowedHostsBody, admin_uid: str = Depends(get_current_admin)):
    cleaned = [h.strip().lower() for h in body.hosts if h and h.strip()]
    bad = [h for h in cleaned if not url_guard.is_valid_hostname(h)]   # 拒 scheme/port/path/通配符/空白
    if bad:
        raise HTTPException(status_code=400, detail=f"非法域名：{', '.join(bad)}（只填主机名，如 my.llm.cn）")
    accounts.set_custom_api_extra_hosts(cleaned)
    url_guard.set_runtime_allowed_hosts(accounts.get_custom_api_extra_hosts())  # 即时刷新，无需重启
    return {"extra_hosts": accounts.get_custom_api_extra_hosts()}


@app.get("/api/settings")
async def get_settings(uid: str = Depends(require_password_current)):
    data = load_settings(uid).model_dump(exclude={"managed_client_token"})
    data["api_key"] = "***" if data["api_key"] else ""
    data["custom_api_key"] = "***" if data.get("custom_api_key") else ""
    return data


class SettingsUpdate(BaseModel):
    """前端提交的设置更新"""

    mode: Literal["managed", "custom"]
    managed_base_url: str | None = None   # 服务端只读：接收但忽略，永远用 DEFAULT_MANAGED_BASE_URL
    managed_model: str
    managed_vision_model: Optional[str] = None
    vision_enabled: Optional[bool] = None
    custom_api_base: str = ""
    custom_api_key: str = ""
    custom_model: str = ""
    custom_context_limit_override: int | None = None


@app.post("/api/settings")
async def update_settings(update: SettingsUpdate, uid: str = Depends(require_password_current)):
    from backend import url_guard
    if (update.mode or "") == "custom":
        # 保存 custom 前即时校验 base：https + 白名单主机 + 解析到公网；不合法不落盘。
        try:
            url_guard.validate_custom_api_base(update.custom_api_base or "")
        except url_guard.SsrfBlockedError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    with _settings_lock:
        s = load_settings(uid)
        s.mode = update.mode
        # managed_base_url 服务端只读：忽略客户端值，由 normalize 强制为 DEFAULT_MANAGED_BASE_URL。
        s.managed_model = update.managed_model
        if "managed_vision_model" in update.model_fields_set and update.managed_vision_model is not None:
            s.managed_vision_model = update.managed_vision_model
        if "vision_enabled" in update.model_fields_set and update.vision_enabled is not None:
            s.vision_enabled = update.vision_enabled
        s.custom_api_base = update.custom_api_base
        if update.custom_api_key != "***":
            s.custom_api_key = update.custom_api_key
        s.custom_model = update.custom_model
        if "custom_context_limit_override" in update.model_fields_set:
            s.custom_context_limit_override = clamp_custom_context_limit_override(
                update.custom_context_limit_override
            )
        save_settings(s, uid=uid)
        for k in [k for k in _chat_handlers if k[0] == uid]:
            _chat_handlers.pop(k, None)
    return {"status": "ok"}


class ModelsRequest(BaseModel):
    """获取模型列表请求"""

    api_key: str
    api_base: str


class WorkspaceFilesRequest(BaseModel):
    workspace_dir: str


@app.post("/api/models/list")
async def list_models(request: ModelsRequest, uid: str = Depends(require_password_current)):
    from openai import OpenAI
    from backend import url_guard

    # —— 校验在宽 try 之外，400 不被吞成 500 ——
    try:
        validated_base = url_guard.validate_custom_api_base(request.api_base or "")
    except url_guard.SsrfBlockedError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    http_client = url_guard.build_guarded_http_client(timeout=30.0)
    try:
        client = OpenAI(
            api_key=request.api_key,
            base_url=validated_base,
            http_client=http_client,
        )
        models = client.models.list()
        model_ids = [m.id for m in models.data]
        return {"models": model_ids}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取模型列表失败: {str(e)}")
    finally:
        http_client.close()


@app.post("/api/system/select-workspace-folder")
async def select_workspace_folder(uid: str = Depends(require_password_current)):
    bridge = require_desktop_bridge()
    selected_path = await asyncio.to_thread(bridge.select_workspace_folder)
    return {"path": selected_path or ""}


@app.post("/api/system/select-workspace-files")
async def select_workspace_files(request: WorkspaceFilesRequest, uid: str = Depends(require_password_current)):
    bridge = require_desktop_bridge()
    selected_paths = await asyncio.to_thread(bridge.select_workspace_files, request.workspace_dir)
    return {"paths": selected_paths or []}


@app.get("/api/projects")
async def list_projects(uid: str = Depends(require_password_current)):
    return get_skill_engine(uid).list_projects()


@app.post("/api/projects")
async def create_project(info: ProjectInfo, request: Request, uid: str = Depends(require_password_current)):
    if getattr(request.app.state, "auth_required", True):   # web mode
        # 按「字段是否发送」拒绝，而非按值真假——web 客户端根本不该带这两个字段（更清晰的契约）。
        sent = info.model_fields_set
        if "workspace_dir" in sent or "initial_material_paths" in sent:
            raise HTTPException(status_code=400, detail="web 模式不接受客户端工作目录/本地材料路径")
        info = info.model_copy(update={"workspace_dir": str(user_projects_dir(uid) / uuid.uuid4().hex)})
    try:
        project = get_skill_engine(uid).create_project(info)
        return {"status": "ok", "project_id": project["id"], "project": project}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/projects/{project_id}/materials")
async def list_project_materials(scope: ProjectScope = Depends(require_project)):
    return {"materials": scope.engine.list_materials(scope.project_id)}


@app.post("/api/projects/{project_id}/materials/select-from-workspace")
async def select_materials_from_workspace(scope: ProjectScope = Depends(require_project)):
    # require_project 已校验归属（非属主 404）；之后才判桌面桥（非属主拿 404 而非 503）。
    bridge = require_desktop_bridge()
    file_paths = await asyncio.to_thread(
        bridge.select_workspace_files, scope.project_record["workspace_dir"]
    )
    if not file_paths:
        return {"materials": []}

    try:
        materials = scope.engine.add_materials(
            scope.project_id, file_paths, added_via="workspace_select"
        )
        return {"materials": materials}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/projects/{project_id}/materials/upload")
async def upload_materials(
    scope: ProjectScope = Depends(require_project), files: list[UploadFile] = File(...)
):
    limit_mb = MAX_HEAVY_MATERIAL_BYTES / (1024 * 1024)
    staged_paths = []
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        for upload in files:
            safe_name = Path(upload.filename or "attachment").name
            temp_path = tmpdir_path / safe_name
            # Stream-accumulate to enforce size limit without buffering the whole file
            chunk_size = 256 * 1024  # 256 KB chunks
            total_bytes = 0
            with temp_path.open("wb") as f:
                while True:
                    chunk = await upload.read(chunk_size)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    if total_bytes > MAX_HEAVY_MATERIAL_BYTES:
                        # Partial file: close and clean up before rejecting
                        f.close()
                        try:
                            temp_path.unlink(missing_ok=True)
                        except Exception:
                            pass
                        raise HTTPException(
                            status_code=413,
                            detail=(
                                f"文件 {safe_name!r} 大小超过上传限制 {limit_mb:.0f} MB，"
                                "请压缩后重试"
                            ),
                        )
                    f.write(chunk)
            staged_paths.append(str(temp_path))

        try:
            materials = scope.engine.add_materials(
                scope.project_id, staged_paths, added_via="chat_upload"
            )
            return {"materials": materials}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/projects/{project_id}/materials/{material_id}")
async def delete_material(material_id: str, scope: ProjectScope = Depends(require_project)):
    try:
        scope.engine.remove_material(scope.project_id, material_id)
        return {"status": "ok"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/chat")
@limiter.limit("20/minute")
async def chat(request: Request, chat_request: ChatRequest, uid: str = Depends(require_password_current)):
    # scope 解析必须在 try 外：require_project 的 404（非属主/不存在）不能被下面的
    # 宽泛 except Exception 吞成 500（破坏租户隔离 404 契约，Codex T11a review）。
    logger.info(f"Chat request for project: {chat_request.project_id}")
    scope = require_project(chat_request.project_id, uid)
    # 预检（建 handler 前）：仅 managed 模式做 ¥ 门禁——✦ Codex BLOCKER：custom 不计费/不受 ¥ 门禁（§6.4），
    # 不可因 managed 额度尽而拦 custom 聊天。B2 production 仍 managed-forced，此 gate 为正确性 + B3 预留。
    # 额度尽时直接友好返回，不建 handler、不半启动 turn（mid-turn 由 wrapper reserve 兜底）。
    # ✦ Codex NIT2：先判 mode==managed，才碰 metering/accounts——custom 路径（B3 真开放时）不该被坏
    # cap 配置 / 计费 DB 异常波及。metering import 提到 try 之前，供下面 except 引用其异常类型。
    from backend import metering
    if load_settings(scope.uid).mode == "managed":
        cap = accounts.get_effective_daily_cap_micro(scope.uid)
        used = accounts.get_usage_today(scope.uid, metering.today_shanghai())["cost_micro_yuan"]
        if used >= cap:
            # ✦ Codex BLOCKER：system_notices 是 List[SystemNotice]、非 List[str] → 置 None，提示放 content。
            return ChatResponse(
                content=(
                    f"今日额度已用尽（已用 ¥{used / 1_000_000:.2f} / 上限 ¥{cap / 1_000_000:.2f}），"
                    f"明日 0 点（北京时间）恢复。"
                ),
                token_usage=None,
                system_notices=None,
            )
    handler = get_chat_handler(scope.uid, scope.project_id)
    try:
        result = await asyncio.to_thread(
            handler.chat,
            scope.project_id,
            chat_request.message_text,
            chat_request.attached_material_ids,
            [item.model_dump() for item in chat_request.transient_attachments],
            client_message_id=chat_request.client_message_id,
        )
        token_usage = result.get("token_usage") or {}
        logger.info(f"Chat completed, tokens: {token_usage.get('context_used_tokens', 0)}")
        return ChatResponse(
            content=result["content"],
            token_usage=result.get("token_usage"),
            system_notices=result.get("system_notices"),
        )
    except (metering.QuotaExceededError, metering.ModelPausedError) as qe:
        return ChatResponse(
            content=handler._format_quota_error(qe),
            token_usage=None,
            system_notices=None,
        )
    except Exception as e:
        logger.error(f"Chat error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/projects/{project_id}/files")
async def list_files(scope: ProjectScope = Depends(require_project)):
    try:
        return {"files": scope.engine.list_workspace_files(scope.project_id)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/projects/{project_id}/files/{file_path:path}")
async def read_file(file_path: str, scope: ProjectScope = Depends(require_project)):
    try:
        normalized = scope.engine.normalize_file_path(scope.project_id, file_path)
        data = scope.engine.read_file_with_mtime(scope.project_id, file_path)
        data["editable"] = scope.engine.is_user_editable(normalized)
        return data
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class UserFileWrite(BaseModel):
    content: str
    base_mtime_ns: str  # opaque string; pydantic rejects a raw JSON number → 422


# R3: 用户保存的临界区跑在这个**专用**线程池，而不是 Starlette 的默认 anyio 线程池。
# 关键正确性约束（codex 后端 quality 审 BLOCKER）：chat_stream 是同步 generator，Starlette 用
# anyio 默认池 iterate_in_threadpool 逐 chunk 迭代它，而 `with request_lock:`（threading.RLock）
# 跨 yield 持有、owner 是某个 anyio worker 线程。若保存也用默认池（run_in_threadpool），可能复用到
# 那个 owner 线程 → RLock 重入放行 → 保存绕过锁、CAS 形同虚设。专用池线程绝不会是 chat 的 worker，
# 故保存线程 ≠ RLock owner，acquire 必真正阻塞到 chat 释放——互斥才成立。不要改回 run_in_threadpool。
_USER_WRITE_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="user-write")

# W2-C Task 7: SSE 周期心跳——app 部署在 Cloudflare 后面，CF 会丢弃空闲（~100s 无字节）的流。
# DeepSeek reasoner 可中途思考 > 100s 不出字节 → CF 杀连接。对策：两条 SSE 流在空闲时发周期
# ': keepalive' 注释帧（SSE 注释行被消费侧忽略，零语义影响）。心跳只在 HTTP SSE 帧层注入。
SSE_HEARTBEAT_INTERVAL_SECONDS = 20.0
_CHAT_STREAM_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="chat-stream")


async def _sse_with_heartbeat(sync_gen_factory, interval=None):
    """在专用线程池跑同步 SSE 生成器；空闲 > interval 秒发 ': keepalive\\n\\n'。
    心跳只在 HTTP SSE 帧层注入——不碰 chat.py 的 provider/tool-call/DeepSeek 逻辑、不碰 request lock。

    断连正确性：finally 置 stop_event；pump 每轮**先判 stop 再 next(gen)**（不在断连后多推进一次
    进新 provider/tool 阶段），随后 `gen.close()` 在 generator 当前 yield 点抛 GeneratorExit →
    chat_stream 的 `with request_lock` finally 释放锁。**注意**：`gen.close()` 只能在 generator 挂起于
    yield、或当前 `next()` 返回后生效，**无法中断已在途的 provider 调用**——故锁释放 / 停止烧 token 发生
    在「当前阶段返回之后」，不是瞬时（与现状 Starlette 断连关闭同步 generator 行为一致）。
    队列无界：体量受**单轮输出量 / app 的 token 限额**界定（非队列界定，慢客户端会让一条流缓存整轮输出）；
    不用有界阻塞 put——断连后消费侧停抽、阻塞 put 会与 stop 死锁、pump 到不了 gen.close()、锁反泄漏。"""
    if interval is None:
        interval = SSE_HEARTBEAT_INTERVAL_SECONDS
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    DONE = object()
    stop_event = threading.Event()

    def pump():
        gen = None
        try:
            gen = sync_gen_factory()
            while not stop_event.is_set():           # 先判 stop 再推进
                try:
                    item = next(gen)
                except StopIteration:
                    break
                if stop_event.is_set():
                    break
                try:
                    loop.call_soon_threadsafe(queue.put_nowait, item)
                except RuntimeError:
                    break  # loop 已关（消费侧消失）→ 停推进，finally 仍 gen.close() 释放锁
        finally:
            if gen is not None:
                gen.close()  # 断连：GeneratorExit 抵达当前 yield → chat_stream finally 释放 request lock
            try:
                loop.call_soon_threadsafe(queue.put_nowait, DONE)
            except RuntimeError:
                pass  # loop 已关，消费侧已走，无需再发 DONE

    def _log_pump_exception(f):
        # pump 异常被 future 捕获——主动取回并记日志（不再静默吞掉：loop-close 竞态等
        # wrapper 级故障否则会变成无 [DONE] 的干净 EOF，难排查）。
        exc = f.exception()
        if exc is not None:
            logger.warning("SSE heartbeat pump raised: %s", exc, exc_info=exc)

    fut = loop.run_in_executor(_CHAT_STREAM_EXECUTOR, pump)
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=interval)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            if item is DONE:
                break
            yield item
    finally:
        stop_event.set()   # 断连/正常结束：令 pump 在下个 item 后停（不排空整轮、不漏锁）
        if fut.done():
            _log_pump_exception(fut)
        else:
            fut.add_done_callback(_log_pump_exception)   # 后台收尾、不阻塞清理


@app.post("/api/projects/{project_id}/files/{file_path:path}")
async def write_user_file(
    file_path: str,
    payload: UserFileWrite,
    scope: ProjectScope = Depends(require_project),
):
    # require_project 已校验归属（非属主 404）；项目存在性也由它兜底。
    handler = get_chat_handler(scope.uid, scope.project_id)
    request_lock = handler._get_project_request_lock(scope.project_id)

    def _write_under_lock():
        # 全段持与聊天同一把锁：CAS(stat) → os.replace 必须对 AI 写入原子互斥。
        # 跑在专用池线程（见 _USER_WRITE_EXECUTOR 注释）：锁阻塞落在该线程、不阻塞事件循环，
        # 且该线程绝不是 chat_stream 的 anyio worker，杜绝 RLock 重入绕过。
        with request_lock:
            new_mtime = scope.engine.user_write_file(
                scope.project_id, file_path, payload.content, payload.base_mtime_ns
            )
            return {"status": "ok", "mtime_ns": new_mtime}

    try:
        return await asyncio.get_running_loop().run_in_executor(
            _USER_WRITE_EXECUTOR, _write_under_lock
        )
    except UserWriteForbiddenError:
        raise HTTPException(status_code=403, detail="该文件不可编辑")
    except StaleFileError:
        raise HTTPException(
            status_code=409,
            detail="文件已被更新（可能是 AI 刚写过），请重新加载后再编辑",
        )
    except FileNotFoundError:
        # OSError 子类，必须排在下面 except OSError 之前
        raise HTTPException(status_code=404, detail="文件不存在")
    except ValueError:
        # 剩余 ValueError = 路径穿越（非法的文件路径）
        raise HTTPException(status_code=400, detail="非法的文件路径")
    except OSError:
        # os.replace/write_text 失败（Windows 上文件被外部编辑器/同步盘/杀毒占用等）——
        # 该文件可编辑、只是临时写不进，给可重试提示，别误报成 403「不可编辑」。
        raise HTTPException(
            status_code=500,
            detail="文件写入失败（可能被外部程序占用），请关闭后重试",
        )


@app.get("/api/projects/{project_id}/workspace")
async def get_workspace(scope: ProjectScope = Depends(require_project)):
    try:
        return scope.engine.get_workspace_summary(scope.project_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/projects/{project_id}/independent-review/stream")
async def independent_review_stream_post(
    request: Request, scope: ProjectScope = Depends(require_project)
):
    """POST stream with frontend-stable run_id + resume/discard support.

    C5 cutover: the legacy GET endpoint was deleted and the front end now drives this POST
    contract exclusively. Body: {resume, run_id, supplement?}.
    Hard invariants (codex R2): the review lock is released on EVERY path (acquire fail,
    claim_first CAS fail, resume done/reject, worker finally); resume waits for the lock via
    to_thread (non-blocking the event loop) then re-reads the store; review-completed is
    re-emitted by this wrapper only after the worker finished AND the lock was released AND a
    done tombstone exists.
    """
    body = await request.json()
    resume = bool(body.get("resume"))
    run_id = body.get("run_id")
    supplement = body.get("supplement")
    if not run_id:
        raise HTTPException(status_code=400, detail="run_id required")

    # W2-B 多租户（T11b）：lock/store 用复合键，engine/agent.run 用 canonical project id。
    # 严格区分——混用是本任务要避免的 bug。
    review_key = scope.lock_key          # composite key for lock + store
    review_project_id = scope.project_id  # canonical id for engine / agent.run

    try:
        workspace = scope.engine.get_workspace_summary(review_project_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if workspace.get("stage_code") != "S5":
        raise HTTPException(status_code=400, detail="独立审查只能在 S5 阶段使用")

    lock = get_independent_review_lock(review_key)
    store = _REVIEW_SESSION_STORE
    cancel_event = threading.Event()
    resume_snapshot = None
    done_mtime = None

    if not resume:
        if not lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="上一次独立审查仍在进行中，请等待")
        if not store.claim_first(review_key, run_id, cancel_event):
            lock.release()  # CAS 失败必须 release（红队：防 lock 泄漏）
            raise HTTPException(status_code=409, detail="已有进行中的审查")
    else:
        # 短 blocking 等锁，不阻塞事件循环：worker 可能正在收尾（release 在即）。
        got = await asyncio.to_thread(lock.acquire, True, 3.0)
        if not got:
            raise HTTPException(status_code=409, detail="上一次审查正在收尾，请稍候")
        # 拿到锁后重读 store（等锁期间 worker 可能已 atomic_commit 收尾、状态翻 done）。
        kind, payload = store.claim_resume(review_key, run_id, cancel_event)
        if kind == "errored":
            resume_snapshot = payload
        elif kind == "done":
            done_mtime = payload
            lock.release()  # done 不启 worker → 必须释放（lock 全路径）
        else:  # reject
            lock.release()
            raise HTTPException(status_code=400, detail="无可续审的会话")

    # Worker + review-lock release are created HERE (function body), NOT inside generate(), so the
    # lock is released even if generate() never runs. Starlette StreamingResponse.__call__ runs
    # stream_response and listen_for_disconnect concurrently in a task group and cancels the group as
    # soon as either finishes (starlette/responses.py:249-257). If the client already disconnected,
    # the disconnect listener can win before stream_response is scheduled, so generate()'s body —
    # which used to create the worker — may never execute, leaking the review lock forever (that
    # project's review then 409s until process restart; discard only clears the store, not the lock).
    # Creating the worker in the function body guarantees run_worker's finally releases the lock
    # regardless of whether generate() is ever consumed (codex C5 red-team B3).
    event_queue = None
    worker_task = None
    if done_mtime is None:
        event_queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def enqueue_event(event):
            if loop.is_closed():
                return
            future = asyncio.run_coroutine_threadsafe(event_queue.put(event), loop)
            future.result()

        def run_worker():
            try:
                agent = IndependentReviewAgent(scope.engine, load_settings(scope.uid), uid=scope.uid)
                for event in agent.run(
                    review_project_id,
                    run_id=run_id,
                    store=store,
                    resume_snapshot=resume_snapshot,
                    supplement=supplement,
                    cancel_event=cancel_event,
                    store_key=review_key,
                ):
                    if cancel_event.is_set():
                        break
                    # 不透传 agent 内部的 review-completed：完成信号由 endpoint wrapper 在 lock
                    # 释放后按 store.get_done_mtime 重新发射，保证前端见 completion 时锁已可用。
                    if isinstance(event, dict) and event.get("type") == "review-completed":
                        continue
                    enqueue_event(event)
            except Exception as exc:
                if not cancel_event.is_set():
                    enqueue_event({"type": "error", "data": str(exc)})
            finally:
                try:
                    # worker 退出兜底（codex R2 BLOCKER 4）：record 仍 running 则收敛——续审场景留
                    # snapshot 可再 resume，无则清 record。done/errored/被 discard → no-op。
                    # 先收敛 store，再释放 review lock。
                    store.finalize_orphan_running(review_key, run_id, resume_snapshot)
                finally:
                    try:
                        enqueue_event(None)
                    finally:
                        lock.release()

        worker_task = asyncio.create_task(asyncio.to_thread(run_worker))

    async def generate():
        # 统一 completion 收尾（codex C4-quality BLOCKER）：done 短路分支与 worker 分支共用同一套
        # completion 时序，消除两分支不一致——发 review-completed 前永远 ① 检查 is_disconnected
        # （断连 → 不发 completed、不发 [DONE]）；② 重新 store.get_done_mtime 重读 tombstone（truthy
        # 才发，带重读到的 mtime，绝不用任何缓存值——防 release lock 后被并发 discard / 新 first-run
        # 覆盖 tombstone 仍发 stale completed）；③ 未断连才发 [DONE]。此刻 review lock 必已释放
        # （done 分支在 claim_resume 后即 release；worker 分支在 run_worker finally release）。
        async def emit_completion():
            if await request.is_disconnected():
                return
            final_mtime = store.get_done_mtime(review_key, run_id)
            if final_mtime:
                yield (
                    "data: "
                    + json.dumps(
                        {
                            "type": "review-completed",
                            "run_id": run_id,
                            "report_mtime_ns": final_mtime,
                        },
                        ensure_ascii=False,
                    )
                    + "\n\n"
                )
            yield "data: [DONE]\n\n"

        # done 分支：worker 不启动（resume 命中已落档的 done tombstone），直接走统一 completion。
        if done_mtime is not None:
            async for chunk in emit_completion():
                yield chunk
            return

        # worker + review-lock release 已在函数体创建（见上方 B3 注释）；generate 只消费 event_queue。
        # worker_task / event_queue 是函数体闭包变量（done 分支已在上方 return，到这里必为非 None）。
        last_emit = time.monotonic()  # W2-C Task 7: 心跳计时锚点（每发任何帧都重置）
        try:
            while True:
                if await request.is_disconnected():
                    cancel_event.set()
                    break
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    # 空闲（审查 agent 仍在思考/无事件）：超 interval 发心跳，防 CF 切空闲流。
                    if time.monotonic() - last_emit >= SSE_HEARTBEAT_INTERVAL_SECONDS:
                        last_emit = time.monotonic()
                        yield ": keepalive\n\n"
                    continue
                if event is None:
                    break
                last_emit = time.monotonic()
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:
            cancel_event.set()
            yield f"data: {json.dumps({'type': 'error', 'data': str(exc)}, ensure_ascii=False)}\n\n"
        finally:
            cancel_event.set()
            if not worker_task.done():
                await worker_task

        # worker 已结束 + lock 已在 run_worker finally 释放 → 走统一 completion（重读 tombstone +
        # disconnect guard 都在 emit_completion 内）。worker error / atomic replace 失败 / 自修失败 /
        # 断连 → emit_completion 重读 get_done_mtime 为空 / is_disconnected → 不发 completed。
        async for chunk in emit_completion():
            yield chunk

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/projects/{project_id}/independent-review/discard")
async def independent_review_discard(
    request: Request, scope: ProjectScope = Depends(require_project)
):
    """C4: cancel an in-flight (or finished) review session without acquiring the review lock.

    Uses only the store guard so it can cancel even while a long-running worker holds the
    review lock. run_id must match the current record; on match it sets the cancel_event and
    drops the record (any late commit from the old worker is then rejected by run_id mismatch).
    This only cancels the session — it never deletes an already-written report.
    """
    body = await request.json()
    run_id = body.get("run_id")
    if not run_id:
        raise HTTPException(status_code=400, detail="run_id required")
    cancelled = _REVIEW_SESSION_STORE.discard(scope.lock_key, run_id)
    return {"cancelled": cancelled}


@app.post("/api/projects/{project_id}/export-draft")
def export_draft(scope: ProjectScope = Depends(require_project)):
    # 同步 def 路由：FastAPI 在线程池执行，pandoc 阻塞子进程不卡事件循环（spec §3.2）。
    # 导出不取 per-project request lock（spec §3.6：chat_stream 整轮持 RLock，R3 原子写保证锁外读安全）。
    try:
        report_path = scope.engine.get_primary_report_path(scope.project_id)
        output_dir = scope.engine.ensure_output_dir(scope.project_id)
        return export_reviewable_draft(report_path, output_dir)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


_EXPORT_DOWNLOAD_FILENAME = "report_draft_v1.docx"


@app.get("/api/projects/{project_id}/export-draft/download")
def export_draft_download(scope: ProjectScope = Depends(require_project)):
    # 只服务确定文件名（不接受客户端任意 filename）；解析后校验仍在该项目 output 目录内。
    output_dir = Path(scope.engine.ensure_output_dir(scope.project_id)).resolve()
    target = (output_dir / _EXPORT_DOWNLOAD_FILENAME).resolve()
    if output_dir not in target.parents or not target.is_file():
        raise HTTPException(status_code=404, detail="尚未生成可审草稿，请先导出。")
    return FileResponse(
        path=str(target),
        filename=_EXPORT_DOWNLOAD_FILENAME,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@app.delete("/api/projects/{project_id}")
async def delete_project(scope: ProjectScope = Depends(require_project)):
    try:
        scope.engine.delete_project(scope.project_id)
        with _settings_lock:
            _chat_handlers.pop((scope.uid, scope.project_id), None)
        return {"status": "ok"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除失败: {str(e)}")


_CHECKPOINT_ROUTES = {
    "s0-interview-done": "s0_interview_done_at",
    "outline-confirmed": "outline_confirmed_at",
    "review-started": "review_started_at",
    "review-passed": "review_passed_at",
    "presentation-ready": "presentation_ready_at",
    "delivery-archived": "delivery_archived_at",
}


@app.post("/api/projects/{project_id}/checkpoints/{name}")
async def set_checkpoint(
    name: str, action: str = "set", scope: ProjectScope = Depends(require_project)
):
    key = _CHECKPOINT_ROUTES.get(name)
    if key is None:
        raise HTTPException(status_code=404, detail=f"未知 checkpoint: {name}")
    if action not in ("set", "clear"):
        raise HTTPException(status_code=400, detail=f"未知 action: {action}")
    if key == "s0_interview_done_at" and action == "set":
        raise HTTPException(
            status_code=400,
            detail=(
                "s0_interview_done_at 不能通过 endpoint 直接 set："
                "endpoint 层无对话上下文，无法执行 S0 对话级软门槛。"
                "set 只能走 advance_stage / schema migration。"
            ),
        )
    try:
        return scope.engine.record_stage_checkpoint(scope.project_id, key, action)
    except ValueError as exc:
        detail = str(exc)
        status = 404 if "项目不存在" in detail else 400
        raise HTTPException(status_code=status, detail=detail)


@app.get("/api/projects/{project_id}/conversation")
async def get_conversation(scope: ProjectScope = Depends(require_project)):
    project_path = scope.engine.get_project_path(scope.project_id)
    if not project_path:
        raise HTTPException(status_code=404, detail="项目不存在")
    conv_file = project_path / "conversation.json"
    if not conv_file.exists():
        return {"messages": []}
    with open(conv_file, "r", encoding="utf-8") as f:
        messages = json.load(f)

    # v5: sanitize 历史 fallback assistant，避免旧占位气泡重新展示。
    sanitized = []
    for m in messages:
        if m.get("role") == "assistant":
            raw = m.get("content") or ""
            if raw.strip() in LEGACY_EMPTY_ASSISTANT_FALLBACKS:
                continue
            cleaned = strip_tool_log_comments(_strip_legacy_stage_ack(raw))
            raw_events = m.get("tool_events") if isinstance(m.get("tool_events"), list) else []
            tool_events = [
                {
                    "tool": str(e.get("tool") or ""),
                    "arg": str(e.get("arg") or ""),
                    "status": e.get("status") if e.get("status") in ("success", "error", "pending") else "success",
                    "summary": str(e.get("summary") or ""),
                    "id": f"reload-{i}",
                }
                for i, e in enumerate(raw_events)
                if isinstance(e, dict) and e.get("tool")
            ]
            sanitized.append({**m, "content": cleaned, "tool_events": tool_events})
        else:
            sanitized.append(m)
    return {"messages": sanitized}


@app.delete("/api/projects/{project_id}/conversation")
def clear_conversation(scope: ProjectScope = Depends(require_project)):
    # 同步 def 路由：FastAPI 在线程池执行，`with request_lock:` 的阻塞等待落在 worker 线程、
    # 绝不卡事件循环（否则一次「清空对话」在聊天长 provider 调用持锁期间会冻结 loop → SSE 心跳
    # 发不出 → CF 断流 + 单 worker 全员 stall）。与导出端点同款离 loop 策略（W2-C 终审 BLOCKER）。
    # 该 worker 不是 _CHAT_STREAM_EXECUTOR 的 chat 生成器线程 → RLock 真阻塞、无重入绕过。
    project_path = scope.engine.get_project_path(scope.project_id)
    if not project_path:
        raise HTTPException(status_code=404, detail="项目不存在")
    handler = get_chat_handler(scope.uid, scope.project_id)
    request_lock = handler._get_project_request_lock(scope.project_id)
    with request_lock:
        for file_name in (
            "conversation.json",
            "conversation_state.json",
            "conversation_compact_state.json",
        ):
            target_path = project_path / file_name
            if target_path.exists():
                target_path.unlink()
    return {"status": "ok"}


@app.post("/api/chat/stream")
@limiter.limit("20/minute")
def chat_stream(request: Request, chat_request: ChatRequest, uid: str = Depends(require_password_current)):
    # 归属校验在函数体（StreamingResponse 之前）：非属主 → require_project 抛 404，
    # 而不是把 404 埋进 SSE 流里。scope 解析失败直接以 HTTP 错误冒泡。
    scope = require_project(chat_request.project_id, uid)

    def generate():
        try:
            handler = get_chat_handler(scope.uid, scope.project_id)
            # C5: thread run-bound trigger metadata end-to-end so the main agent can bind a
            # review report to the exact run that produced it (run_id/report_mtime_ns stay
            # opaque strings — pydantic already rejects raw ints; never coerce to Number).
            trigger_metadata = {
                "run_id": chat_request.run_id,
                "report_mtime_ns": chat_request.report_mtime_ns,
            }
            for chunk in handler.chat_stream(
                scope.project_id,
                chat_request.message_text,
                chat_request.attached_material_ids,
                [item.model_dump() for item in chat_request.transient_attachments],
                system_trigger=chat_request.system_trigger,
                trigger_metadata=trigger_metadata,
                client_message_id=chat_request.client_message_id,
            ):
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'data': str(e)}, ensure_ascii=False)}\n\n"

    # W2-C Task 7: 传 generate 函数本身（工厂），不是 generate()——多路复用包装在专用线程跑同步
    # generator、空闲发心跳。无前导心跳：快路径（mock/短轮）输出与原行为字节一致，心跳只在真长空闲出现。
    # 断连时 wrapper 的 gen.close() 让 GeneratorExit 抵达当前 yield → handler.chat_stream 的
    # `with request_lock` finally 释放 per-project 锁（正确性见 _sse_with_heartbeat docstring）。
    return StreamingResponse(
        _sse_with_heartbeat(generate),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


class _SPAStaticFiles(StaticFiles):
    """SPA 静态服务 + 正确缓存头。

    StaticFiles 默认只发 ETag/Last-Modified、**不发 Cache-Control** → 浏览器对 index.html
    做启发式缓存，重新部署后陈旧 shell 仍指向旧 hash bundle；旧 bundle 被原子 swap 删除后
    返回 404 → React 脚本加载失败 → 空 #root → 满屏深色空白页（控制台静默 404、UI 无报错）。
    （2026-06-23 调试定位。）

    修复（判定基于请求 path——当前 Vite dist 只有根 index.html，无嵌套 index.html / 深链回退）：
    - 根目录请求 + 显式 `*.html` 请求 → `no-cache, must-revalidate`：每次带条件请求校验，
      确保始终拿到最新 bundle 引用（ETag 命中走 304、不重传，开销极小）。
    - 带内容 hash 的 `assets/*` → `immutable` 长缓存：hash 变即换 URL，永不陈旧。

    缓存头**按规范化路径**判定（不仅看 content-type）——因为条件请求命中时 StaticFiles 返回
    `NotModifiedResponse`(304)，它不带 content-type，只按 content-type 判会漏掉 304 → 旧缓存
    学不到 no-cache（codex BLOCKER）。按 path 判则 200 与 304 都覆盖。
    """

    @staticmethod
    def _cache_control_for(path):
        # Starlette 的 path：根目录请求为 "."；Windows 下分隔符可能是 "\\" → 先规范化。
        norm = path.replace("\\", "/").strip("/")
        if norm in ("", "."):                       # 根目录请求 → 渲染 index.html
            return "no-cache, must-revalidate"
        base = norm.rsplit("/", 1)[-1]
        if base.lower().endswith(".html"):          # 显式 .html 请求（大小写不敏感）
            return "no-cache, must-revalidate"
        if norm.split("/", 1)[0] == "assets":       # 仅根级 assets/（按路径组件，非子串误命中）
            return "public, max-age=31536000, immutable"
        return None

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        cache_control = self._cache_control_for(path)
        if cache_control:
            # 200 FileResponse 与 304 NotModifiedResponse 都是带可变 headers 的 Response，
            # 设头不该失败；若失败是真回归，不静默吞（codex NIT：去掉宽 except）。
            response.headers["Cache-Control"] = cache_control
        return response


frontend_dist = get_base_path() / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", _SPAStaticFiles(directory=str(frontend_dist), html=True), name="static")


def start_server():
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="error")


if __name__ == "__main__":
    start_server()
