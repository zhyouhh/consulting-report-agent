import webview
import threading
from backend.main import start_server, settings


def main():
    """启动应用"""
    # 后台线程启动FastAPI
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()

    # 创建PyWebView窗口
    webview.create_window(
        "咨询报告写作助手",
        f"http://{settings.host}:{settings.port}",
        width=1400,
        height=900,
        resizable=True
    )
    webview.start()


if __name__ == "__main__":
    main()
