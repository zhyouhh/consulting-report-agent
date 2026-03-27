import threading

import webview

from backend.main import register_desktop_bridge, settings, start_server


class DesktopBridge:
    def __init__(self, window):
        self.window = window

    def select_workspace_folder(self):
        result = self.window.create_file_dialog(webview.FOLDER_DIALOG)
        if not result:
            return ""
        return result[0]

    def select_workspace_files(self, initial_directory: str):
        result = self.window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=True,
            directory=initial_directory,
        )
        return list(result or [])


def main():
    """启动应用"""
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()

    window = webview.create_window(
        "咨询报告写作助手",
        f"http://{settings.host}:{settings.port}",
        width=1400,
        height=900,
        resizable=True,
    )
    register_desktop_bridge(DesktopBridge(window))
    webview.start()


if __name__ == "__main__":
    main()
