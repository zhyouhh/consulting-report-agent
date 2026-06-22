#!/usr/bin/env python3
"""Web模式启动脚本 - 可从外部访问"""
import os

from backend.main import app, assert_safe_startup
import uvicorn

if __name__ == "__main__":
    port = 8888
    host = "0.0.0.0"
    print(f"\n🚀 启动 Web 服务...")
    print(f"📍 本地访问: http://localhost:{port}")
    print(f"🌐 外网访问: http://57.129.103.127:{port}")
    print(f"\n按 Ctrl+C 停止服务\n")

    app.state.auth_required = True
    app.state.cookie_secure = True   # web 默认部署在 https 之后；本地 http 调试可设 CRA_COOKIE_INSECURE
    if (os.environ.get("CRA_COOKIE_INSECURE") or "").strip():
        app.state.cookie_secure = False
    if not (os.environ.get("CRA_ALLOWED_ORIGIN") or "").strip():
        print("⚠️ 未设 CRA_ALLOWED_ORIGIN：仅 loopback 来源的写请求会被 CSRF 放行；"
              "远程部署必须设为你的站点 origin（如 https://app.example.com）")
    assert_safe_startup(True, host)
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info"
    )
