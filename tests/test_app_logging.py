"""验证 app.log RotatingFileHandler 配置。"""
import importlib
import logging
import sys
import tempfile
import unittest
from logging.handlers import RotatingFileHandler
from pathlib import Path
from unittest.mock import patch


def _close_handlers_not_in(root, original_handlers):
    for handler in list(root.handlers):
        if handler not in original_handlers:
            root.removeHandler(handler)
            handler.close()


def _import_app_without_real_home():
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    with tempfile.TemporaryDirectory() as temp_dir:
        with patch("pathlib.Path.home", return_value=Path(temp_dir)):
            if "app" in sys.modules:
                app_module = importlib.reload(sys.modules["app"])
            else:
                app_module = importlib.import_module("app")
        _close_handlers_not_in(root, original_handlers)
        root.setLevel(original_level)
    return app_module


class AppLogTests(unittest.TestCase):
    def test_setup_app_log_attaches_rotating_file_handler_to_root_logger(self):
        app_module = _import_app_without_real_home()
        _setup_app_log = app_module._setup_app_log

        root = logging.getLogger()
        original_handlers = list(root.handlers)
        original_level = root.level
        try:
            for handler in list(root.handlers):
                if isinstance(handler, RotatingFileHandler):
                    root.removeHandler(handler)

            with patch("app.Path.home") as mock_home:
                mock_home.return_value = Path("C:/__test_home__")
                with patch("app.Path.mkdir"):
                    handler_init_path = "logging.handlers.RotatingFileHandler.__init__"
                    with patch(handler_init_path, return_value=None) as mock_handler_init:
                        _setup_app_log()
                        self.assertTrue(
                            mock_handler_init.called,
                            "RotatingFileHandler should be constructed",
                        )
                        args, kwargs = mock_handler_init.call_args
                        expected_log = Path(
                            "C:/__test_home__/.consulting-report/app.log"
                        )
                        self.assertEqual(args[0], expected_log)
                        self.assertEqual(kwargs.get("maxBytes"), 5 * 1024 * 1024)
                        self.assertEqual(kwargs.get("backupCount"), 3)
                        self.assertEqual(kwargs.get("encoding"), "utf-8")
        finally:
            root.handlers = original_handlers
            root.setLevel(original_level)

    def test_setup_app_log_does_not_add_duplicate_app_log_handlers(self):
        app_module = _import_app_without_real_home()
        _setup_app_log = app_module._setup_app_log

        root = logging.getLogger()
        original_handlers = list(root.handlers)
        original_level = root.level
        try:
            for handler in list(root.handlers):
                if isinstance(handler, RotatingFileHandler):
                    root.removeHandler(handler)

            with tempfile.TemporaryDirectory() as temp_dir:
                test_home = Path(temp_dir)
                try:
                    with patch("app.Path.home", return_value=test_home):
                        _setup_app_log()
                        _setup_app_log()

                    log_file = test_home / ".consulting-report" / "app.log"
                    matching_handlers = [
                        handler
                        for handler in root.handlers
                        if isinstance(handler, RotatingFileHandler)
                        and Path(handler.baseFilename) == log_file
                    ]
                    self.assertEqual(len(matching_handlers), 1)
                finally:
                    for handler in list(root.handlers):
                        if handler not in original_handlers:
                            root.removeHandler(handler)
                            handler.close()
        finally:
            _close_handlers_not_in(root, original_handlers)
            root.handlers = original_handlers
            root.setLevel(original_level)

    def test_setup_app_log_ignores_rotating_file_handler_oserror(self):
        app_module = _import_app_without_real_home()
        _setup_app_log = app_module._setup_app_log

        class FailingRotatingFileHandler(RotatingFileHandler):
            def __init__(self, *args, **kwargs):
                raise OSError("log file unavailable")

        root = logging.getLogger()
        original_handlers = list(root.handlers)
        original_level = root.level
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                with patch("app.Path.home", return_value=Path(temp_dir)):
                    with patch("app.RotatingFileHandler", FailingRotatingFileHandler):
                        _setup_app_log()

            self.assertEqual(root.handlers, original_handlers)
        finally:
            _close_handlers_not_in(root, original_handlers)
            root.handlers = original_handlers
            root.setLevel(original_level)
