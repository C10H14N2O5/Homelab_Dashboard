from __future__ import annotations

import json
import logging
import socket
import threading
import unittest
import uuid
from dataclasses import replace
from http.client import HTTPConnection
from urllib.parse import urlencode
from unittest import mock

from homelab_state import backgrounds, http_api, notes_http
from tests.helpers import FIXED_TIME, TEST_ORIGIN, TemporaryProject, image_bytes


def identity() -> str:
    return str(uuid.uuid4())


class NotesHTTPTests(unittest.TestCase):
    """Exercise actual request framing, route dispatch, durable state and images."""

    def setUp(self) -> None:
        self.project = TemporaryProject()
        self.config = replace(self.project.write_config(
            version=4,
            listen="127.0.0.1:1",
            notes={"enabled": True, "primaryDir": "notes-primary", "backupDir": "notes-backup"},
        ), listen_port=0)
        logger = logging.getLogger(f"notes-http-{id(self)}")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        self.server = http_api.StateHTTPServer(
            self.config,
            backgrounds.StateStore(self.config.background_dir, self.config.state_file, lambda: FIXED_TIME),
            logger,
            probe_client=mock.Mock(),
            bestdori_manager=mock.Mock(),
            bing_manager=mock.Mock(),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.server.server_address
        self.assertIsNotNone(self.server.notes_service, "Notes service failed to start")

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.project.close()

    def exchange(self, method, path, body=None, headers=None):
        connection = HTTPConnection(self.host, self.port, timeout=15)
        try:
            connection.request(method, path, body, headers or {})
            response = connection.getresponse()
            return response.status, response.read(), {key.lower(): value for key, value in response.getheaders()}
        finally:
            connection.close()

    def request(self, method, path, payload=None, headers=None):
        request_headers = {"Origin": TEST_ORIGIN, "X-Requested-With": "homelab-dashboard", "Content-Type": "application/json"}
        request_headers.update(headers or {})
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        status, data, response_headers = self.exchange(method, path, body, request_headers)
        return status, json.loads(data) if data else {}, response_headers

    def raw(self, headers: str) -> bytes:
        with socket.create_connection((self.host, self.port), timeout=3) as connection:
            connection.settimeout(3)
            connection.sendall(headers.encode("ascii"))
            connection.shutdown(socket.SHUT_WR)
            response = bytearray()
            while True:
                data = connection.recv(65536)
                if not data:
                    return bytes(response)
                response.extend(data)

    def create(self, body="first body", title="title"):
        note_id = identity()
        payload = {"title": title, "body": body, "expectedRevision": None, "operationId": identity()}
        status, result, _ = self.request("PUT", "/api/notes/" + note_id, payload)
        self.assertEqual(status, 200, result)
        return note_id, result["note"], payload

    def upload(self, upload_id=None, data=None, name="example.png"):
        upload_id = upload_id or identity()
        image = data if data is not None else image_bytes()
        query = urlencode({"id": upload_id, "name": name})
        status, raw, headers = self.exchange("POST", "/api/notes/uploads?" + query, image, {
            "Origin": TEST_ORIGIN, "X-Requested-With": "homelab-dashboard", "Content-Type": "application/octet-stream",
        })
        return status, json.loads(raw), headers, image

    def test_create_read_update_and_list_excludes_body(self):
        note_id, created, _ = self.create(body="## 正文\n\n- [ ] task", title="")
        status, result, headers = self.request("GET", "/api/notes/" + note_id)
        self.assertEqual(status, 200)
        self.assertEqual(result["note"], created)
        self.assertEqual(headers["cache-control"], "no-store, max-age=0")
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        changed = {"title": "new title", "body": "new body", "expectedRevision": created["revision"], "operationId": identity()}
        status, result, _ = self.request("PUT", "/api/notes/" + note_id, changed)
        self.assertEqual(status, 200)
        self.assertNotEqual(result["note"]["revision"], created["revision"])
        self.assertEqual(result["note"]["createdAt"], created["createdAt"])
        status, listing, _ = self.request("GET", "/api/notes")
        self.assertEqual(status, 200)
        self.assertEqual([entry["id"] for entry in listing["notes"]], [note_id])
        self.assertNotIn("body", listing["notes"][0])
        self.assertNotIn("lastOperation", result["note"])

    def test_lost_response_retries_create_and_update_idempotently(self):
        note_id, note, create_request = self.create()
        status, duplicate, _ = self.request("PUT", "/api/notes/" + note_id, create_request)
        self.assertEqual(status, 200)
        self.assertEqual(duplicate["note"], note)
        update = {**create_request, "body": "changed", "expectedRevision": note["revision"], "operationId": identity()}
        status, changed, _ = self.request("PUT", "/api/notes/" + note_id, update)
        self.assertEqual(status, 200)
        status, duplicate, _ = self.request("PUT", "/api/notes/" + note_id, update)
        self.assertEqual(status, 200)
        self.assertEqual(duplicate["note"], changed["note"])
        status, conflict, _ = self.request("PUT", "/api/notes/" + note_id, {**update, "body": "different"})
        self.assertEqual(status, 409)
        self.assertEqual(conflict["error"], "operation_conflict")

    def test_stale_revision_returns_server_copy_without_overwrite(self):
        note_id, original, create_request = self.create()
        current_request = {**create_request, "expectedRevision": original["revision"], "operationId": identity(), "body": "server edit"}
        status, current, _ = self.request("PUT", "/api/notes/" + note_id, current_request)
        self.assertEqual(status, 200)
        stale_request = {**current_request, "operationId": identity(), "body": "stale edit"}
        status, conflict, _ = self.request("PUT", "/api/notes/" + note_id, stale_request)
        self.assertEqual(status, 409)
        self.assertEqual(conflict["error"], "revision_conflict")
        self.assertEqual(conflict["current"], current["note"])
        self.assertEqual(self.request("GET", "/api/notes/" + note_id)[1]["note"]["body"], "server edit")

    def test_delete_is_revision_checked_idempotent_and_leaves_no_body(self):
        note_id, note, _ = self.create(body="private body")
        payload = {"expectedRevision": note["revision"], "operationId": identity()}
        status, deleted, _ = self.request("DELETE", "/api/notes/" + note_id, payload)
        self.assertEqual(status, 200)
        self.assertTrue(deleted["note"]["deleted"])
        self.assertNotIn("body", deleted["note"])
        self.assertNotIn("title", deleted["note"])
        status, duplicate, _ = self.request("DELETE", "/api/notes/" + note_id, payload)
        self.assertEqual(status, 200)
        self.assertEqual(duplicate["note"], deleted["note"])
        status, missing, _ = self.request("GET", "/api/notes/" + note_id)
        self.assertEqual(status, 410)
        self.assertEqual(missing["error"], "note_deleted")
        self.assertEqual(self.request("GET", "/api/notes")[1]["notes"], [])
        persisted = json.loads((self.config.notes_primary_dir / "notes" / (note_id + ".json")).read_text(encoding="utf-8"))
        self.assertNotIn("body", persisted)

    def test_delete_rejects_stale_revision(self):
        note_id, note, _ = self.create()
        status, result, _ = self.request("DELETE", "/api/notes/" + note_id, {"expectedRevision": identity(), "operationId": identity()})
        self.assertEqual(status, 409)
        self.assertEqual(result["current"], note)
        self.assertEqual(self.request("GET", "/api/notes/" + note_id)[0], 200)

    def test_settings_validation_revision_and_shared_read(self):
        status, result, _ = self.request("GET", "/api/notes/settings")
        self.assertEqual(status, 200)
        settings = result["settings"]
        self.assertTrue(settings["autoSaveEnabled"])
        self.assertEqual(settings["autoSaveSeconds"], 60)
        payload = {"expectedRevision": settings["revision"], "operationId": identity(), "autoSaveSeconds": 10, "saveOnSwitch": False}
        status, updated, _ = self.request("PATCH", "/api/notes/settings", payload)
        self.assertEqual(status, 200)
        self.assertEqual(self.request("GET", "/api/notes/settings")[1], updated)
        status, conflict, _ = self.request("PATCH", "/api/notes/settings", {**payload, "operationId": identity(), "autoSaveSeconds": 3600})
        self.assertEqual(status, 409)
        self.assertEqual(conflict["error"], "revision_conflict")
        for value in [9, 3601, 60.5, True, "60"]:
            with self.subTest(value=value):
                status, error, _ = self.request("PATCH", "/api/notes/settings", {"expectedRevision": updated["settings"]["revision"], "operationId": identity(), "autoSaveSeconds": value})
                self.assertEqual(status, 400)
                self.assertEqual(error["error"], "invalid_settings")

    def test_order_requires_current_list_and_revision(self):
        one, _, _ = self.create(title="one")
        two, _, _ = self.create(title="two")
        listing = self.request("GET", "/api/notes")[1]
        payload = {"expectedRevision": listing["orderRevision"], "operationId": identity(), "ids": [one, two]}
        status, result, _ = self.request("PATCH", "/api/notes/order", payload)
        self.assertEqual(status, 200)
        self.assertEqual([note["id"] for note in self.request("GET", "/api/notes")[1]["notes"]], [one, two])
        status, stale, _ = self.request("PATCH", "/api/notes/order", {**payload, "operationId": identity(), "ids": [two, one]})
        self.assertEqual(status, 409)
        self.assertEqual(stale["error"], "revision_conflict")
        status, missing, _ = self.request("PATCH", "/api/notes/order", {"expectedRevision": result["order"]["revision"], "operationId": identity(), "ids": [one]})
        self.assertEqual(status, 409)
        self.assertEqual(missing["error"], "order_conflict")

    def test_upload_roundtrip_has_verified_mime_private_headers_and_deduplication(self):
        upload_id = identity()
        status, uploaded, _, original = self.upload(upload_id, name="misleading.jpg")
        self.assertEqual(status, 201, uploaded)
        self.assertEqual(uploaded["mime"], "image/png")
        self.assertEqual(uploaded["status"], "uploaded")
        self.assertEqual(self.request("GET", "/api/notes/uploads/" + upload_id)[1]["status"], "uploaded")
        status, body, headers = self.exchange("GET", uploaded["url"])
        self.assertEqual(status, 200)
        self.assertEqual(body, original)
        self.assertEqual(headers["content-type"], "image/png")
        self.assertEqual(headers["content-length"], str(len(original)))
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertEqual(headers["cache-control"], "no-store, max-age=0")
        self.assertEqual(headers["cross-origin-resource-policy"], "same-origin")
        status, repeated, _, _ = self.upload(upload_id, original)
        self.assertEqual(status, 201)
        self.assertEqual(repeated["url"], uploaded["url"])
        status, conflict, _, _ = self.upload(upload_id, image_bytes("JPEG"))
        self.assertEqual(status, 409)
        self.assertEqual(conflict["error"], "upload_conflict")

    def test_invalid_image_fails_as_client_error_without_creating_upload(self):
        upload_id = identity()
        status, error, _, _ = self.upload(upload_id, b'<svg onload="alert(1)"></svg>', name="image.png")
        self.assertEqual(status, 400, error)
        self.assertEqual(error["error"], "invalid_image")
        self.assertEqual(self.request("GET", "/api/notes/uploads/" + upload_id)[1]["status"], "missing")
        self.assertEqual(self.exchange("GET", "/api/notes/images/" + upload_id)[0], 404)

    def test_malformed_order_members_are_client_errors(self):
        order_revision = self.request("GET", "/api/notes")[1]["orderRevision"]
        for ids in [[{}], [[]], [True], [identity(), identity(), "bad-id"], "not-a-list"]:
            with self.subTest(ids=ids):
                status, error, _ = self.request("PATCH", "/api/notes/order", {"expectedRevision": order_revision, "operationId": identity(), "ids": ids})
                self.assertEqual(status, 400, error)

    def test_saved_image_reference_prevents_discard(self):
        upload_id = identity()
        status, uploaded, _, _ = self.upload(upload_id)
        self.assertEqual(status, 201, uploaded)
        _, note, _ = self.create(body="![example](" + uploaded["url"] + ")")
        self.assertEqual(note["attachments"], [upload_id])
        status, error, _ = self.request("POST", "/api/notes/uploads/" + upload_id + "/discard", {})
        self.assertEqual(status, 409)
        self.assertEqual(error["error"], "image_referenced")
        self.assertEqual(self.exchange("GET", uploaded["url"])[0], 200)

    def test_invalid_upload_query_is_rejected_before_storage(self):
        cases = ["", "id=" + identity(), "id=" + identity() + "&name=", "id=invalid&name=x.png", "id=" + identity() + "&name=x.png&extra=x", "id=" + identity() + "&name=x.png&name=y.png"]
        with mock.patch.object(self.server.notes_service, "upload") as upload:
            for query in cases:
                with self.subTest(query=query):
                    status, _, _ = self.exchange("POST", "/api/notes/uploads?" + query, b"x", {"Origin": TEST_ORIGIN, "X-Requested-With": "homelab-dashboard", "Content-Type": "application/octet-stream"})
                    self.assertEqual(status, 400)
            upload.assert_not_called()

    def test_upload_20_mib_boundary_at_http_layer(self):
        upload_id = identity()
        endpoint = "/api/notes/uploads?id=" + upload_id + "&name=limit.png"
        with mock.patch.object(self.server.notes_service, "upload", return_value={"id": upload_id}) as upload:
            body = b"x" * notes_http.MAX_NOTES_IMAGE_BODY
            status, _, _ = self.exchange("POST", endpoint, body, {"Origin": TEST_ORIGIN, "X-Requested-With": "homelab-dashboard", "Content-Type": "application/octet-stream"})
            self.assertEqual(status, 201)
            self.assertEqual(len(upload.call_args.args[2]), 20 * 1024 * 1024)
        response = self.raw(f"POST {endpoint} HTTP/1.1\r\nHost: localhost\r\nOrigin: {TEST_ORIGIN}\r\nX-Requested-With: homelab-dashboard\r\nContent-Type: application/octet-stream\r\nContent-Length: {notes_http.MAX_NOTES_IMAGE_BODY + 1}\r\nExpect: 100-continue\r\nConnection: close\r\n\r\n")
        self.assertIn(b" 413 ", response.split(b"\r\n", 1)[0])
        self.assertNotIn(b"100 Continue", response)

    def test_mutations_require_trusted_origin_dashboard_header_and_content_type(self):
        note_id = identity()
        payload = {"title": "", "body": "", "expectedRevision": None, "operationId": identity()}
        for headers, expected in [({"Origin": "https://evil.example"}, 403), ({"Origin": ""}, 403), ({"X-Requested-With": ""}, 403), ({"Content-Type": "text/plain"}, 415)]:
            with self.subTest(headers=headers):
                self.assertEqual(self.request("PUT", "/api/notes/" + note_id, payload, headers)[0], expected)
        self.assertEqual(self.request("GET", "/api/notes")[1]["notes"], [])

    def test_methods_and_non_upload_query_parameters_are_rejected(self):
        for method, endpoint, allow in [("POST", "/api/notes", "GET"), ("PUT", "/api/notes/settings", "GET, PATCH"), ("GET", "/api/notes/order", "PATCH"), ("DELETE", "/api/notes/images/" + identity(), "GET")]:
            with self.subTest(method=method, endpoint=endpoint):
                status, _, headers = self.exchange(method, endpoint)
                self.assertEqual(status, 405)
                self.assertEqual(headers["allow"], allow)
        self.assertEqual(self.request("GET", "/api/notes?cache=123")[0], 400)
        self.assertEqual(self.request("GET", "/api/notes/images/" + identity() + "?download=1")[0], 400)
        self.assertEqual(self.request("GET", "/api/notes/invalid-id")[0], 404)

    def test_duplicate_json_fields_invalid_shape_and_unknown_fields_rejected(self):
        endpoint = "/api/notes/" + identity()
        headers = {"Origin": TEST_ORIGIN, "X-Requested-With": "homelab-dashboard", "Content-Type": "application/json"}
        status, _, _ = self.exchange("PUT", endpoint, b'{"title":"a","title":"b"}', headers)
        self.assertEqual(status, 400)
        for payload in [[], {"title": "a", "body": "b", "expectedRevision": None, "operationId": identity(), "admin": True}]:
            self.assertEqual(self.request("PUT", endpoint, payload)[0], 400)

    def test_request_framing_rejects_duplicate_length_chunked_and_missing_length(self):
        endpoint = "/api/notes/" + identity()
        for framing in ["Content-Length: 2\r\nContent-Length: 2\r\n", "Transfer-Encoding: chunked\r\n", ""]:
            with self.subTest(framing=framing):
                response = self.raw(f"PUT {endpoint} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n{framing}\r\n")
                self.assertIn(b" 400 ", response.split(b"\r\n", 1)[0])
        response = self.raw("GET /api/notes HTTP/1.1\r\nHost: localhost\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}")
        self.assertIn(b" 400 ", response.split(b"\r\n", 1)[0])

    def test_json_body_limit_is_checked_before_continue(self):
        endpoint = "/api/notes/" + identity()
        response = self.raw(f"PUT {endpoint} HTTP/1.1\r\nHost: localhost\r\nOrigin: {TEST_ORIGIN}\r\nX-Requested-With: homelab-dashboard\r\nContent-Type: application/json\r\nContent-Length: {notes_http.MAX_NOTES_JSON_BODY + 1}\r\nExpect: 100-continue\r\nConnection: close\r\n\r\n")
        self.assertIn(b" 413 ", response.split(b"\r\n", 1)[0])
        self.assertNotIn(b"100 Continue", response)

    def test_disabled_v3_reports_unavailable_without_affecting_dashboard(self):
        self.server.notes_service.close()
        self.server.notes_service = None
        self.server.config = replace(self.project.write_config(listen="127.0.0.1:1"), listen_port=self.port)
        status, result, _ = self.request("GET", "/api/notes/status")
        self.assertEqual(status, 200)
        self.assertFalse(result["enabled"])
        self.assertEqual(result["error"], "notes_disabled")
        self.assertEqual(self.request("GET", "/api/notes")[0], 503)
        status, result, _ = self.request("PUT", "/api/notes/" + identity(), {"title": "", "body": "", "expectedRevision": None, "operationId": identity()})
        self.assertEqual(status, 503)
        self.assertEqual(result["error"], "notes_disabled")
        self.assertEqual(self.request("GET", http_api.API_PATH)[0], 200)


if __name__ == "__main__":
    unittest.main()
