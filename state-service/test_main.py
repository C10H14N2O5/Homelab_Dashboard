from __future__ import annotations

import argparse
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parent))
import main  # noqa: E402
from tests.helpers import TemporaryProject  # noqa: E402


class LoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.log_file = Path(self.temporary.name) / "service.log"

    def tearDown(self) -> None:
        logger = main.logging.getLogger(main.SERVICE_NAME)
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()
        self.temporary.cleanup()

    def flush_handlers(self, logger: main.logging.Logger) -> None:
        for handler in logger.handlers:
            handler.flush()

    def test_logging_uses_one_small_rotating_backup(self) -> None:
        logger = main.configure_logging(self.log_file, include_console=False)
        handler = logger.handlers[0]
        self.assertIsInstance(handler, main.RotatingFileHandler)
        self.assertEqual(handler.maxBytes, 48 * 1024)
        self.assertEqual(handler.backupCount, 1)

        for index in range(20):
            logger.info("entry %d %s", index, "x" * 8192)
        self.flush_handlers(logger)

        backup = Path(f"{self.log_file}.1")
        self.assertTrue(self.log_file.is_file())
        self.assertTrue(backup.is_file())
        self.assertLessEqual(self.log_file.stat().st_size, main.LOG_MAX_BYTES)
        self.assertLessEqual(backup.stat().st_size, main.LOG_MAX_BYTES)
        self.assertEqual({path.name for path in self.log_file.parent.iterdir()}, {"service.log", "service.log.1"})

    def test_http_access_is_suppressed_but_lifecycle_is_logged(self) -> None:
        logger = main.configure_logging(self.log_file, include_console=False)
        request_handler = main.StateRequestHandler.__new__(main.StateRequestHandler)
        request_handler.server = mock.Mock(logger=logger)
        with mock.patch.object(request_handler, "address_string", return_value="127.0.0.1"):
            request_handler.log_message('"%s" %s', "GET /healthz HTTP/1.1", "200")
        self.flush_handlers(logger)
        self.assertEqual(self.log_file.read_text(encoding="utf-8"), "")

        logger.info("wallpaper state service listening")
        self.flush_handlers(logger)
        self.assertIn("wallpaper state service listening", self.log_file.read_text(encoding="utf-8"))

    def test_oversized_legacy_logs_are_discarded(self) -> None:
        backup = Path(f"{self.log_file}.1")
        self.log_file.write_bytes(b"x" * (main.LOG_MAX_BYTES + 1))
        backup.write_bytes(b"y" * (main.LOG_MAX_BYTES + 1))

        main.configure_logging(self.log_file, include_console=False)

        self.assertEqual(self.log_file.stat().st_size, 0)
        self.assertFalse(backup.exists())


class LauncherTests(unittest.TestCase):
    def test_console_encoding_is_forced_to_utf8_on_windows(self) -> None:
        kernel32 = mock.Mock()
        windll = mock.Mock(kernel32=kernel32)
        streams = [mock.Mock() for _ in range(3)]
        with mock.patch.object(main.os, "name", "nt"), mock.patch.object(main.ctypes, "windll", windll), mock.patch.object(
            main.sys, "stdin", streams[0]
        ), mock.patch.object(main.sys, "stdout", streams[1]), mock.patch.object(main.sys, "stderr", streams[2]):
            main.configure_console_encoding()

        kernel32.SetConsoleCP.assert_called_once_with(65001)
        kernel32.SetConsoleOutputCP.assert_called_once_with(65001)
        for stream in streams:
            stream.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")

    def test_ensure_backend_reuses_healthy_service(self) -> None:
        config = mock.Mock()
        expected = {
            "status": "ok",
            "service": main.SERVICE_NAME,
            "version": main.SERVICE_VERSION,
            "pid": 123,
        }
        with mock.patch.object(main, "health", return_value=expected), mock.patch.object(main, "spawn_backend") as spawn:
            self.assertEqual(main.ensure_backend(config), expected)
            spawn.assert_not_called()

    def test_health_requires_exact_service_version(self) -> None:
        config = mock.Mock(local_base_url="http://127.0.0.1:8787")
        matching = {
            "status": "ok",
            "service": main.SERVICE_NAME,
            "version": main.SERVICE_VERSION,
            "pid": 123,
        }
        with mock.patch.object(main, "read_json_url", return_value=matching):
            self.assertEqual(main.health(config), matching)

        for version in (None, str(main.SERVICE_VERSION), main.SERVICE_VERSION - 1, main.SERVICE_VERSION + 1):
            incompatible = dict(matching)
            if version is None:
                incompatible.pop("version")
            else:
                incompatible["version"] = version
            with self.subTest(version=version), mock.patch.object(
                main, "read_json_url", return_value=incompatible
            ):
                self.assertIsNone(main.health(config))

    def test_spawn_rejects_incompatible_listener_without_starting_process(self) -> None:
        config = mock.Mock(
            local_base_url="http://127.0.0.1:8787",
            listen="127.0.0.1:8787",
        )
        incompatible = {
            "status": "ok",
            "service": main.SERVICE_NAME,
            "version": main.SERVICE_VERSION - 1,
        }
        with mock.patch.object(main, "read_json_url", return_value=incompatible), mock.patch.object(
            main, "port_has_http_listener", return_value=True
        ), mock.patch.object(main.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(RuntimeError, "incompatible"):
                main.spawn_backend(config)
            popen.assert_not_called()

    def test_spawn_rejects_foreign_listener(self) -> None:
        config = mock.Mock()
        config.listen = "127.0.0.1:8787"
        with mock.patch.object(main, "health", return_value=None), mock.patch.object(
            main, "port_has_http_listener", return_value=True
        ):
            with self.assertRaisesRegex(RuntimeError, "occupied"):
                main.spawn_backend(config)

    def test_executable_command_supports_source_mode(self) -> None:
        with mock.patch.object(main.sys, "frozen", False, create=True):
            command = main.executable_command("serve", "--config", "config.json")
        self.assertEqual(command[0], sys.executable)
        self.assertTrue(command[1].endswith("main.py"))
        self.assertEqual(command[-3:], ["serve", "--config", "config.json"])

    def test_main_validates_application_location_before_serve(self) -> None:
        project = TemporaryProject()
        try:
            config = project.write_config()
            arguments = argparse.Namespace(command="serve", config=project.config_path)
            with mock.patch.object(main, "parse_arguments", return_value=arguments), mock.patch.object(
                main, "load_config", return_value=config
            ), mock.patch.object(main, "validate_application_location") as validate_location, mock.patch.object(
                main, "serve", return_value=0
            ) as serve:
                self.assertEqual(main.main([]), 0)
            validate_location.assert_called_once_with(config)
            serve.assert_called_once_with(config)
        finally:
            project.close()

    def test_monitor_refresh_and_quit_keys_remain_immediate(self) -> None:
        keyboard = mock.Mock()
        keyboard.kbhit.return_value = True
        keyboard.getwch.side_effect = ["r", "q"]
        config = mock.Mock()
        config.listen = "127.0.0.1:8787"
        config.background_dir = Path("backgrounds")
        config.state_dir = Path("state")
        config.log_file = Path("service.log")
        status = {"status": "ok", "pid": 123}

        with mock.patch.dict(sys.modules, {"msvcrt": keyboard}), mock.patch.object(
            main, "health", return_value=status
        ) as health, mock.patch.object(main, "read_preferences", return_value=None), mock.patch.object(
            main, "clear_console"
        ), mock.patch.object(main, "tail_lines", return_value=[]), mock.patch.object(
            main.time, "sleep"
        ) as sleep, mock.patch("builtins.print"):
            self.assertEqual(main.monitor(config), 0)

        self.assertEqual(health.call_count, 2)
        self.assertEqual(keyboard.getwch.call_count, 2)
        sleep.assert_not_called()

    def test_monitor_shutdown_confirmation_requires_exact_uppercase_y(self) -> None:
        for value, expected in (
            ("Y", True),
            ("y", False),
            ("Y ", False),
            ("yes", False),
            ("", False),
        ):
            with self.subTest(value=value), mock.patch("builtins.input", return_value=value), mock.patch.object(
                main, "clear_console"
            ), mock.patch("builtins.print"):
                self.assertIs(main.confirm_monitor_shutdown(), expected)

        for error in (EOFError(), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__), mock.patch(
                "builtins.input", side_effect=error
            ), mock.patch.object(main, "clear_console"), mock.patch("builtins.print"):
                self.assertFalse(main.confirm_monitor_shutdown())

    def test_monitor_big_exit_stops_api_or_exits_when_already_stopped(self) -> None:
        for stopped in (True, False):
            keyboard = mock.Mock()
            keyboard.kbhit.return_value = True
            keyboard.getwch.return_value = "x"
            config = mock.Mock(
                listen="127.0.0.1:8787",
                background_dir=Path("backgrounds"),
                state_dir=Path("state"),
                log_file=Path("service.log"),
                probe_connect_address="127.0.0.1:443",
                probe_domain_suffix="home.example.test",
            )
            status = {"status": "ok", "pid": 123}

            with self.subTest(stopped=stopped), mock.patch.dict(
                sys.modules, {"msvcrt": keyboard}
            ), mock.patch.object(main, "health", return_value=status), mock.patch.object(
                main, "read_preferences", return_value=None
            ), mock.patch.object(main, "clear_console"), mock.patch.object(
                main, "tail_lines", return_value=[]
            ), mock.patch.object(main, "confirm_monitor_shutdown", return_value=True), mock.patch.object(
                main, "request_stop", return_value=stopped
            ) as request_stop, mock.patch.object(
                main.time, "sleep"
            ) as sleep, mock.patch(
                "builtins.print"
            ) as output:
                self.assertEqual(main.monitor(config), 0)

            request_stop.assert_called_once_with(config)
            sleep.assert_not_called()
            message = "API 已停止，Monitor 已关闭。" if stopped else "API 当前未运行，Monitor 已关闭。"
            self.assertTrue(any(message in str(call) for call in output.call_args_list))

    def test_monitor_big_exit_cancel_returns_to_monitor_and_can_retry(self) -> None:
        keyboard = mock.Mock()
        keyboard.kbhit.return_value = True
        keyboard.getwch.side_effect = ["x", "x"]
        config = mock.Mock(
            listen="127.0.0.1:8787",
            background_dir=Path("backgrounds"),
            state_dir=Path("state"),
            log_file=Path("service.log"),
            probe_connect_address="127.0.0.1:443",
            probe_domain_suffix="home.example.test",
        )
        status = {"status": "ok", "pid": 123}

        with mock.patch.dict(sys.modules, {"msvcrt": keyboard}), mock.patch.object(
            main, "health", return_value=status
        ) as health, mock.patch.object(main, "read_preferences", return_value=None), mock.patch.object(
            main, "clear_console"
        ), mock.patch.object(main, "tail_lines", return_value=[]), mock.patch.object(
            main, "confirm_monitor_shutdown", side_effect=[False, True]
        ), mock.patch.object(main, "request_stop") as request_stop, mock.patch.object(
            main.time, "sleep"
        ) as sleep, mock.patch(
            "builtins.print"
        ) as output:
            self.assertEqual(main.monitor(config), 0)

        request_stop.assert_called_once_with(config)
        self.assertEqual(health.call_count, 2)
        sleep.assert_not_called()
        self.assertTrue(any("已取消大退，API 继续运行" in str(call) for call in output.call_args_list))

    def test_monitor_big_exit_failure_returns_to_monitor_and_can_retry(self) -> None:
        keyboard = mock.Mock()
        keyboard.kbhit.return_value = True
        keyboard.getwch.side_effect = ["X", "x"]
        config = mock.Mock(
            listen="127.0.0.1:8787",
            background_dir=Path("backgrounds"),
            state_dir=Path("state"),
            log_file=Path("service.log"),
            probe_connect_address="127.0.0.1:443",
            probe_domain_suffix="home.example.test",
        )
        status = {"status": "ok", "pid": 123}

        with mock.patch.dict(sys.modules, {"msvcrt": keyboard}), mock.patch.object(
            main, "health", return_value=status
        ), mock.patch.object(main, "read_preferences", return_value=None), mock.patch.object(
            main, "clear_console"
        ), mock.patch.object(main, "tail_lines", return_value=[]), mock.patch.object(
            main, "confirm_monitor_shutdown", return_value=True
        ), mock.patch.object(
            main,
            "request_stop",
            side_effect=[RuntimeError("backend did not stop within the timeout"), True],
        ) as request_stop, mock.patch.object(
            main.time, "sleep"
        ) as sleep, mock.patch(
            "builtins.print"
        ) as output:
            self.assertEqual(main.monitor(config), 0)

        self.assertEqual(request_stop.call_args_list, [mock.call(config), mock.call(config)])
        sleep.assert_not_called()
        self.assertTrue(
            any(
                "大退失败：backend did not stop within the timeout" in str(call)
                for call in output.call_args_list
            )
        )


if __name__ == "__main__":
    unittest.main()
