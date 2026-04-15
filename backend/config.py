from dataclasses import dataclass
from pydantic_settings import BaseSettings
from pydantic import Field
from pathlib import Path
import json
import os
import sys

DEFAULT_MANAGED_BASE_URL = "https://newapi.z0y0h.work/client/v1"
DEFAULT_MANAGED_MODEL = "gemini-3-flash"
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


def get_search_runtime_state_path(config_dir: Path | None = None) -> Path:
    return (config_dir or get_user_config_dir()) / SEARCH_RUNTIME_STATE_FILENAME


def get_search_cache_path(config_dir: Path | None = None) -> Path:
    return (config_dir or get_user_config_dir()) / SEARCH_CACHE_FILENAME


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


def _require_provider_entry(name: str, payload: dict) -> ManagedSearchProviderConfig:
    if not isinstance(payload, dict):
        raise ValueError(f"managed_search_pool.json 中 {name} 配置格式不正确")
    enabled = payload.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError(f"managed_search_pool.json 中 {name}.enabled 必须是 boolean")
    api_key = str(payload.get("api_key", "")).strip()
    if enabled and not api_key:
        raise ValueError(f"managed_search_pool.json 中 {name} 缺少 api_key")
    return ManagedSearchProviderConfig(
        enabled=enabled,
        api_key=api_key,
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

    normalized.setdefault("managed_base_url", DEFAULT_MANAGED_BASE_URL)
    normalized.setdefault("managed_model", DEFAULT_MANAGED_MODEL)
    normalized.setdefault("managed_search_api_url", DEFAULT_MANAGED_SEARCH_API_URL)
    normalized["managed_client_token"] = runtime_managed_token
    normalized.setdefault("custom_api_base", normalized.get("api_base", ""))
    normalized.setdefault("custom_api_key", normalized.get("api_key", ""))
    normalized.setdefault("custom_model", normalized.get("model", ""))
    normalized.setdefault("custom_context_limit_override", None)
    normalized["projects_dir"] = runtime_projects_dir
    normalized["skill_dir"] = runtime_skill_dir

    # 桌面端始终以默认通道启动，保留自定义 API 信息供用户临时切换。
    # 同时旧版本配置可能遗留开发环境路径和自定义模式，也在这里统一纠正。
    if is_legacy_config:
        normalized["mode"] = "managed"
    else:
        normalized["mode"] = "managed"

    if normalized["mode"] == "managed":
        normalized["api_base"] = normalized["managed_base_url"]
        normalized["model"] = normalized["managed_model"]
        normalized["api_key"] = normalized["managed_client_token"]
    else:
        normalized["api_base"] = normalized["custom_api_base"]
        normalized["model"] = normalized["custom_model"]
        normalized["api_key"] = normalized["custom_api_key"]

    return normalized


def load_settings() -> Settings:
    """加载配置"""
    config_file = get_user_config_dir() / "config.json"
    if config_file.exists():
        with open(config_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            return Settings(**normalize_settings_payload(data))
    return Settings()


def save_settings(settings: Settings):
    """保存配置"""
    config_file = get_user_config_dir() / "config.json"
    data = normalize_settings_payload(settings.model_dump())
    for key in [
        "mode",
        "api_key",
        "api_base",
        "model",
        "projects_dir",
        "skill_dir",
        "managed_client_token",
    ]:
        data.pop(key, None)
    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
