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


def get_default_managed_client_token(base_path: Path | None = None) -> str:
    env_token = os.getenv("CONSULTING_REPORT_MANAGED_CLIENT_TOKEN", "").strip()
    if env_token:
        return env_token

    token_path = get_managed_client_token_path(base_path)
    if token_path.exists():
        token = token_path.read_text(encoding="utf-8").strip()
        if token:
            return token

    return DEFAULT_MANAGED_CLIENT_TOKEN


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
