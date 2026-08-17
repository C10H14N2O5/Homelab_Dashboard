from __future__ import annotations

import argparse
import io
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from http.client import HTTPConnection
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parent))
import main  # noqa: E402
from homelab_state import backgrounds  # noqa: E402
from tests.helpers import (  # noqa: E402
    FIXED_TIME,
    TEST_ORIGIN,
    TemporaryProject,
    image_bytes,
)


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


class FakeProbeClient:
    def __init__(self) -> None:
        self.executor = object()
        self.targets: list[str] = []

    def probe_many(self, targets: list[str]) -> list[main.ProbeResult]:
        self.targets = list(targets)
        return [main.ProbeResult(target, 200 if index == 0 else None, 10 + index, None if index == 0 else "timeout")
                for index, target in enumerate(targets)]


class FakeBestdoriManager:
    def __init__(self) -> None:
        self.draw_request: tuple[str, set[int], int | None] | None = None
        self.history_action: str | None = None

    def characters(self) -> list[dict[str, object]]:
        return [{"id": 1, "name": "户山香澄"}, {"id": 40, "name": "奥泽美咲"}]

    def draw(self, mode: str, rarities: set[int], character_id: int | None) -> dict[str, object]:
        self.draw_request = (mode, set(rarities), character_id)
        return {
            "background": {
                "background": "户山香澄_五星_测试卡面_101_after_training.png",
                "size": 100,
                "width": 1000,
                "height": 1334,
            },
            "card": {
                "id": 101,
                "characterId": 1,
                "characterName": "户山香澄",
                "rarity": 5,
                "title": "测试卡面",
                "art": "after_training",
            },
        }

    def reconcile(self) -> dict[str, int]:
        self.history_action = "reconcile"
        return {"removed": 1, "remaining": 2}

    def reset(self) -> dict[str, int]:
        self.history_action = "reset"
        return {"removed": 3, "remaining": 0}


class APITests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = TemporaryProject()
        self.config = replace(
            self.project.write_config(listen="127.0.0.1:1"),
            listen_port=0,
        )
        self.store = main.StateStore(self.config.background_dir, self.config.state_file, lambda: FIXED_TIME)
        logger = main.logging.getLogger(f"test-{id(self)}")
        logger.handlers.clear()
        logger.addHandler(main.logging.NullHandler())
        self.server = main.StateHTTPServer(
            self.config,
            self.store,
            logger,
            FakeProbeClient(),
            FakeBestdoriManager(),
        )
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

    def raw_exchange(
        self,
        request: bytes,
        *,
        shutdown_write: bool = False,
        timeout: float = 2.0,
    ) -> bytes:
        client = socket.create_connection((self.host, self.port), timeout=timeout)
        client.settimeout(timeout)
        try:
            client.sendall(request)
            if shutdown_write:
                client.shutdown(socket.SHUT_WR)
            response = bytearray()
            while True:
                try:
                    chunk = client.recv(65536)
                except (socket.timeout, OSError):
                    break
                if not chunk:
                    break
                response.extend(chunk)
            return bytes(response)
        finally:
            client.close()

    def patch(self, value: dict[str, object], headers: dict[str, str] | None = None) -> tuple[int, dict[str, object]]:
        request_headers = {
            "Origin": TEST_ORIGIN,
            "X-Requested-With": "homelab-dashboard",
            "Content-Type": "application/json",
        }
        if headers:
            request_headers.update(headers)
        return self.request("PATCH", main.API_PATH, json.dumps(value).encode(), request_headers)

    def service_status(self, value: object, headers: dict[str, str] | None = None) -> tuple[int, dict[str, object]]:
        request_headers = {
            "Origin": TEST_ORIGIN,
            "X-Requested-With": "homelab-dashboard",
            "Content-Type": "application/json",
        }
        if headers:
            request_headers.update(headers)
        return self.request("POST", main.SERVICE_STATUS_PATH, json.dumps(value).encode(), request_headers)

    def upload(
        self,
        name: str,
        body: bytes,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, object]]:
        request_headers = {
            "Origin": TEST_ORIGIN,
            "X-Requested-With": "homelab-dashboard",
            "Content-Type": "application/octet-stream",
        }
        if headers:
            request_headers.update(headers)
        return self.request("POST", f"{main.BACKGROUND_UPLOAD_PATH}?name={name}", body, request_headers)

    def remove(
        self,
        value: object,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, object]]:
        request_headers = {
            "Origin": TEST_ORIGIN,
            "X-Requested-With": "homelab-dashboard",
            "Content-Type": "application/json",
        }
        if headers:
            request_headers.update(headers)
        return self.request("POST", main.BACKGROUND_REMOVE_PATH, json.dumps(value).encode(), request_headers)

    def bestdori_post(
        self,
        path: str,
        value: object,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, object]]:
        request_headers = {
            "Origin": TEST_ORIGIN,
            "X-Requested-With": "homelab-dashboard",
            "Content-Type": "application/json",
        }
        if headers:
            request_headers.update(headers)
        return self.request("POST", path, json.dumps(value).encode(), request_headers)

    def test_health_and_get_contract(self) -> None:
        status, health = self.request("GET", main.HEALTH_PATH)
        self.assertEqual(status, 200)
        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["service"], main.SERVICE_NAME)
        self.assertEqual(health["version"], main.SERVICE_VERSION)
        status, preferences = self.request("GET", main.API_PATH)
        self.assertEqual(status, 200)
        self.assertEqual(preferences["landscape"], "bg.jpg")

    def test_bodyless_routes_reject_unread_bytes_and_request_smuggling(self) -> None:
        embedded = (
            b"GET /healthz HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"\r\n"
        )
        response = self.raw_exchange(
            b"GET /healthz HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            + f"Content-Length: {len(embedded)}\r\n".encode("ascii")
            + b"\r\n"
            + embedded
        )
        self.assertIn(b"HTTP/1.1 400 Bad Request", response)
        self.assertEqual(response.count(b"HTTP/1.1 "), 1)
        self.assertIn(b"Connection: close", response)

        for request in (
            (
                b"GET /missing HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Content-Length: 1\r\n\r\nx"
            ),
            (
                f"POST {main.CONTROL_PATH} HTTP/1.1\r\n".encode("ascii")
                + b"Host: localhost\r\n"
                b"Content-Length: 1\r\n\r\nx"
            ),
            (
                f"PUT {main.API_PATH} HTTP/1.1\r\n".encode("ascii")
                + b"Host: localhost\r\n"
                b"Content-Length: 1\r\n\r\nx"
            ),
        ):
            with self.subTest(request=request.split(b"\r\n", 1)[0]):
                response = self.raw_exchange(request)
                self.assertIn(b"HTTP/1.1 400 Bad Request", response)
                self.assertEqual(response.count(b"HTTP/1.1 "), 1)

    def test_ambiguous_http_framing_is_rejected_before_routing(self) -> None:
        requests = (
            (
                b"GET /healthz HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Content-Length: 0\r\n"
                b"Content-Length: 0\r\n\r\n"
            ),
            (
                b"GET /healthz HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Content-Length: 0\r\n"
                b"Content-Length: 1\r\n\r\nx"
            ),
            (
                b"GET /healthz HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Content-Length: 1, 1\r\n\r\nx"
            ),
            (
                b"GET /healthz HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Transfer-Encoding: chunked\r\n\r\n0\r\n\r\n"
            ),
            (
                b"GET /healthz HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Transfer-Encoding: chunked\r\n"
                b"Content-Length: 0\r\n\r\n0\r\n\r\n"
            ),
        )
        for request in requests:
            with self.subTest(request=request):
                response = self.raw_exchange(request)
                self.assertIn(b"HTTP/1.1 400 Bad Request", response)
                self.assertIn(b"Connection: close", response)
                self.assertEqual(response.count(b"HTTP/1.1 "), 1)

    def test_complete_requests_can_reuse_connections(self) -> None:
        connection = HTTPConnection(self.host, self.port, timeout=2)
        try:
            connection.request("GET", main.HEALTH_PATH)
            first = connection.getresponse()
            self.assertEqual(first.status, 200)
            first.read()

            connection.request("GET", main.API_PATH)
            second = connection.getresponse()
            self.assertEqual(second.status, 200)
            second.read()

            body = json.dumps({"mode": "landscape", "background": "test.jpg"}).encode()
            connection.request(
                "PATCH",
                main.API_PATH,
                body=body,
                headers={
                    "Origin": TEST_ORIGIN,
                    "X-Requested-With": "homelab-dashboard",
                    "Content-Type": "application/json",
                },
            )
            patched = connection.getresponse()
            self.assertEqual(patched.status, 200)
            patched.read()

            connection.request("GET", main.API_PATH)
            after_patch = connection.getresponse()
            self.assertEqual(after_patch.status, 200)
            after_patch.read()
        finally:
            connection.close()

        response = self.raw_exchange(
            b"GET /healthz HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Connection: close\r\n\r\n"
        )
        self.assertIn(b"HTTP/1.1 200 OK", response)
        self.assertIn(b"Connection: close", response)

    def test_incomplete_and_slow_request_bodies_time_out(self) -> None:
        body = b'{"mode":"landscape","background":"test.jpg"}'
        headers = (
            f"PATCH {main.API_PATH} HTTP/1.1\r\n".encode("ascii")
            + b"Host: localhost\r\n"
            + f"Origin: {TEST_ORIGIN}\r\n".encode("ascii")
            + b"X-Requested-With: homelab-dashboard\r\n"
            b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        )
        response = self.raw_exchange(
            headers + body[:5],
            shutdown_write=True,
        )
        self.assertIn(b"HTTP/1.1 408 Request Timeout", response)
        self.assertIn(b"Connection: close", response)

        with mock.patch.object(main, "HTTP_IDLE_TIMEOUT_SECONDS", 0.1):
            client = socket.create_connection((self.host, self.port), timeout=2)
            client.settimeout(2)
            try:
                client.sendall(headers + body[:1])
                response = client.recv(65536)
            finally:
                client.close()
        self.assertIn(b"HTTP/1.1 408 Request Timeout", response)

    def test_request_read_deadline_cannot_be_extended_by_trickle_input(self) -> None:
        body = b'{"mode":"landscape","background":"test.jpg"}'
        headers = (
            f"PATCH {main.API_PATH} HTTP/1.1\r\n".encode("ascii")
            + b"Host: localhost\r\n"
            + f"Origin: {TEST_ORIGIN}\r\n".encode("ascii")
            + b"X-Requested-With: homelab-dashboard\r\n"
            b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        )
        with mock.patch.object(main, "HTTP_IDLE_TIMEOUT_SECONDS", 0.2), mock.patch.object(
            main,
            "HTTP_REQUEST_READ_DEADLINE_SECONDS",
            0.35,
        ):
            client = socket.create_connection((self.host, self.port), timeout=2)
            client.settimeout(2)
            response = bytearray()
            sent = 0
            started = main.time.monotonic()
            try:
                client.sendall(headers)
                for byte in body:
                    try:
                        client.sendall(bytes([byte]))
                        sent += 1
                    except OSError:
                        break
                    threading.Event().wait(0.05)
                while True:
                    chunk = client.recv(65536)
                    if not chunk:
                        break
                    response.extend(chunk)
            except OSError:
                pass
            finally:
                client.close()
        elapsed = main.time.monotonic() - started
        self.assertLess(sent, len(body))
        self.assertLess(elapsed, 1.5)
        if response:
            self.assertIn(b"HTTP/1.1 408 Request Timeout", response)

    def test_slow_headers_and_connection_limit_are_bounded(self) -> None:
        with mock.patch.object(main, "HTTP_IDLE_TIMEOUT_SECONDS", 0.1):
            started = main.time.monotonic()
            response = self.raw_exchange(
                b"GET /healthz HTTP/1.1\r\nHost: local",
                timeout=1,
            )
            elapsed = main.time.monotonic() - started
        self.assertEqual(response, b"")
        self.assertLess(elapsed, 0.8)

        held_connections = []
        try:
            for _ in range(main.MAX_HTTP_CONNECTIONS):
                client = socket.create_connection((self.host, self.port), timeout=2)
                client.settimeout(2)
                client.sendall(b"GET /healthz HTTP/1.1\r\nHost: local")
                held_connections.append(client)
            threading.Event().wait(0.1)
            deadline = main.time.monotonic() + 2
            response = b""
            while main.time.monotonic() < deadline:
                response = self.raw_exchange(
                    b"GET /healthz HTTP/1.1\r\nHost: localhost\r\n\r\n",
                    timeout=0.2,
                )
                if b"HTTP/1.1 503 Service Unavailable" in response:
                    break
            self.assertIn(b"HTTP/1.1 503 Service Unavailable", response)
            self.assertIn(b"Connection: close", response)
        finally:
            for client in held_connections:
                client.close()

        deadline = main.time.monotonic() + 2
        while True:
            response = self.raw_exchange(
                b"GET /healthz HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Connection: close\r\n\r\n"
            )
            if b"HTTP/1.1 200 OK" in response or main.time.monotonic() >= deadline:
                break
            threading.Event().wait(0.02)
        self.assertIn(b"HTTP/1.1 200 OK", response)

    def test_expect_continue_is_sent_only_after_validation(self) -> None:
        body = b'{"mode":"landscape","background":"test.jpg"}'
        invalid = self.raw_exchange(
            f"PATCH {main.API_PATH} HTTP/1.1\r\n".encode("ascii")
            + b"Host: localhost\r\n"
            b"Origin: https://evil.test\r\n"
            b"X-Requested-With: homelab-dashboard\r\n"
            b"Content-Type: application/json\r\n"
            b"Expect: 100-continue\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        )
        self.assertIn(b"HTTP/1.1 403 Forbidden", invalid)
        self.assertNotIn(b"100 Continue", invalid)

        client = socket.create_connection((self.host, self.port), timeout=2)
        client.settimeout(2)
        try:
            client.sendall(
                f"PATCH {main.API_PATH} HTTP/1.1\r\n".encode("ascii")
                + b"Host: localhost\r\n"
                + f"Origin: {TEST_ORIGIN}\r\n".encode("ascii")
                + b"X-Requested-With: homelab-dashboard\r\n"
                b"Content-Type: application/json\r\n"
                b"Expect: 100-continue\r\n"
                b"Connection: close\r\n"
                + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
            )
            interim = client.recv(65536)
            self.assertIn(b"HTTP/1.1 100 Continue", interim)
            self.assertNotIn(b"HTTP/1.1 200 OK", interim)
            client.sendall(body)
            final = bytearray()
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                final.extend(chunk)
        finally:
            client.close()
        self.assertIn(b"HTTP/1.1 200 OK", final)

    def test_background_manifest_contract_partial_failure_and_methods(self) -> None:
        (self.project.backgrounds / "bg.jpg").write_bytes(image_bytes("JPEG"))
        with self.assertLogs(main.SERVICE_NAME, level="WARNING"):
            status, manifest = self.request("GET", main.BACKGROUND_FILES_PATH)
        self.assertEqual(status, 200)
        self.assertEqual(
            set(manifest),
            {"version", "backgrounds", "thumbnails", "total", "truncated"},
        )
        self.assertEqual(manifest["version"], 1)
        self.assertEqual(manifest["total"], 4)
        self.assertFalse(manifest["truncated"])
        self.assertEqual(manifest["backgrounds"][0]["name"], "bg.jpg")
        self.assertIsInstance(manifest["backgrounds"][0]["thumbnail"], str)
        self.assertEqual(
            manifest["thumbnails"],
            {"generated": 1, "reused": 0, "failed": 3, "deferred": 0},
        )
        failed = [item for item in manifest["backgrounds"] if item["thumbnail"] is None]
        self.assertEqual(len(failed), 3)

        with self.assertLogs(main.SERVICE_NAME, level="WARNING"):
            status, reused = self.request("GET", main.BACKGROUND_FILES_PATH)
        self.assertEqual(status, 200)
        self.assertEqual(
            reused["thumbnails"],
            {"generated": 0, "reused": 1, "failed": 3, "deferred": 0},
        )
        for method in ("POST", "PATCH", "DELETE", "HEAD", "OPTIONS"):
            with self.subTest(method=method):
                status, _ = self.request(method, main.BACKGROUND_FILES_PATH)
                self.assertEqual(status, 405)

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

    def test_background_upload_contract_and_collision_naming(self) -> None:
        payload = image_bytes()
        status, uploaded = self.upload("from-browser.png", payload)
        self.assertEqual(status, 201)
        self.assertEqual(
            uploaded,
            {
                "background": "from-browser.png",
                "size": len(payload),
                "width": 3,
                "height": 2,
            },
        )
        self.assertEqual((self.project.backgrounds / "from-browser.png").read_bytes(), payload)

        status, duplicate = self.upload("from-browser.png", payload)
        self.assertEqual(status, 201)
        self.assertEqual(duplicate["background"], "from-browser (2).png")
        self.assertEqual(self.store.get()["landscape"], "bg.jpg")

    def test_background_upload_request_validation(self) -> None:
        payload = image_bytes()
        cases = (
            ("from-browser.png", payload, {"Origin": "https://evil.test"}, 403),
            ("from-browser.png", payload, {"X-Requested-With": "wrong"}, 403),
            ("from-browser.png", payload, {"Content-Type": "image/png"}, 415),
            ("../from-browser.png", payload, None, 400),
            ("from-browser.jpg", payload, None, 400),
            ("from-browser.png", b"not an image", None, 400),
            ("from-browser.png&extra=value", payload, None, 400),
        )
        for name, body, headers, expected in cases:
            with self.subTest(name=name, headers=headers):
                status, _ = self.upload(name, body, headers)
                self.assertEqual(status, expected)

        connection = HTTPConnection(self.host, self.port, timeout=2)
        connection.putrequest("POST", f"{main.BACKGROUND_UPLOAD_PATH}?name=too-large.png")
        connection.putheader("Origin", TEST_ORIGIN)
        connection.putheader("X-Requested-With", "homelab-dashboard")
        connection.putheader("Content-Type", "application/octet-stream")
        connection.putheader("Content-Length", str(main.MAX_BACKGROUND_UPLOAD_BODY + 1))
        connection.endheaders()
        response = connection.getresponse()
        self.assertEqual(response.status, 413)
        response.read()
        connection.close()

        status, _ = self.request("GET", main.BACKGROUND_UPLOAD_PATH)
        self.assertEqual(status, 405)

        with mock.patch.object(
            self.store,
            "upload_background",
            side_effect=main.BackgroundQuotaExceeded("full"),
        ):
            status, quota = self.upload("from-browser.png", payload)
        self.assertEqual(status, 409)
        self.assertEqual(quota["error"], "background_file_quota_exceeded")

    def test_background_remove_trash_and_permanent_fallback_contract(self) -> None:
        self.store.update("landscape", "test.jpg")
        self.store.update("portrait", "test.jpg")

        with mock.patch.object(backgrounds, "send2trash", side_effect=OSError("trash unavailable")):
            status, conflict = self.remove({"background": "test.jpg"})
        self.assertEqual(status, 409)
        self.assertEqual(conflict["error"], "trash_unavailable")
        self.assertEqual(conflict["expiresIn"], main.REMOVAL_TOKEN_TTL_SECONDS)
        self.assertIsInstance(conflict["confirmationToken"], str)
        self.assertTrue((self.project.backgrounds / "test.jpg").exists())

        status, removed = self.remove(
            {
                "background": "test.jpg",
                "confirmationToken": conflict["confirmationToken"],
            }
        )
        self.assertEqual(status, 200)
        self.assertEqual(removed["background"], "test.jpg")
        self.assertEqual(removed["disposition"], "permanent")
        self.assertEqual(removed["preferences"]["landscape"], "bg.jpg")
        self.assertEqual(removed["preferences"]["portrait"], "bg.jpg")
        self.assertFalse((self.project.backgrounds / "test.jpg").exists())

    def test_background_remove_validation_and_trash_success(self) -> None:
        cases = (
            ({"background": "bg.jpg"}, None, 400),
            ({"background": "../test.jpg"}, None, 400),
            ({"background": "missing.jpg"}, None, 404),
            ({"background": "test.jpg", "confirmationToken": "invalid"}, None, 403),
            ({"background": "test.jpg", "extra": True}, None, 400),
            ({"background": ["test.jpg"]}, None, 400),
            ({"background": "test.jpg"}, {"Origin": "https://evil.test"}, 403),
            ({"background": "test.jpg"}, {"Content-Type": "text/plain"}, 415),
        )
        for value, headers, expected in cases:
            with self.subTest(value=value, headers=headers):
                status, _ = self.remove(value, headers)
                self.assertEqual(status, expected)

        self.store.update("landscape", "alternate.webp")

        def simulate_trash(path: str) -> None:
            Path(path).unlink()

        with mock.patch.object(backgrounds, "send2trash", side_effect=simulate_trash):
            status, removed = self.remove({"background": "alternate.webp"})
        self.assertEqual(status, 200)
        self.assertEqual(removed["disposition"], "trash")
        self.assertEqual(removed["preferences"]["landscape"], "bg.jpg")

        status, _ = self.request("GET", main.BACKGROUND_REMOVE_PATH)
        self.assertEqual(status, 405)

    def test_bestdori_characters_draw_and_history_contracts(self) -> None:
        status, characters = self.request("GET", main.BESTDORI_CHARACTERS_PATH)
        self.assertEqual(status, 200)
        self.assertEqual(characters["characters"][0], {"id": 1, "name": "户山香澄"})

        status, drawn = self.bestdori_post(
            main.BESTDORI_DRAW_PATH,
            {"mode": "character", "rarities": [3, 5], "characterId": 1},
        )
        self.assertEqual(status, 201)
        self.assertEqual(drawn["background"]["background"], "户山香澄_五星_测试卡面_101_after_training.png")
        self.assertEqual(drawn["card"]["id"], 101)
        self.assertEqual(self.server.bestdori_manager.draw_request, ("character", {3, 5}, 1))

        status, reconciled = self.bestdori_post(main.BESTDORI_RECONCILE_PATH, {})
        self.assertEqual(status, 200)
        self.assertEqual(reconciled, {"removed": 1, "remaining": 2})
        status, reset = self.bestdori_post(main.BESTDORI_RESET_PATH, {})
        self.assertEqual(status, 200)
        self.assertEqual(reset, {"removed": 3, "remaining": 0})

    def test_bestdori_request_validation_and_security(self) -> None:
        valid_random = {"mode": "random", "rarities": [4, 5]}
        cases = (
            (valid_random, {"Origin": "https://evil.test"}, 403),
            (valid_random, {"X-Requested-With": "wrong"}, 403),
            (valid_random, {"Content-Type": "text/plain"}, 415),
            ({"mode": "random", "rarities": []}, None, 400),
            ({"mode": "random", "rarities": [3, 3]}, None, 400),
            ({"mode": "random", "rarities": [2]}, None, 400),
            ({"mode": "random", "rarities": [True]}, None, 400),
            ({"mode": "random", "rarities": [3], "characterId": 1}, None, 400),
            ({"mode": "character", "rarities": [3]}, None, 400),
            ({"mode": "character", "rarities": [3], "characterId": 41}, None, 400),
            ({"mode": "character", "rarities": [3], "characterId": True}, None, 400),
        )
        for value, headers, expected in cases:
            with self.subTest(value=value, headers=headers):
                status, _ = self.bestdori_post(main.BESTDORI_DRAW_PATH, value, headers)
                self.assertEqual(status, expected)

        for path in (main.BESTDORI_RECONCILE_PATH, main.BESTDORI_RESET_PATH):
            with self.subTest(path=path):
                status, _ = self.bestdori_post(path, {"unexpected": True})
                self.assertEqual(status, 400)
                status, _ = self.request("GET", path)
                self.assertEqual(status, 405)
        status, _ = self.request("POST", main.BESTDORI_DRAW_PATH, b"{}", {})
        self.assertEqual(status, 403)
        status, _ = self.request("POST", main.BESTDORI_CHARACTERS_PATH)
        self.assertEqual(status, 405)

    def test_all_dashboard_json_endpoints_reject_null_without_hanging(self) -> None:
        dashboard_headers = {
            "Origin": TEST_ORIGIN,
            "X-Requested-With": "homelab-dashboard",
            "Content-Type": "application/json",
        }
        endpoints = (
            ("PATCH", main.API_PATH),
            ("POST", main.BACKGROUND_REMOVE_PATH),
            ("POST", main.SERVICE_STATUS_PATH),
            ("POST", main.BESTDORI_DRAW_PATH),
            ("POST", main.BESTDORI_RECONCILE_PATH),
            ("POST", main.BESTDORI_RESET_PATH),
        )
        for method, path in endpoints:
            with self.subTest(method=method, path=path):
                status, payload = self.request(method, path, b"null", dashboard_headers)
                self.assertEqual(status, 400)
                self.assertIn("null", payload["error"])

    def test_bestdori_failures_map_to_stable_http_errors(self) -> None:
        for error, expected_status, expected_message in (
            (main.BestdoriDrawBusy("busy"), 409, "bestdori_operation_in_progress"),
            (main.BestdoriPoolExhausted("empty"), 409, "bestdori_card_pool_exhausted"),
            (main.BestdoriHistoryError("broken"), 503, "bestdori_history_unavailable"),
            (main.BestdoriUnavailable("offline"), 502, "bestdori_unavailable"),
            (main.InvalidImage("bad"), 502, "bestdori_invalid_image"),
            (main.BackgroundQuotaExceeded("full"), 409, "background_file_quota_exceeded"),
        ):
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(self.server.bestdori_manager, "draw", side_effect=error):
                    status, payload = self.bestdori_post(
                        main.BESTDORI_DRAW_PATH,
                        {"mode": "random", "rarities": [3]},
                    )
                self.assertEqual(status, expected_status)
                self.assertEqual(payload["error"], expected_message)

    def test_service_status_contract_and_partial_failure(self) -> None:
        targets = ["https://one.home.example.test/", "https://two.home.example.test/"]
        status, payload = self.service_status({"targets": targets})
        self.assertEqual(status, 200)
        self.assertEqual(self.server.probe_client.targets, targets)
        self.assertEqual(
            payload["results"],
            [
                {"target": targets[0], "status": 200, "latencyMs": 10, "error": None},
                {"target": targets[1], "status": None, "latencyMs": 11, "error": "timeout"},
            ],
        )

    def test_service_status_request_validation(self) -> None:
        for count in (1, 11, 32):
            with self.subTest(count=count):
                targets = [f"https://service{index}.home.example.test/" for index in range(count)]
                status, payload = self.service_status({"targets": targets})
                self.assertEqual(status, 200)
                self.assertEqual(len(payload["results"]), count)

        cases = (
            ({}, None, 400),
            ({"targets": []}, None, 400),
            ({"targets": [1]}, None, 400),
            ({"targets": ["https://one.home.example.test/"] * 33}, None, 400),
            ({"targets": ["https://one.home.example.test/"]}, {"Origin": "https://evil.test"}, 403),
            ({"targets": ["https://one.home.example.test/"]}, {"X-Requested-With": "wrong"}, 403),
            ({"targets": ["https://one.home.example.test/"]}, {"Content-Type": "text/plain"}, 415),
        )
        for value, headers, expected in cases:
            with self.subTest(value=value, headers=headers):
                status, _ = self.service_status(value, headers)
                self.assertEqual(status, expected)

        status, _ = self.request("GET", main.SERVICE_STATUS_PATH)
        self.assertEqual(status, 405)

        dashboard_headers = {
            "Origin": TEST_ORIGIN,
            "X-Requested-With": "homelab-dashboard",
            "Content-Type": "application/json",
        }
        status, _ = self.request(
            "POST",
            main.SERVICE_STATUS_PATH,
            b'{"targets":[],"targets":[]}',
            dashboard_headers,
        )
        self.assertEqual(status, 400)
        status, _ = self.request(
            "POST",
            main.SERVICE_STATUS_PATH,
            b"x" * (main.MAX_SERVICE_STATUS_BODY + 1),
            dashboard_headers,
        )
        self.assertEqual(status, 413)

    def test_control_requires_token_and_shuts_down(self) -> None:
        status, _ = self.request("POST", main.CONTROL_PATH, headers={"X-Control-Token": "wrong"})
        self.assertEqual(status, 403)
        status, payload = self.request(
            "POST", main.CONTROL_PATH, headers={"X-Control-Token": self.config.control_token}
        )
        self.assertEqual(status, 202)
        self.assertEqual(payload["status"], "stopping")


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
