#!/usr/bin/env python3
"""Web模式启动脚本 - 可从外部访问"""
from backend.main import app
import uvicorn

if __name__ == "__main__":
    port = 8888
    print(f"\n🚀 启动 Web 服务...")
    print(f"📍 本地访问: http://localhost:{port}")
    print(f"🌐 外网访问: http://57.129.103.127:{port}")
    print(f"\n按 Ctrl+C 停止服务\n")

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info"
    )
