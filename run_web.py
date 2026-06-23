#!/usr/bin/env python3
"""Web模式启动脚本 - 可从外部访问（反代在前）"""
import os

from backend.main import app, assert_safe_startup
import uvicorn

if __name__ == "__main__":
    host = (os.environ.get("CRA_BIND_HOST") or "127.0.0.1").strip()
    port = int((os.environ.get("CRA_BIND_PORT") or "8888").strip())
    print(f"\n🚀 启动 Web 服务... 监听 {host}:{port}（反代/HTTPS 在前）\n")

    app.state.auth_required = True
    app.state.cookie_secure = True   # web 默认部署在 https 之后；本地 http 调试可设 CRA_COOKIE_INSECURE
    if (os.environ.get("CRA_COOKIE_INSECURE") or "").strip():
        app.state.cookie_secure = False
    if not (os.environ.get("CRA_ALLOWED_ORIGIN") or "").strip():
        if app.state.cookie_secure:
            print("⚠️ 未设 CRA_ALLOWED_ORIGIN（生产默认 cookie_secure 态）："
                  "CSRF 不信任 loopback，所有写请求（POST/PUT/PATCH/DELETE）会被 403 拒绝（fail-closed）。"
                  "请设为你的站点 origin（如 https://consulting.z0y0h.work）后重启。")
        else:
            print("⚠️ 未设 CRA_ALLOWED_ORIGIN（本地 CRA_COOKIE_INSECURE 调试态）："
                  "仅 loopback 来源的写请求会被 CSRF 放行；远程部署须设站点 origin 并去掉 CRA_COOKIE_INSECURE。")
    assert_safe_startup(True, host)
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        proxy_headers=True,            # 信任 nginx 注入的 X-Forwarded-For（配合 §5.7 real_ip）
        forwarded_allow_ips="127.0.0.1",
    )
