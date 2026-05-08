import logging
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path


def _setup_app_log():
    log_dir = Path.home() / ".consulting-report"
    log_file = log_dir / "app.log"
    root_logger = logging.getLogger()

    for handler in root_logger.handlers:
        handler_path = getattr(handler, "baseFilename", None)
        if (
            isinstance(handler, RotatingFileHandler)
            and handler_path
            and Path(handler_path) == log_file
        ):
            return

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return

    handler = None
    try:
        handler = RotatingFileHandler(
            log_file,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        handler.setLevel(logging.INFO)
        if root_logger.level > logging.INFO:
            root_logger.setLevel(logging.INFO)
        root_logger.addHandler(handler)
    except OSError:
        if handler is not None:
            handler.close()
        return


# Install file logging before importing backend modules that may log during import.
_setup_app_log()

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
