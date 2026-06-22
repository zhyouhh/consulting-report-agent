from dataclasses import dataclass
from pydantic_settings import BaseSettings
from pydantic import Field
from pathlib import Path
import json
import os
import sys

DEFAULT_MANAGED_BASE_URL = "https://newapi.z0y0h.work/client/v1"
DEFAULT_MANAGED_MODEL = "deepseek-v4-pro"
DEFAULT_MANAGED_VISION_MODEL = "Qwen/Qwen3-VL-8B-Instruct"
# 每模型单价（元/百万 token）：(命中, 未命中, 输出)。spec §6.1 上机实测口径。
# vision 单价占位（按 deepseek 同档保守，可后填真实价）。
DEFAULT_MANAGED_MODEL_PRICING: dict[str, tuple[float, float, float]] = {
    "deepseek-v4-pro": (0.025, 3.0, 6.0),
    "Qwen/Qwen3-VL-8B-Instruct": (0.025, 3.0, 6.0),
}
# 未知模型 fallback 单价（保守按 deepseek 三档）
FALLBACK_MODEL_PRICING: tuple[float, float, float] = (0.025, 3.0, 6.0)
# 全局默认日配额：¥5/天 = 5_000_000 微元
DEFAULT_GLOBAL_DAILY_CAP_MICRO_YUAN: int = 5_000_000
# fail-closed：连续 N 次 usage 缺失 → 暂停该 (uid, model)
MAX_CONSECUTIVE_USAGE_MISS = 3
# fail-closed 保守封顶上下文上限（token）。✦ Codex BLOCKER：视觉模型不在 context_policy 的
# EXACT_MODEL_TIERS → 会落 UNKNOWN_FALLBACK_TIER（非「该模型上下文上限」）→ 显式给视觉模型定锚；
# 其余模型 _settle 回落 resolve_context_policy(model).effective_context_limit。
MANAGED_FAILCLOSED_CEILING: dict[str, int] = {
    "Qwen/Qwen3-VL-8B-Instruct": 32768,   # Qwen3-VL 基础上下文；× p_miss 作视觉调用 fail-closed 上界
}
DEFAULT_MANAGED_SEARCH_API_URL = "https://search.z0y0h.work/search"
DEFAULT_MANAGED_CLIENT_TOKEN = "managed"
MANAGED_CLIENT_TOKEN_FILENAME = "managed_client_token.txt"
MANAGED_SEARCH_POOL_FILENAME = "managed_search_pool.json"
SEARCH_RUNTIME_STATE_FILENAME = "search_runtime_state.json"
SEARCH_CACHE_FILENAME = "search_cache.json"
DESKTOP_CONFIG_VERSION = 4


def get_base_path() -> Path:
    """获取基础路径（兼容PyInstaller打包）"""
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS)
    return Path(__file__).parent.parent


def get_user_config_dir() -> Path:
    """获取用户配置目录"""
    config_dir = Path.home() / ".consulting-report"
    config_dir.mkdir(exist_ok=True)
    return config_dir


def get_managed_client_token_path(base_path: Path | None = None) -> Path:
    runtime_base = base_path or get_base_path()
    return runtime_base / MANAGED_CLIENT_TOKEN_FILENAME


def get_managed_search_pool_path(base_path: Path | None = None) -> Path:
    runtime_base = base_path or get_base_path()
    return runtime_base / MANAGED_SEARCH_POOL_FILENAME


def data_root() -> Path:
    env = os.environ.get("CRA_DATA_ROOT")
    root = Path(env).expanduser() if env else get_user_config_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_search_runtime_state_path(config_dir: Path | None = None) -> Path:
    return (config_dir or data_root()) / SEARCH_RUNTIME_STATE_FILENAME


def get_search_cache_path(config_dir: Path | None = None) -> Path:
    return (config_dir or data_root()) / SEARCH_CACHE_FILENAME


def get_default_managed_client_token(base_path: Path | None = None) -> str:
    env_token = os.getenv("CONSULTING_REPORT_MANAGED_CLIENT_TOKEN", "").strip()
    if env_token:
        return env_token

    token_path = get_managed_client_token_path(base_path)
    if token_path.exists():
        token = token_path.read_text(encoding="utf-8").strip().lstrip("\ufeff")
        if token:
            return token

    return DEFAULT_MANAGED_CLIENT_TOKEN


@dataclass(frozen=True)
class ManagedSearchProviderConfig:
    enabled: bool
    api_key: str
    weight: int
    minute_limit: int
    daily_soft_limit: int
    cooldown_seconds: int
    api_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # 兼容单 key（api_key）与多 key 轮询（api_keys）：两者互相回填，
        # api_key 恒为首个有效 key，api_keys 恒为去空后的元组。
        keys = tuple(k for k in self.api_keys if k)
        if not keys and self.api_key:
            keys = (self.api_key,)
        object.__setattr__(self, "api_keys", keys)
        if not self.api_key and keys:
            object.__setattr__(self, "api_key", keys[0])


@dataclass(frozen=True)
class ManagedSearchRoutingConfig:
    primary: list[str]
    secondary: list[str]
    native_fallback: bool


@dataclass(frozen=True)
class ManagedSearchLimitsConfig:
    per_turn_searches: int
    project_minute_limit: int
    global_minute_limit: int
    memory_cache_ttl_seconds: int
    project_cache_ttl_seconds: int


@dataclass(frozen=True)
class ManagedSearchPoolConfig:
    version: int
    providers: dict[str, ManagedSearchProviderConfig]
    routing: ManagedSearchRoutingConfig
    limits: ManagedSearchLimitsConfig


def _require_int(payload: dict, key: str, *, minimum: int = 1) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"managed_search_pool.json 缺少有效整数配置 {key}")
    return value


def _require_bool(payload: dict, key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"managed_search_pool.json 缺少有效布尔配置 {key}")
    return value


def _parse_provider_api_keys(name: str, payload: dict) -> tuple[str, ...]:
    """支持 `api_keys`（多账号轮询）与 `api_key`（单账号）两种写法。

    `api_keys` 优先；两者都缺省则返回空元组（enabled provider 上游会报错）。
    """
    raw_keys = payload.get("api_keys")
    if raw_keys is not None:
        if not isinstance(raw_keys, list):
            raise ValueError(f"managed_search_pool.json 中 {name}.api_keys 必须是列表")
        return tuple(str(key).strip() for key in raw_keys if str(key).strip())
    single = str(payload.get("api_key", "")).strip()
    return (single,) if single else ()


def _require_provider_entry(name: str, payload: dict) -> ManagedSearchProviderConfig:
    if not isinstance(payload, dict):
        raise ValueError(f"managed_search_pool.json 中 {name} 配置格式不正确")
    enabled = payload.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError(f"managed_search_pool.json 中 {name}.enabled 必须是 boolean")
    api_keys = _parse_provider_api_keys(name, payload)
    if enabled and not api_keys:
        raise ValueError(f"managed_search_pool.json 中 {name} 缺少 api_key")
    return ManagedSearchProviderConfig(
        enabled=enabled,
        api_key=api_keys[0] if api_keys else "",
        api_keys=api_keys,
        weight=_require_int(payload, "weight"),
        minute_limit=_require_int(payload, "minute_limit"),
        daily_soft_limit=_require_int(payload, "daily_soft_limit"),
        cooldown_seconds=_require_int(payload, "cooldown_seconds"),
    )


def _load_json_text_file(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} 必须是 JSON object")
    return payload


def load_managed_search_pool_config_from_path(config_path: Path) -> ManagedSearchPoolConfig:
    payload = _load_json_text_file(config_path)

    provider_payloads = payload.get("providers")
    if not isinstance(provider_payloads, dict) or not provider_payloads:
        raise ValueError("managed_search_pool.json 缺少 providers 配置")
    providers = {
        name: _require_provider_entry(name, provider_payloads[name])
        for name in provider_payloads
    }

    routing_payload = payload.get("routing")
    if not isinstance(routing_payload, dict):
        raise ValueError("managed_search_pool.json 缺少 routing 配置")

    def _validate_routing_names(names: list[str], *, field_name: str) -> list[str]:
        if not isinstance(names, list) or not names:
            raise ValueError(f"managed_search_pool.json 缺少有效 routing.{field_name}")
        for name in names:
            if name not in providers:
                raise ValueError(f"managed_search_pool.json 中 routing.{field_name} 引用了未知 provider: {name}")
            if not providers[name].enabled:
                raise ValueError(f"managed_search_pool.json 中 routing.{field_name} 引用了未启用 provider: {name}")
        return names

    primary = _validate_routing_names(routing_payload.get("primary"), field_name="primary")
    secondary_value = routing_payload.get("secondary", [])
    if not isinstance(secondary_value, list):
        raise ValueError("managed_search_pool.json 中 routing.secondary 必须是列表")
    for name in secondary_value:
        if name not in providers:
            raise ValueError(f"managed_search_pool.json 中 routing.secondary 引用了未知 provider: {name}")
    routing = ManagedSearchRoutingConfig(
        primary=primary,
        secondary=secondary_value,
        native_fallback=_require_bool(routing_payload, "native_fallback"),
    )

    limits_payload = payload.get("limits")
    if not isinstance(limits_payload, dict):
        raise ValueError("managed_search_pool.json 缺少 limits 配置")
    limits = ManagedSearchLimitsConfig(
        per_turn_searches=_require_int(limits_payload, "per_turn_searches"),
        project_minute_limit=_require_int(limits_payload, "project_minute_limit"),
        global_minute_limit=_require_int(limits_payload, "global_minute_limit"),
        memory_cache_ttl_seconds=_require_int(limits_payload, "memory_cache_ttl_seconds"),
        project_cache_ttl_seconds=_require_int(limits_payload, "project_cache_ttl_seconds"),
    )

    version = payload.get("version", 1)
    if not isinstance(version, int) or version < 1:
        raise ValueError("managed_search_pool.json 缺少有效 version")

    return ManagedSearchPoolConfig(
        version=version,
        providers=providers,
        routing=routing,
        limits=limits,
    )


def load_managed_search_pool_config(base_path: Path | None = None) -> ManagedSearchPoolConfig:
    config_path = get_managed_search_pool_path(base_path)
    return load_managed_search_pool_config_from_path(config_path)


class Settings(BaseSettings):
    """应用配置"""

    config_version: int = DESKTOP_CONFIG_VERSION

    # 连接模式
    mode: str = "managed"

    # 默认托管通道
    managed_base_url: str = DEFAULT_MANAGED_BASE_URL
    managed_model: str = DEFAULT_MANAGED_MODEL
    managed_vision_model: str = DEFAULT_MANAGED_VISION_MODEL
    vision_enabled: bool = True
    managed_search_api_url: str = DEFAULT_MANAGED_SEARCH_API_URL
    managed_client_token: str = Field(default_factory=get_default_managed_client_token)

    # 自定义API配置
    custom_api_key: str = ""
    custom_api_base: str = ""
    custom_model: str = ""
    custom_context_limit_override: int | None = None

    # 兼容旧代码的别名字段
    api_provider: str = "siliconflow"
    api_key: str = ""
    api_base: str = ""
    model: str = ""

    # 项目路径
    projects_dir: Path = get_user_config_dir() / "projects"
    skill_dir: Path = get_base_path() / "skill"

    # 上下文管理配置
    context_window: int = 128000       # 模型上下文窗口大小
    compress_threshold: int = 60000    # 压缩触发阈值（tokens）
    keep_recent_messages: int = 6      # 压缩时保留最近N条消息

    # 服务配置
    host: str = "127.0.0.1"
    port: int = 8080

    class Config:
        env_file = ".env"

    def model_post_init(self, __context) -> None:
        if self.mode != "managed":
            return
        if not self.api_base:
            self.api_base = self.managed_base_url
        if not self.model:
            self.model = self.managed_model
        if not self.api_key:
            self.api_key = self.managed_client_token


def normalize_settings_payload(data: dict) -> dict:
    """兼容旧配置，并同步当前模式对应的运行时字段。"""
    normalized = dict(data)
    config_version = int(normalized.get("config_version", 0) or 0)
    is_legacy_config = config_version < DESKTOP_CONFIG_VERSION
    runtime_projects_dir = get_user_config_dir() / "projects"
    runtime_skill_dir = get_base_path() / "skill"
    runtime_managed_token = get_default_managed_client_token()

    normalized["config_version"] = DESKTOP_CONFIG_VERSION

    if "mode" not in normalized:
        normalized["mode"] = "managed"

    normalized["managed_base_url"] = DEFAULT_MANAGED_BASE_URL   # 服务端只读，覆盖任何历史/客户端值
    normalized.setdefault("managed_model", DEFAULT_MANAGED_MODEL)
    normalized.setdefault("managed_vision_model", DEFAULT_MANAGED_VISION_MODEL)
    normalized.setdefault("vision_enabled", True)
    normalized.setdefault("managed_search_api_url", DEFAULT_MANAGED_SEARCH_API_URL)
    normalized["managed_client_token"] = runtime_managed_token
    normalized.setdefault("custom_api_base", normalized.get("api_base", ""))
    normalized.setdefault("custom_api_key", normalized.get("api_key", ""))
    normalized.setdefault("custom_model", normalized.get("model", ""))
    normalized.setdefault("custom_context_limit_override", None)
    normalized["projects_dir"] = runtime_projects_dir
    normalized["skill_dir"] = runtime_skill_dir

    # 桌面默认 managed；legacy 配置强制迁移到 managed（迁移安全）；
    # 非 legacy 配置 honor 用户选择的 mode（custom 已激活，B3）。
    if is_legacy_config:
        normalized["mode"] = "managed"
    else:
        requested = normalized.get("mode", "managed")
        normalized["mode"] = requested if requested in ("managed", "custom") else "managed"

    if normalized["mode"] == "managed":
        normalized["api_base"] = normalized["managed_base_url"]
        normalized["model"] = normalized["managed_model"]
        normalized["api_key"] = normalized["managed_client_token"]
    else:
        normalized["api_base"] = normalized["custom_api_base"]
        normalized["model"] = normalized["custom_model"]
        normalized["api_key"] = normalized["custom_api_key"]

    return normalized


def _config_path_for(uid: str | None) -> Path:
    """uid=None → 旧全局位置（向后兼容：模块级 settings 全局 + heal 块）；
    否则 → 每用户隔离的 data_root/users/<uid>/config.json。"""
    if uid is None:
        return get_user_config_dir() / "config.json"
    from .tenant import user_config_path
    return user_config_path(uid)


def load_settings(uid: str | None = None) -> Settings:
    """加载配置"""
    config_file = _config_path_for(uid)
    if config_file.exists():
        with open(config_file, "r", encoding="utf-8") as f:
            return Settings(**normalize_settings_payload(json.load(f)))
    return Settings()


def save_settings(settings: Settings, uid: str | None = None):
    """保存配置"""
    config_file = _config_path_for(uid)
    config_file.parent.mkdir(parents=True, exist_ok=True)
    data = normalize_settings_payload(settings.model_dump())
    # 注意：mode 是用户选择、必须持久化（B3 Task 4 custom 激活），故 *不* 剔除。
    # 以下都是 normalize 从 mode + custom_*/managed_* 派生的别名/运行时值，持久化它们会污染配置。
    # managed_base_url 服务端只读（spec §8 不进 per-user settings）：load 时 normalize 强制回常量。
    for key in [
        "api_key",
        "api_base",
        "model",
        "projects_dir",
        "skill_dir",
        "managed_client_token",
        "managed_base_url",
    ]:
        data.pop(key, None)
    # 原子写：同目录 temp + os.replace（与 R3 用户写一致），避免 GET 无锁 load_settings 读到半截。
    import os
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=str(config_file.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, config_file)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _default_managed_models_fetch(url: str, headers: dict[str, str], timeout: float) -> bytes:
    """默认通过 urllib 调用薄网关 /v1/models（stdlib，不引入额外依赖）。"""
    import urllib.request  # 局部 import，避免污染模块顶层
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def heal_stale_managed_model(
    settings: Settings,
    http_fetch=None,
    timeout: float = 5.0,
) -> tuple[Settings, str | None]:
    """如果 managed 模式下保存的 managed_model 已不在薄网关 /v1/models 列表里，
    自动切换到第一个可用模型（best-effort：网络/解析失败一律返回原 settings）。

    背景：用户从老版本（gemini-3-flash）升到新版本（deepseek-v4-pro）时，
    `~/.consulting-report/config.json` 里的 managed_model 字段不会自动迁移
    （`setdefault` 只填空，不覆盖），导致第一条 chat 直接被薄网关 400 拦死。

    返回 (possibly_updated_settings, info_message_or_None)。
    msg 非 None 时调用方应：save_settings(settings) + 记日志。
    """
    if settings.mode != "managed":
        return settings, None

    fetch = http_fetch or _default_managed_models_fetch
    base_url = (settings.managed_base_url or "").rstrip("/")
    if not base_url:
        return settings, None
    url = f"{base_url}/models"
    headers = {"Authorization": f"Bearer {settings.managed_client_token or ''}"}

    try:
        body = fetch(url, headers, timeout)
    except Exception:
        return settings, None

    try:
        data = json.loads(body)
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return settings, None
        allowed = [m.get("id") for m in items if isinstance(m, dict) and m.get("id")]
    except Exception:
        return settings, None

    if not allowed:
        return settings, None

    if settings.managed_model in allowed:
        return settings, None

    old = settings.managed_model
    new = allowed[0]
    updated = settings.model_copy(update={
        "managed_model": new,
        "model": new,
    })
    msg = f"管理通道升级：默认模型已从 {old or '(空)'} 自动切换到 {new}（旧值已不在网关白名单内）"
    return updated, msg
