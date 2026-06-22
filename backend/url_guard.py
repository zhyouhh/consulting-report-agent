"""SSRF 防护叶子模块：public-IP 校验 + custom API 白名单 + guarded http client。
绝不 import chat/skill/main/config——只依赖 httpx + stdlib。"""
import ipaddress
import os
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
