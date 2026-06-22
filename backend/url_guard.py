"""SSRF 防护叶子模块：public-IP 校验 + custom API 白名单 + guarded http client。
绝不 import chat/skill/main/config——只依赖 httpx + stdlib。"""
import ipaddress
import os
import re
import socket
from urllib.parse import urlparse

import httpx

_CGNAT = ipaddress.ip_network("100.64.0.0/10")


class SsrfBlockedError(ValueError):
    """目标地址未通过 SSRF 校验（协议/主机/IP 不合法）。"""


def assert_public_ip(ip_text: str) -> None:
    ip = ipaddress.ip_address(ip_text)
    if (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified or (ip in _CGNAT)
        or getattr(ip, "is_site_local", False)
    ):
        raise SsrfBlockedError("不允许访问本地或内网地址。")


# url_guard 保持叶子（只依赖 httpx + stdlib），绝不 import accounts。
# 运行时 admin 增删的白名单由 main.py 启动/编辑后经 set_runtime_allowed_hosts 注入。
_DEFAULT_ALLOWED_HOSTS = (
    "newapi.z0y0h.work",        # managed 上游（服务端常量，始终允许）
    "api.openai.com",
    "api.deepseek.com",
    "api.moonshot.cn",
    "open.bigmodel.cn",         # 智谱
    "dashscope.aliyuncs.com",   # 通义千问
)
_RUNTIME_ALLOWED_HOSTS: set[str] = set()   # app_config 注入，admin 面板可增删


def set_runtime_allowed_hosts(hosts) -> None:
    """main.py 启动从 app_config 载入 + admin 编辑后刷新；归一化为小写集合。"""
    global _RUNTIME_ALLOWED_HOSTS
    _RUNTIME_ALLOWED_HOSTS = {h.strip().lower() for h in (hosts or []) if h and h.strip()}


_HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")


def is_valid_hostname(host: str) -> bool:
    """纯主机名（无 scheme/port/path/逗号/空白/通配符）。供 admin 白名单输入校验。"""
    return bool(_HOSTNAME_RE.match((host or "").strip().lower()))


def builtin_allowed_hosts() -> set[str]:
    return {h.lower() for h in _DEFAULT_ALLOWED_HOSTS}


def env_allowed_hosts() -> set[str]:
    raw = (os.environ.get("CRA_CUSTOM_API_ALLOWED_HOSTS") or "").strip()
    return {h.strip().lower() for h in raw.split(",") if h.strip()} if raw else set()


def custom_api_allowed_hosts() -> set[str]:
    """白名单 = 内置默认 ∪ env(bootstrap) ∪ 运行时(app_config，admin 维护)。"""
    return builtin_allowed_hosts() | env_allowed_hosts() | _RUNTIME_ALLOWED_HOSTS


def assert_resolves_public(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise SsrfBlockedError(f"无法解析主机：{host}") from exc
    if not infos:
        raise SsrfBlockedError(f"无法解析主机：{host}")
    for info in infos:
        assert_public_ip(info[4][0])


def validate_custom_api_base(url: str) -> str:
    """校验用户自填 custom_api_base：https + 白名单主机 + 解析到公网。
    返回去空白后的 URL；任何不合法抛 SsrfBlockedError。"""
    cleaned = (url or "").strip()
    parsed = urlparse(cleaned)
    if parsed.scheme != "https":
        raise SsrfBlockedError("自定义 API 地址必须是 https。")
    if parsed.username or parsed.password:
        # userinfo 会让 httpx 注入 Authorization: Basic，覆盖用户的 Bearer key → 静默坏掉 custom 调用。
        raise SsrfBlockedError("自定义 API 地址不能包含用户名/密码")
    try:
        _ = parsed.port   # 坏端口（如 :bad）此处抛 ValueError；合法端口/无端口都放行。
    except ValueError as exc:
        raise SsrfBlockedError("自定义 API 地址端口非法") from exc
    host = (parsed.hostname or "").lower()
    if not host:
        raise SsrfBlockedError("自定义 API 地址缺少主机名。")
    if host not in custom_api_allowed_hosts():
        raise SsrfBlockedError(f"主机 {host} 不在允许列表，请联系管理员添加。")
    assert_resolves_public(host)
    return cleaned


class _GuardedHTTPTransport(httpx.HTTPTransport):
    """每次请求都重校验：https + 主机在白名单 + 解析到公网。
    安全边界 = 白名单（只有 admin 批准的主机能被连接）。public-IP 校验是第二道防线，
    拦「白名单主机被误配/投毒到私网」。**注意**：此 transport 未在连接层 pin IP，
    对「攻击者控制白名单内域名 + 解析后到连接前翻转到私网」的 DNS rebinding 仍有 TOCTOU；
    彻底防 rebinding 需 pinned-IP-with-SNI transport（后置增强，§8.3 R3-NIT3 允许白名单为 B3 终态）。"""

    def handle_request(self, request):
        host = (request.url.host or "").lower()
        if request.url.scheme != "https":
            raise SsrfBlockedError("阻止非 https 请求。")
        if host not in custom_api_allowed_hosts():
            raise SsrfBlockedError(f"阻止对 {host!r} 的请求（不在允许列表）。")
        assert_resolves_public(host)
        return super().handle_request(request)


def build_guarded_http_client(timeout) -> httpx.Client:
    """供 OpenAI SDK 用的受控 http client：白名单 transport + 忽略环境代理 + 不跟随重定向。"""
    return httpx.Client(
        timeout=timeout,
        trust_env=False,          # 忽略 HTTP(S)_PROXY，防经代理绕过
        follow_redirects=False,   # 不跟随重定向，防重定向到私网
        transport=_GuardedHTTPTransport(),
    )
