from __future__ import annotations

import json
import logging
import socket
import threading
import unittest
from dataclasses import replace
from http.client import HTTPConnection
from pathlib import Path
from unittest import mock

from homelab_state import backgrounds, bestdori, bing, cards, core, favicon, http_api, probe
from tests.helpers import (
    FIXED_TIME,
    TEST_ORIGIN,
    TemporaryProject,
    image_bytes,
)


class FakeProbeClient:
    def __init__(self) -> None:
        self.executor = object()
        self.targets: list[str] = []

    def probe_many(self, targets: list[str]) -> list[probe.ProbeResult]:
        self.targets = list(targets)
        return [probe.ProbeResult(target, 200 if index == 0 else None, 10 + index, None if index == 0 else "timeout")
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


class FakeBingManager:
    def __init__(self) -> None:
        self.calls = 0

    def fetch_daily(self) -> dict[str, object]:
        self.calls += 1
        return {
            "version": 1,
            "results": [
                {
                    "slot": "cn",
                    "market": "zh-CN",
                    "status": "created",
                    "startdate": "20260820",
                    "imageId": "OHR.China_ZH-CN100",
                    "variant": "UHD",
                    "background": "bing_cn_20260820_OHR.China_ZH-CN100.jpg",
                },
                {
                    "slot": "international",
                    "market": "ROW",
                    "fallback": False,
                    "status": "existing",
                    "startdate": "20260820",
                    "imageId": "OHR.World_ROW200",
                    "background": "bing_row_20260820_OHR.World_ROW200.jpg",
                },
            ],
        }


def card_initialize_request() -> dict[str, object]:
    icon = '<svg viewBox="0 0 24 24"><path d="M1 1h22v22H1z"/></svg>'
    return {
        "version": 1,
        "services": [
            {
                "id": "synology-nas",
                "iconSvg": icon,
                "name": "Synology NAS",
                "description": "数据存储中心",
                "url": "https://nas.home.example.test/",
                "probeUrl": None,
            }
        ],
        "quickLinks": [
            {
                "id": "github",
                "iconSvg": icon,
                "name": "GitHub",
                "url": "https://github.com/",
            }
        ],
    }


class APITests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = TemporaryProject()
        self.config = replace(
            self.project.write_config(listen="127.0.0.1:1"),
            listen_port=0,
        )
        self.store = backgrounds.StateStore(self.config.background_dir, self.config.state_file, lambda: FIXED_TIME)
        self.cards_store = cards.CardsStore.for_state_directory(
            self.config.state_dir,
            self.config.probe_domain_suffix,
            lambda: FIXED_TIME,
        )
        self.favicon_store = favicon.FaviconStore.for_state_directory(
            self.config.state_dir,
            lambda: FIXED_TIME,
        )
        logger = logging.getLogger(f"test-{id(self)}")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        self.server = http_api.StateHTTPServer(
            self.config,
            self.store,
            logger,
            FakeProbeClient(),
            FakeBestdoriManager(),
            self.cards_store,
            FakeBingManager(),
            self.favicon_store,
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

    def raw_http_request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes, dict[str, str]]:
        connection = HTTPConnection(self.host, self.port, timeout=2)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        payload = response.read()
        response_headers = {name.lower(): value for name, value in response.getheaders()}
        status = response.status
        connection.close()
        return status, payload, response_headers

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
        return self.request("PATCH", http_api.API_PATH, json.dumps(value).encode(), request_headers)

    def service_status(self, value: object, headers: dict[str, str] | None = None) -> tuple[int, dict[str, object]]:
        request_headers = {
            "Origin": TEST_ORIGIN,
            "X-Requested-With": "homelab-dashboard",
            "Content-Type": "application/json",
        }
        if headers:
            request_headers.update(headers)
        return self.request("POST", http_api.SERVICE_STATUS_PATH, json.dumps(value).encode(), request_headers)

    def cards_request(
        self,
        method: str,
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
        return self.request(method, path, json.dumps(value).encode(), request_headers)

    def favicon_request(
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
        return self.request(
            "PATCH",
            http_api.FAVICON_PATH,
            json.dumps(value).encode(),
            request_headers,
        )

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
        return self.request("POST", f"{http_api.BACKGROUND_UPLOAD_PATH}?name={name}", body, request_headers)

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
        return self.request("POST", http_api.BACKGROUND_REMOVE_PATH, json.dumps(value).encode(), request_headers)

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

    def bing_post(
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
        return self.request(
            "POST",
            http_api.BING_DAILY_PATH,
            json.dumps(value).encode(),
            request_headers,
        )

    def test_health_and_get_contract(self) -> None:
        status, health = self.request("GET", http_api.HEALTH_PATH)
        self.assertEqual(status, 200)
        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["service"], core.SERVICE_NAME)
        self.assertEqual(health["version"], core.SERVICE_VERSION)
        status, preferences = self.request("GET", http_api.API_PATH)
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
                f"POST {http_api.CONTROL_PATH} HTTP/1.1\r\n".encode("ascii")
                + b"Host: localhost\r\n"
                b"Content-Length: 1\r\n\r\nx"
            ),
            (
                f"PUT {http_api.API_PATH} HTTP/1.1\r\n".encode("ascii")
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
            connection.request("GET", http_api.HEALTH_PATH)
            first = connection.getresponse()
            self.assertEqual(first.status, 200)
            first.read()

            connection.request("GET", http_api.API_PATH)
            second = connection.getresponse()
            self.assertEqual(second.status, 200)
            second.read()

            body = json.dumps({"mode": "landscape", "background": "test.jpg"}).encode()
            connection.request(
                "PATCH",
                http_api.API_PATH,
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

            connection.request("GET", http_api.API_PATH)
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
            f"PATCH {http_api.API_PATH} HTTP/1.1\r\n".encode("ascii")
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

        with mock.patch.object(http_api, "HTTP_IDLE_TIMEOUT_SECONDS", 0.1):
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
            f"PATCH {http_api.API_PATH} HTTP/1.1\r\n".encode("ascii")
            + b"Host: localhost\r\n"
            + f"Origin: {TEST_ORIGIN}\r\n".encode("ascii")
            + b"X-Requested-With: homelab-dashboard\r\n"
            b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        )
        with mock.patch.object(http_api, "HTTP_IDLE_TIMEOUT_SECONDS", 0.2), mock.patch.object(
            http_api,
            "HTTP_REQUEST_READ_DEADLINE_SECONDS",
            0.35,
        ):
            client = socket.create_connection((self.host, self.port), timeout=2)
            client.settimeout(2)
            response = bytearray()
            sent = 0
            started = http_api.time.monotonic()
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
        elapsed = http_api.time.monotonic() - started
        self.assertLess(sent, len(body))
        self.assertLess(elapsed, 1.5)
        if response:
            self.assertIn(b"HTTP/1.1 408 Request Timeout", response)

    def test_slow_headers_and_connection_limit_are_bounded(self) -> None:
        with mock.patch.object(http_api, "HTTP_IDLE_TIMEOUT_SECONDS", 0.1):
            started = http_api.time.monotonic()
            response = self.raw_exchange(
                b"GET /healthz HTTP/1.1\r\nHost: local",
                timeout=1,
            )
            elapsed = http_api.time.monotonic() - started
        self.assertEqual(response, b"")
        self.assertLess(elapsed, 0.8)

        held_connections = []
        try:
            for _ in range(http_api.MAX_HTTP_CONNECTIONS):
                client = socket.create_connection((self.host, self.port), timeout=2)
                client.settimeout(2)
                client.sendall(b"GET /healthz HTTP/1.1\r\nHost: local")
                held_connections.append(client)
            threading.Event().wait(0.1)
            deadline = http_api.time.monotonic() + 2
            response = b""
            while http_api.time.monotonic() < deadline:
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

        deadline = http_api.time.monotonic() + 2
        while True:
            response = self.raw_exchange(
                b"GET /healthz HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Connection: close\r\n\r\n"
            )
            if b"HTTP/1.1 200 OK" in response or http_api.time.monotonic() >= deadline:
                break
            threading.Event().wait(0.02)
        self.assertIn(b"HTTP/1.1 200 OK", response)

    def test_expect_continue_is_sent_only_after_validation(self) -> None:
        body = b'{"mode":"landscape","background":"test.jpg"}'
        invalid = self.raw_exchange(
            f"PATCH {http_api.API_PATH} HTTP/1.1\r\n".encode("ascii")
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
                f"PATCH {http_api.API_PATH} HTTP/1.1\r\n".encode("ascii")
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
        with self.assertLogs(core.SERVICE_NAME, level="WARNING"):
            status, manifest = self.request("GET", http_api.BACKGROUND_FILES_PATH)
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

        with self.assertLogs(core.SERVICE_NAME, level="WARNING"):
            status, reused = self.request("GET", http_api.BACKGROUND_FILES_PATH)
        self.assertEqual(status, 200)
        self.assertEqual(
            reused["thumbnails"],
            {"generated": 0, "reused": 1, "failed": 3, "deferred": 0},
        )
        for method in ("POST", "PATCH", "DELETE", "HEAD", "OPTIONS"):
            with self.subTest(method=method):
                status, _ = self.request(method, http_api.BACKGROUND_FILES_PATH)
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

    def test_cards_initialize_get_update_and_conflict_contract(self) -> None:
        status, initial = self.request("GET", http_api.CARDS_PATH)
        self.assertEqual(status, 200)
        self.assertEqual(initial, {"version": 1, "initialized": False, "channels": None})
        self.assertFalse((self.config.state_dir / cards.CARDS_STATE_FILE_NAME).exists())

        status, not_initialized = self.cards_request(
            "PATCH",
            http_api.CARDS_PATH,
            {"version": 1, "channel": "services", "expectedRevision": 1, "cards": []},
        )
        self.assertEqual(status, 409)
        self.assertEqual(not_initialized, {"error": "not_initialized"})

        status, initialized = self.cards_request(
            "POST",
            http_api.CARDS_INITIALIZE_PATH,
            card_initialize_request(),
        )
        self.assertEqual(status, 201)
        self.assertTrue(initialized["initialized"])
        self.assertEqual(initialized["channels"]["services"]["revision"], 1)
        self.assertEqual(initialized["channels"]["quickLinks"]["revision"], 1)
        self.assertEqual(initialized["channels"]["services"]["updatedAt"], FIXED_TIME)
        self.assertIn(
            'xmlns="http://www.w3.org/2000/svg"',
            initialized["channels"]["services"]["cards"][0]["iconSvg"],
        )

        status, fetched = self.request("GET", http_api.CARDS_PATH)
        self.assertEqual(status, 200)
        self.assertEqual(fetched, initialized)

        status, duplicate = self.cards_request(
            "POST",
            http_api.CARDS_INITIALIZE_PATH,
            card_initialize_request(),
        )
        self.assertEqual(status, 409)
        self.assertEqual(duplicate, {"error": "already_initialized"})

        status, updated = self.cards_request(
            "PATCH",
            http_api.CARDS_PATH,
            {"version": 1, "channel": "quickLinks", "expectedRevision": 1, "cards": []},
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["channels"]["quickLinks"]["revision"], 2)
        self.assertEqual(updated["channels"]["quickLinks"]["cards"], [])
        self.assertEqual(updated["channels"]["services"], initialized["channels"]["services"])

        status, conflict = self.cards_request(
            "PATCH",
            http_api.CARDS_PATH,
            {"version": 1, "channel": "quickLinks", "expectedRevision": 1, "cards": []},
        )
        self.assertEqual(status, 409)
        self.assertEqual(conflict, {"error": "revision_conflict", "currentRevision": 2})

    def test_cards_update_validation_identifies_the_rejected_payload_card(self) -> None:
        invalid_initialize = card_initialize_request()
        invalid_initialize["services"][0]["probeUrl"] = "https://example.com/"
        status, initialize_error = self.cards_request(
            "POST",
            http_api.CARDS_INITIALIZE_PATH,
            invalid_initialize,
        )
        self.assertEqual(status, 400)
        self.assertEqual(
            initialize_error,
            {"error": "effective service probe URL is not allowed by the configured probe policy"},
        )

        status, _ = self.cards_request(
            "POST",
            http_api.CARDS_INITIALIZE_PATH,
            card_initialize_request(),
        )
        self.assertEqual(status, 201)

        icon = '<svg viewBox="0 0 24 24"><path d="M1 1h22v22H1z"/></svg>'
        card_a = {
            "id": "card-a",
            "iconSvg": icon,
            "name": "Card A",
            "description": "Invalid probe target",
            "url": "https://a.home.example.test/",
            "probeUrl": "https://example.com/",
        }
        card_b = {
            "id": "card-b",
            "iconSvg": icon,
            "name": "Card B",
            "description": "Currently selected in the editor",
            "url": "https://b.home.example.test/",
            "probeUrl": None,
        }
        status, update_error = self.cards_request(
            "PATCH",
            http_api.CARDS_PATH,
            {
                "version": 1,
                "channel": "services",
                "expectedRevision": 1,
                "cards": [card_a, card_b],
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(
            update_error,
            {
                "error": "effective service probe URL is not allowed by the configured probe policy",
                "cardId": "card-a",
            },
        )

        status, request_error = self.cards_request(
            "PATCH",
            http_api.CARDS_PATH,
            {
                "version": 1,
                "channel": "services",
                "expectedRevision": 0,
                "cards": [card_b],
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(
            request_error,
            {"error": "expectedRevision must be a positive integer"},
        )

    def test_cards_write_validation_security_limits_and_methods(self) -> None:
        valid = card_initialize_request()
        cases = (
            ("POST", http_api.CARDS_INITIALIZE_PATH, valid, {"Origin": "https://evil.test"}, 403),
            ("POST", http_api.CARDS_INITIALIZE_PATH, valid, {"X-Requested-With": "wrong"}, 403),
            ("POST", http_api.CARDS_INITIALIZE_PATH, valid, {"Content-Type": "text/plain"}, 415),
            (
                "POST",
                http_api.CARDS_INITIALIZE_PATH,
                {**valid, "unexpected": True},
                None,
                400,
            ),
            (
                "POST",
                http_api.CARDS_INITIALIZE_PATH,
                {**valid, "version": True},
                None,
                400,
            ),
        )
        for method, path, value, headers, expected in cases:
            with self.subTest(method=method, headers=headers, expected=expected):
                status, _ = self.cards_request(method, path, value, headers)
                self.assertEqual(status, expected)

        dashboard_headers = {
            "Origin": TEST_ORIGIN,
            "X-Requested-With": "homelab-dashboard",
            "Content-Type": "application/json",
        }
        duplicate_json = (
            b'{"version":1,"services":[],"services":[],"quickLinks":[]}'
        )
        status, _ = self.request(
            "POST",
            http_api.CARDS_INITIALIZE_PATH,
            duplicate_json,
            dashboard_headers,
        )
        self.assertEqual(status, 400)

        connection = HTTPConnection(self.host, self.port, timeout=2)
        connection.putrequest("POST", http_api.CARDS_INITIALIZE_PATH)
        for name, value in dashboard_headers.items():
            connection.putheader(name, value)
        connection.putheader("Content-Length", str(cards.MAX_CARDS_REQUEST_BODY + 1))
        connection.endheaders()
        response = connection.getresponse()
        self.assertEqual(response.status, 413)
        response.read()
        connection.close()

        for method, path in (
            ("POST", http_api.CARDS_PATH),
            ("PUT", http_api.CARDS_PATH),
            ("DELETE", http_api.CARDS_PATH),
            ("GET", http_api.CARDS_INITIALIZE_PATH),
            ("PATCH", http_api.CARDS_INITIALIZE_PATH),
            ("PUT", http_api.CARDS_INITIALIZE_PATH),
        ):
            with self.subTest(method=method, path=path):
                status, _ = self.request(method, path)
                self.assertEqual(status, 405)

    def test_cards_corrupt_state_maps_to_unavailable_without_affecting_health(self) -> None:
        with mock.patch.object(
            self.server.cards_store,
            "get",
            side_effect=cards.CardsStateUnavailable("damaged"),
        ):
            status, payload = self.request("GET", http_api.CARDS_PATH)
        self.assertEqual(status, 503)
        self.assertEqual(payload, {"error": "cards_state_unavailable"})
        status, health = self.request("GET", http_api.HEALTH_PATH)
        self.assertEqual(status, 200)
        self.assertEqual(health["status"], "ok")

    def test_favicon_get_upload_asset_replace_remove_and_cache_contract(self) -> None:
        status, initial = self.request("GET", http_api.FAVICON_PATH)
        self.assertEqual(status, 200)
        self.assertEqual(
            initial,
            {
                "version": 1,
                "configured": False,
                "updatedAt": None,
                "contentHash": None,
            },
        )
        self.assertFalse((self.config.state_dir / favicon.FAVICON_STATE_FILE_NAME).exists())

        first_svg = '<svg viewBox="0 0 24 24"><path d="M1 1h22v22H1z"/></svg>'
        status, created = self.favicon_request({"version": 1, "iconSvg": first_svg})
        self.assertEqual(status, 200)
        self.assertTrue(created["configured"])
        first_hash = created["contentHash"]
        self.assertRegex(first_hash, r"^[0-9a-f]{64}$")
        self.assertTrue((self.config.state_dir / favicon.FAVICON_STATE_FILE_NAME).is_file())

        asset_path = f"{http_api.FAVICON_ASSET_PATH}?v={first_hash}"
        status, body, headers = self.raw_http_request("GET", asset_path)
        self.assertEqual(status, 200)
        self.assertIn(b'<svg xmlns="http://www.w3.org/2000/svg"', body)
        self.assertEqual(headers["content-type"], "image/svg+xml; charset=utf-8")
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertEqual(headers["cache-control"], "private, max-age=31536000, immutable")
        self.assertEqual(headers["etag"], f'"sha256-{first_hash}"')

        status, not_modified_body, not_modified_headers = self.raw_http_request(
            "GET",
            asset_path,
            headers={"If-None-Match": headers["etag"]},
        )
        self.assertEqual(status, 304)
        self.assertEqual(not_modified_body, b"")
        self.assertEqual(not_modified_headers["etag"], headers["etag"])

        second_svg = '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/></svg>'
        status, replaced = self.favicon_request({"version": 1, "iconSvg": second_svg})
        self.assertEqual(status, 200)
        self.assertNotEqual(replaced["contentHash"], first_hash)
        status, stale = self.request("GET", asset_path)
        self.assertEqual(status, 404)
        self.assertEqual(stale, {"error": "favicon_not_found"})

        status, removed = self.favicon_request({"version": 1, "iconSvg": None})
        self.assertEqual(status, 200)
        self.assertEqual(
            removed,
            {
                "version": 1,
                "configured": False,
                "updatedAt": FIXED_TIME,
                "contentHash": None,
            },
        )
        self.assertTrue((self.config.state_dir / favicon.FAVICON_STATE_FILE_NAME).is_file())
        status, _ = self.request(
            "GET",
            f"{http_api.FAVICON_ASSET_PATH}?v={replaced['contentHash']}",
        )
        self.assertEqual(status, 404)

    def test_page_icon_is_independent_persistent_and_uses_hashed_assets(self) -> None:
        first_svg = '<svg viewBox="0 0 24 24"><path d="M1 1h22v22H1z"/></svg>'
        second_svg = '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/></svg>'
        _, favicon_state = self.favicon_request({"version": 1, "iconSvg": first_svg})
        favicon_file = self.config.state_dir / favicon.FAVICON_STATE_FILE_NAME
        favicon_before = favicon_file.read_bytes()
        headers = {
            "Origin": TEST_ORIGIN,
            "X-Requested-With": "homelab-dashboard",
            "Content-Type": "application/json",
        }
        status, initial = self.request("GET", http_api.PAGE_ICON_PATH)
        self.assertEqual(status, 200)
        self.assertFalse(initial["configured"])
        page_file = self.config.state_dir / favicon.PAGE_ICON_STATE_FILE_NAME
        self.assertFalse(page_file.exists())
        status, created = self.request(
            "PATCH", http_api.PAGE_ICON_PATH,
            json.dumps({"version": 1, "iconSvg": second_svg}).encode(), headers,
        )
        self.assertEqual(status, 200)
        self.assertTrue(created["configured"])
        asset_path = f"{http_api.PAGE_ICON_ASSET_PATH}?v={created['contentHash']}"
        status, body, asset_headers = self.raw_http_request("GET", asset_path)
        self.assertEqual(status, 200)
        self.assertIn(b"<circle", body)
        self.assertEqual(asset_headers["content-type"], "image/svg+xml; charset=utf-8")
        self.assertEqual(asset_headers["x-content-type-options"], "nosniff")
        status, _, _ = self.raw_http_request(
            "GET", asset_path, headers={"If-None-Match": asset_headers["etag"]},
        )
        self.assertEqual(status, 304)
        self.assertEqual(favicon.FaviconStore(page_file).get(), created)
        self.assertEqual(self.request("GET", http_api.FAVICON_PATH)[1], favicon_state)
        self.assertEqual(favicon_file.read_bytes(), favicon_before)
        status, removed = self.request(
            "PATCH", http_api.PAGE_ICON_PATH,
            json.dumps({"version": 1, "iconSvg": None}).encode(), headers,
        )
        self.assertEqual(status, 200)
        self.assertFalse(removed["configured"])
        self.assertEqual(favicon.FaviconStore(page_file).get(), removed)
        self.assertEqual(self.request("GET", asset_path)[0], 404)
        self.assertEqual(favicon_file.read_bytes(), favicon_before)

    def test_page_icon_reuses_validation_guards_and_isolates_failures(self) -> None:
        headers = {
            "Origin": TEST_ORIGIN,
            "X-Requested-With": "homelab-dashboard",
            "Content-Type": "application/json",
        }
        for value, overrides, expected in (
            ({"version": 1, "iconSvg": None}, {"Origin": "https://evil.test"}, 403),
            ({"version": 1, "iconSvg": None}, {"X-Requested-With": "wrong"}, 403),
            ({"version": 1, "iconSvg": None}, {"Content-Type": "text/plain"}, 415),
            ({"version": True, "iconSvg": None}, {}, 400),
            ({"version": 1, "iconSvg": '<svg onload="alert(1)"><path d="M0 0"/></svg>'}, {}, 400),
        ):
            with self.subTest(value=value, overrides=overrides):
                status, _ = self.request(
                    "PATCH", http_api.PAGE_ICON_PATH, json.dumps(value).encode(), headers | overrides,
                )
                self.assertEqual(status, expected)
        for method in ("POST", "PUT", "DELETE", "HEAD"):
            self.assertEqual(self.request(method, http_api.PAGE_ICON_PATH)[0], 405)
        for method in ("PATCH", "POST", "PUT", "DELETE", "HEAD"):
            self.assertEqual(self.request(method, http_api.PAGE_ICON_ASSET_PATH)[0], 405)
        for query in ("", "?v=", "?v=" + "0" * 64 + "&extra=1"):
            self.assertEqual(self.request("GET", http_api.PAGE_ICON_ASSET_PATH + query)[0], 404)
        with mock.patch.object(
            self.server.page_icon_store, "get",
            side_effect=favicon.FaviconStateUnavailable("damaged"),
        ):
            status, payload = self.request("GET", http_api.PAGE_ICON_PATH)
            self.assertEqual(status, 503)
            self.assertEqual(payload, {"error": "page_icon_state_unavailable"})
            self.assertEqual(self.request("GET", http_api.FAVICON_PATH)[0], 200)
            self.assertEqual(self.request("GET", http_api.HEALTH_PATH)[0], 200)

    def test_favicon_validation_body_limit_queries_and_methods(self) -> None:
        valid_svg = '<svg viewBox="0 0 24 24"><path d="M0 0h24v24H0z"/></svg>'
        cases = (
            ({"version": 1, "iconSvg": valid_svg}, {"Origin": "https://evil.test"}, 403),
            ({"version": 1, "iconSvg": valid_svg}, {"X-Requested-With": "wrong"}, 403),
            ({"version": 1, "iconSvg": valid_svg}, {"Content-Type": "text/plain"}, 415),
            ({"version": True, "iconSvg": valid_svg}, None, 400),
            ({"version": 1, "iconSvg": valid_svg, "extra": True}, None, 400),
            (
                {"version": 1, "iconSvg": '<svg onload="alert(1)"><path d="M0 0"/></svg>'},
                None,
                400,
            ),
        )
        for value, headers, expected in cases:
            with self.subTest(value=value, headers=headers):
                status, _ = self.favicon_request(value, headers)
                self.assertEqual(status, expected)

        dashboard_headers = {
            "Origin": TEST_ORIGIN,
            "X-Requested-With": "homelab-dashboard",
            "Content-Type": "application/json",
        }
        status, _ = self.request(
            "PATCH",
            http_api.FAVICON_PATH,
            b'{"version":1,"version":1,"iconSvg":null}',
            dashboard_headers,
        )
        self.assertEqual(status, 400)

        connection = HTTPConnection(self.host, self.port, timeout=2)
        connection.putrequest("PATCH", http_api.FAVICON_PATH)
        for name, value in dashboard_headers.items():
            connection.putheader(name, value)
        connection.putheader("Content-Length", str(favicon.MAX_FAVICON_REQUEST_BODY + 1))
        connection.endheaders()
        response = connection.getresponse()
        self.assertEqual(response.status, 413)
        response.read()
        connection.close()

        for path in (
            http_api.FAVICON_ASSET_PATH,
            f"{http_api.FAVICON_ASSET_PATH}?v=",
            f"{http_api.FAVICON_ASSET_PATH}?v={'A' * 64}",
            f"{http_api.FAVICON_ASSET_PATH}?v={'0' * 64}&extra=1",
            f"{http_api.FAVICON_ASSET_PATH}?v={'0' * 64}&v={'0' * 64}",
        ):
            with self.subTest(path=path):
                status, _ = self.request("GET", path)
                self.assertEqual(status, 404)

        for method, path in (
            ("POST", http_api.FAVICON_PATH),
            ("PUT", http_api.FAVICON_PATH),
            ("DELETE", http_api.FAVICON_PATH),
            ("HEAD", http_api.FAVICON_PATH),
            ("POST", http_api.FAVICON_ASSET_PATH),
            ("PATCH", http_api.FAVICON_ASSET_PATH),
            ("PUT", http_api.FAVICON_ASSET_PATH),
        ):
            with self.subTest(method=method, path=path):
                status, _, headers = self.raw_http_request(method, path)
                self.assertEqual(status, 405)
                self.assertIn("allow", headers)

    def test_favicon_corrupt_state_maps_to_unavailable_without_affecting_health(self) -> None:
        with mock.patch.object(
            self.server.favicon_store,
            "get",
            side_effect=favicon.FaviconStateUnavailable("damaged"),
        ):
            status, payload = self.request("GET", http_api.FAVICON_PATH)
        self.assertEqual(status, 503)
        self.assertEqual(payload, {"error": "favicon_state_unavailable"})

        with mock.patch.object(
            self.server.favicon_store,
            "update",
            side_effect=favicon.FaviconStateUnavailable("damaged"),
        ):
            status, payload = self.favicon_request({"version": 1, "iconSvg": None})
        self.assertEqual(status, 503)
        self.assertEqual(payload, {"error": "favicon_state_unavailable"})

        status, health = self.request("GET", http_api.HEALTH_PATH)
        self.assertEqual(status, 200)
        self.assertEqual(health["status"], "ok")

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
        connection.putrequest("POST", f"{http_api.BACKGROUND_UPLOAD_PATH}?name=too-large.png")
        connection.putheader("Origin", TEST_ORIGIN)
        connection.putheader("X-Requested-With", "homelab-dashboard")
        connection.putheader("Content-Type", "application/octet-stream")
        connection.putheader("Content-Length", str(backgrounds.MAX_BACKGROUND_UPLOAD_BODY + 1))
        connection.endheaders()
        response = connection.getresponse()
        self.assertEqual(response.status, 413)
        response.read()
        connection.close()

        status, _ = self.request("GET", http_api.BACKGROUND_UPLOAD_PATH)
        self.assertEqual(status, 405)

        with mock.patch.object(
            self.store,
            "upload_background",
            side_effect=backgrounds.BackgroundQuotaExceeded("full"),
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
        self.assertEqual(conflict["expiresIn"], backgrounds.REMOVAL_TOKEN_TTL_SECONDS)
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

        status, _ = self.request("GET", http_api.BACKGROUND_REMOVE_PATH)
        self.assertEqual(status, 405)

    def test_bestdori_characters_draw_and_history_contracts(self) -> None:
        status, characters = self.request("GET", http_api.BESTDORI_CHARACTERS_PATH)
        self.assertEqual(status, 200)
        self.assertEqual(characters["characters"][0], {"id": 1, "name": "户山香澄"})

        status, drawn = self.bestdori_post(
            http_api.BESTDORI_DRAW_PATH,
            {"mode": "character", "rarities": [3, 5], "characterId": 1},
        )
        self.assertEqual(status, 201)
        self.assertEqual(drawn["background"]["background"], "户山香澄_五星_测试卡面_101_after_training.png")
        self.assertEqual(drawn["card"]["id"], 101)
        self.assertEqual(self.server.bestdori_manager.draw_request, ("character", {3, 5}, 1))

        status, reconciled = self.bestdori_post(http_api.BESTDORI_RECONCILE_PATH, {})
        self.assertEqual(status, 200)
        self.assertEqual(reconciled, {"removed": 1, "remaining": 2})
        status, reset = self.bestdori_post(http_api.BESTDORI_RESET_PATH, {})
        self.assertEqual(status, 200)
        self.assertEqual(reset, {"removed": 3, "remaining": 0})

    def test_bing_daily_contract_partial_results_and_methods(self) -> None:
        status, payload = self.bing_post({})
        self.assertEqual(status, 200)
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["results"][0]["status"], "created")
        self.assertEqual(payload["results"][1]["status"], "existing")
        self.assertEqual(self.server.bing_manager.calls, 1)

        status, _ = self.request("GET", http_api.BING_DAILY_PATH)
        self.assertEqual(status, 405)
        status, _ = self.request("PATCH", http_api.BING_DAILY_PATH)
        self.assertEqual(status, 405)

    def test_bing_daily_validation_and_aggregate_statuses(self) -> None:
        cases = (
            ({}, {"Origin": "https://evil.test"}, 403),
            ({}, {"X-Requested-With": "wrong"}, 403),
            ({}, {"Content-Type": "text/plain"}, 415),
            ({"unexpected": True}, None, 400),
        )
        for value, headers, expected in cases:
            with self.subTest(value=value, headers=headers):
                status, _ = self.bing_post(value, headers)
                self.assertEqual(status, expected)

        failed_upstream = {
            "version": 1,
            "results": [
                {"slot": "cn", "status": "failed", "error": "bing_unavailable"},
                {"slot": "international", "status": "failed", "error": "bing_unavailable"},
            ],
        }
        failed_quota = {
            "version": 1,
            "results": [
                {"slot": "cn", "status": "failed", "error": "background_file_quota_exceeded"},
                {"slot": "international", "status": "failed", "error": "background_file_quota_exceeded"},
            ],
        }
        with mock.patch.object(self.server.bing_manager, "fetch_daily", return_value=failed_upstream):
            status, payload = self.bing_post({})
        self.assertEqual(status, 502)
        self.assertEqual(payload, failed_upstream)
        with mock.patch.object(self.server.bing_manager, "fetch_daily", return_value=failed_quota):
            status, payload = self.bing_post({})
        self.assertEqual(status, 409)
        self.assertEqual(payload, failed_quota)
        mixed_failures = {
            "version": 1,
            "results": [
                {"slot": "cn", "status": "failed", "error": "bing_unavailable"},
                {
                    "slot": "international",
                    "status": "failed",
                    "error": "background_file_quota_exceeded",
                },
            ],
        }
        with mock.patch.object(self.server.bing_manager, "fetch_daily", return_value=mixed_failures):
            status, payload = self.bing_post({})
        self.assertEqual(status, 409)
        self.assertEqual(payload, mixed_failures)
        with mock.patch.object(
            self.server.bing_manager,
            "fetch_daily",
            side_effect=bing.BingDailyBusy("busy"),
        ):
            status, payload = self.bing_post({})
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"], "bing_operation_in_progress")

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
                status, _ = self.bestdori_post(http_api.BESTDORI_DRAW_PATH, value, headers)
                self.assertEqual(status, expected)

        for path in (http_api.BESTDORI_RECONCILE_PATH, http_api.BESTDORI_RESET_PATH):
            with self.subTest(path=path):
                status, _ = self.bestdori_post(path, {"unexpected": True})
                self.assertEqual(status, 400)
                status, _ = self.request("GET", path)
                self.assertEqual(status, 405)
        status, _ = self.request("POST", http_api.BESTDORI_DRAW_PATH, b"{}", {})
        self.assertEqual(status, 403)
        status, _ = self.request("POST", http_api.BESTDORI_CHARACTERS_PATH)
        self.assertEqual(status, 405)

    def test_all_dashboard_json_endpoints_reject_null_without_hanging(self) -> None:
        dashboard_headers = {
            "Origin": TEST_ORIGIN,
            "X-Requested-With": "homelab-dashboard",
            "Content-Type": "application/json",
        }
        endpoints = (
            ("PATCH", http_api.API_PATH),
            ("PATCH", http_api.CARDS_PATH),
            ("PATCH", http_api.FAVICON_PATH),
            ("POST", http_api.BACKGROUND_REMOVE_PATH),
            ("POST", http_api.SERVICE_STATUS_PATH),
            ("POST", http_api.CARDS_INITIALIZE_PATH),
            ("POST", http_api.BESTDORI_DRAW_PATH),
            ("POST", http_api.BESTDORI_RECONCILE_PATH),
            ("POST", http_api.BESTDORI_RESET_PATH),
            ("POST", http_api.BING_DAILY_PATH),
        )
        for method, path in endpoints:
            with self.subTest(method=method, path=path):
                status, payload = self.request(method, path, b"null", dashboard_headers)
                self.assertEqual(status, 400)
                self.assertIn("null", payload["error"])

    def test_bestdori_failures_map_to_stable_http_errors(self) -> None:
        for error, expected_status, expected_message in (
            (bestdori.BestdoriDrawBusy("busy"), 409, "bestdori_operation_in_progress"),
            (bestdori.BestdoriPoolExhausted("empty"), 409, "bestdori_card_pool_exhausted"),
            (bestdori.BestdoriHistoryError("broken"), 503, "bestdori_history_unavailable"),
            (bestdori.BestdoriUnavailable("offline"), 502, "bestdori_unavailable"),
            (backgrounds.InvalidImage("bad"), 502, "bestdori_invalid_image"),
            (backgrounds.BackgroundQuotaExceeded("full"), 409, "background_file_quota_exceeded"),
        ):
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(self.server.bestdori_manager, "draw", side_effect=error):
                    status, payload = self.bestdori_post(
                        http_api.BESTDORI_DRAW_PATH,
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

        status, _ = self.request("GET", http_api.SERVICE_STATUS_PATH)
        self.assertEqual(status, 405)

        dashboard_headers = {
            "Origin": TEST_ORIGIN,
            "X-Requested-With": "homelab-dashboard",
            "Content-Type": "application/json",
        }
        status, _ = self.request(
            "POST",
            http_api.SERVICE_STATUS_PATH,
            b'{"targets":[],"targets":[]}',
            dashboard_headers,
        )
        self.assertEqual(status, 400)
        status, _ = self.request(
            "POST",
            http_api.SERVICE_STATUS_PATH,
            b"x" * (http_api.MAX_SERVICE_STATUS_BODY + 1),
            dashboard_headers,
        )
        self.assertEqual(status, 413)

    def test_control_requires_token_and_shuts_down(self) -> None:
        status, _ = self.request("POST", http_api.CONTROL_PATH, headers={"X-Control-Token": "wrong"})
        self.assertEqual(status, 403)
        status, payload = self.request(
            "POST", http_api.CONTROL_PATH, headers={"X-Control-Token": self.config.control_token}
        )
        self.assertEqual(status, 202)
        self.assertEqual(payload["status"], "stopping")
