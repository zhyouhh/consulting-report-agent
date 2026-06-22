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
        if app.state.cookie_secure:
            # 生产默认（cookie_secure=True）：CSRF 不信任 loopback，缺 origin → 写请求全 fail-closed 403。
            print("⚠️ 未设 CRA_ALLOWED_ORIGIN（生产默认 cookie_secure 态）："
                  "CSRF 不信任 loopback，所有写请求（POST/PUT/PATCH/DELETE）会被 403 拒绝（fail-closed）。"
                  "请设为你的站点 origin（如 https://app.example.com）后重启。")
        else:
            # 本地 http 调试（CRA_COOKIE_INSECURE）：才是「仅 loopback 放行」。
            print("⚠️ 未设 CRA_ALLOWED_ORIGIN（本地 CRA_COOKIE_INSECURE 调试态）："
                  "仅 loopback 来源的写请求会被 CSRF 放行；远程部署须设站点 origin 并去掉 CRA_COOKIE_INSECURE。")
    assert_safe_startup(True, host)
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info"
    )
