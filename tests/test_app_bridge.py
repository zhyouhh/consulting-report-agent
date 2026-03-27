import unittest
from unittest import mock

import app as desktop_app


class DesktopBridgeTests(unittest.TestCase):
    def test_desktop_bridge_wraps_window_file_dialogs(self):
        window = mock.Mock()
        window.create_file_dialog.side_effect = [
            ["D:/Workspaces/demo"],
            ["D:/Workspaces/demo/资料/a.txt", "D:/Workspaces/demo/资料/b.png"],
        ]

        bridge = desktop_app.DesktopBridge(window)

        self.assertEqual(bridge.select_workspace_folder(), "D:/Workspaces/demo")
        self.assertEqual(
            bridge.select_workspace_files("D:/Workspaces/demo"),
            ["D:/Workspaces/demo/资料/a.txt", "D:/Workspaces/demo/资料/b.png"],
        )

    @mock.patch("app.register_desktop_bridge")
    @mock.patch("app.webview.start")
    @mock.patch("app.webview.create_window")
    @mock.patch("app.threading.Thread")
    def test_main_registers_bridge_with_created_window(
        self,
        mock_thread,
        mock_create_window,
        mock_webview_start,
        mock_register_bridge,
    ):
        fake_thread = mock.Mock()
        mock_thread.return_value = fake_thread
        fake_window = mock.Mock()
        mock_create_window.return_value = fake_window

        desktop_app.main()

        fake_thread.start.assert_called_once_with()
        mock_create_window.assert_called_once()
        mock_register_bridge.assert_called_once()
        bridge = mock_register_bridge.call_args.args[0]
        self.assertIsInstance(bridge, desktop_app.DesktopBridge)
        self.assertIs(bridge.window, fake_window)
        mock_webview_start.assert_called_once_with()
