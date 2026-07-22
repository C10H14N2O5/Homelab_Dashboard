from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parent))
import main  # noqa: E402


FIXED_TIME = "2026-07-22T12:00:00Z"
TEST_ORIGIN = "https://home.example.test"


class TemporaryProject:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.backgrounds = self.root / "web" / "html" / "backgrounds"
        self.backgrounds.mkdir(parents=True)
        for name in ("bg.jpg", "test.jpg", "portrait.png", "alternate.webp"):
            (self.backgrounds / name).write_bytes(b"image")
        self.state_dir = self.root / "HomeLabWallpaperState-data"
        self.config_path = self.root / main.DEFAULT_CONFIG_NAME

    def close(self) -> None:
        self.temporary.cleanup()

    def write_config(self, **overrides: object) -> main.AppConfig:
        value = {
            "version": 1,
            "backgroundDir": r"web\html\backgrounds",
            "allowedOrigins": [TEST_ORIGIN],
            "listen": "127.0.0.1:8787",
            "stateDir": main.DEFAULT_DATA_DIRECTORY,
            "controlToken": "x" * 43,
        }
        value.update(overrides)
        main.atomic_write_json(self.config_path, value)
        return main.load_config(self.config_path)


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = TemporaryProject()

    def tearDown(self) -> None:
        self.project.close()

    def test_load_config_resolves_relative_paths(self) -> None:
        config = self.project.write_config()
        self.assertEqual(config.background_dir, self.project.backgrounds.resolve())
        self.assertEqual(config.state_dir, self.project.state_dir.resolve())
        self.assertEqual(config.listen, "127.0.0.1:8787")
        self.assertEqual(config.allowed_origins, frozenset({TEST_ORIGIN}))

    def test_rejects_unsafe_listen_and_public_state(self) -> None:
        for overrides in (
            {"listen": "0.0.0.0:8787"},
            {"stateDir": r"web\html\runtime"},
            {"allowedOrigins": ["https://example.test/path"]},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(main.ConfigurationError):
                    self.project.write_config(**overrides)

    def test_rejects_missing_default_background(self) -> None:
        (self.project.backgrounds / "bg.jpg").unlink()
        with self.assertRaises(main.ConfigurationError):
            self.project.write_config()


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = TemporaryProject()
        self.config = self.project.write_config()
        self.store = main.StateStore(self.config.background_dir, self.config.state_file, lambda: FIXED_TIME)

    def tearDown(self) -> None:
        self.project.close()

    def test_initial_state_and_independent_updates_persist(self) -> None:
        self.assertEqual(
            self.store.get(),
            {
                "version": 1,
                "landscape": "bg.jpg",
                "portrait": "bg.jpg",
                "updatedAt": FIXED_TIME,
            },
        )
        self.store.update("landscape", "test.jpg")
        result = self.store.update("portrait", "portrait.png")
        self.assertEqual(result["landscape"], "test.jpg")
        self.assertEqual(result["portrait"], "portrait.png")
        reloaded = main.StateStore(self.config.background_dir, self.config.state_file, lambda: FIXED_TIME)
        self.assertEqual(reloaded.get(), result)

    def test_validation(self) -> None:
        for name, error in (
            ("../test.jpg", main.InvalidBackground),
            ("test.svg", main.InvalidBackground),
            ("missing.jpg", main.BackgroundMissing),
        ):
            with self.subTest(name=name), self.assertRaises(error):
                self.store.update("landscape", name)
        with self.assertRaises(ValueError):
            self.store.update("desktop", "test.jpg")

    def test_deleted_selection_falls_back_and_repairs_state(self) -> None:
        self.store.update("landscape", "test.jpg")
        (self.project.backgrounds / "test.jpg").unlink()
        repaired = self.store.get()
        self.assertEqual(repaired["landscape"], "bg.jpg")
        persisted = json.loads(self.config.state_file.read_text(encoding="utf-8"))
        self.assertEqual(persisted, repaired)

    def test_concurrent_updates_leave_valid_complete_state(self) -> None:
        errors: list[Exception] = []

        def update(index: int) -> None:
            try:
                mode = "landscape" if index % 2 == 0 else "portrait"
                background = ("bg.jpg", "test.jpg", "portrait.png", "alternate.webp")[index % 4]
                self.store.update(mode, background)
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=update, args=(index,)) for index in range(40)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        persisted = json.loads(self.config.state_file.read_text(encoding="utf-8"))
        self.assertEqual(set(persisted), {"version", "landscape", "portrait", "updatedAt"})
        self.assertFalse(any(path.name.startswith(".state.json.") for path in self.config.state_dir.iterdir()))


class APITests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = TemporaryProject()
        self.config = self.project.write_config(listen="127.0.0.1:1")
        self.store = main.StateStore(self.config.background_dir, self.config.state_file, lambda: FIXED_TIME)
        logger = main.logging.getLogger(f"test-{id(self)}")
        logger.handlers.clear()
        logger.addHandler(main.logging.NullHandler())
        self.server = main.StateHTTPServer.__new__(main.StateHTTPServer)
        self.server.config = self.config
        self.server.store = self.store
        self.server.logger = logger
        main.ThreadingHTTPServer.__init__(self.server, ("127.0.0.1", 0), main.StateRequestHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.server.server_address

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.project.close()

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, object]]:
        connection = HTTPConnection(self.host, self.port, timeout=2)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        payload = json.loads(response.read()) if response.length != 0 else {}
        status = response.status
        connection.close()
        return status, payload

    def patch(self, value: dict[str, object], headers: dict[str, str] | None = None) -> tuple[int, dict[str, object]]:
        request_headers = {
            "Origin": TEST_ORIGIN,
            "X-Requested-With": "homelab-dashboard",
            "Content-Type": "application/json",
        }
        if headers:
            request_headers.update(headers)
        return self.request("PATCH", main.API_PATH, json.dumps(value).encode(), request_headers)

    def test_health_and_get_contract(self) -> None:
        status, health = self.request("GET", main.HEALTH_PATH)
        self.assertEqual(status, 200)
        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["service"], main.SERVICE_NAME)
        status, preferences = self.request("GET", main.API_PATH)
        self.assertEqual(status, 200)
        self.assertEqual(preferences["landscape"], "bg.jpg")

    def test_patch_matches_frontend_contract(self) -> None:
        status, preferences = self.patch({"mode": "landscape", "background": "test.jpg"})
        self.assertEqual(status, 200)
        self.assertEqual(preferences["landscape"], "test.jpg")

    def test_patch_validation(self) -> None:
        cases = (
            ({"mode": "landscape", "background": "test.jpg"}, {"Origin": "https://evil.test"}, 403),
            ({"mode": "landscape", "background": "test.jpg"}, {"X-Requested-With": "wrong"}, 403),
            ({"mode": "desktop", "background": "test.jpg"}, None, 400),
            ({"mode": ["landscape"], "background": "test.jpg"}, None, 400),
            ({"mode": "landscape", "background": ["test.jpg"]}, None, 400),
            ({"mode": "landscape", "background": "../test.jpg"}, None, 400),
            ({"mode": "landscape", "background": "missing.jpg"}, None, 404),
            ({"mode": "landscape", "background": "test.jpg", "extra": True}, None, 400),
        )
        for value, headers, expected in cases:
            with self.subTest(value=value, headers=headers):
                status, _ = self.patch(value, headers)
                self.assertEqual(status, expected)

    def test_control_requires_token_and_shuts_down(self) -> None:
        status, _ = self.request("POST", main.CONTROL_PATH, headers={"X-Control-Token": "wrong"})
        self.assertEqual(status, 403)
        status, payload = self.request(
            "POST", main.CONTROL_PATH, headers={"X-Control-Token": self.config.control_token}
        )
        self.assertEqual(status, 202)
        self.assertEqual(payload["status"], "stopping")


class LauncherTests(unittest.TestCase):
    def test_ensure_backend_reuses_healthy_service(self) -> None:
        config = mock.Mock()
        expected = {"status": "ok", "service": main.SERVICE_NAME, "pid": 123}
        with mock.patch.object(main, "health", return_value=expected), mock.patch.object(main, "spawn_backend") as spawn:
            self.assertEqual(main.ensure_backend(config), expected)
            spawn.assert_not_called()

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


if __name__ == "__main__":
    unittest.main()
