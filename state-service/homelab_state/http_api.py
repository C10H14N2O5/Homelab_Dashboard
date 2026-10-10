from __future__ import annotations

import json
import logging
import os
import re
import secrets
import socket
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from homelab_state.backgrounds import (
    MAX_BACKGROUND_UPLOAD_BODY,
    REMOVAL_TOKEN_TTL_SECONDS,
    BackgroundMissing,
    BackgroundQuotaExceeded,
    InvalidBackground,
    InvalidConfirmationToken,
    InvalidImage,
    ProtectedBackground,
    StateStore,
    TrashUnavailable,
    UploadTooLarge,
    validate_background_name,
)
from homelab_state.bestdori import (
    BESTDORI_CHARACTER_IDS,
    BESTDORI_RARITIES,
    BestdoriDrawBusy,
    BestdoriDrawManager,
    BestdoriHistoryError,
    BestdoriPoolExhausted,
    BestdoriUnavailable,
)
from homelab_state.bing import BingClient, BingDailyBusy, BingDailyManager
from homelab_state.cards import (
    MAX_CARDS_REQUEST_BODY,
    CardsAlreadyInitialized,
    CardsNotInitialized,
    CardsRevisionConflict,
    CardsStateUnavailable,
    CardsStore,
    InvalidCards,
)
from homelab_state.config import AppConfig, normalize_origin
from homelab_state.core import (
    SERVICE_NAME,
    SERVICE_VERSION,
    ConfigurationError,
)
from homelab_state.favicon import (
    MAX_FAVICON_REQUEST_BODY,
    PAGE_ICON_STATE_FILE_NAME,
    FaviconNotFound,
    FaviconStateUnavailable,
    FaviconStore,
    InvalidFavicon,
)
from homelab_state.probe import MAX_PROBE_TARGETS, ProbeClient
from homelab_state.notes import NotesService
from homelab_state.notes_http import handle_notes_request, notes_accepts_body


API_PATH = "/api/background-preferences"
SERVICE_STATUS_PATH = "/api/service-status"
BACKGROUND_FILES_PATH = "/api/background-files"
BACKGROUND_UPLOAD_PATH = "/api/background-files/upload"
BACKGROUND_REMOVE_PATH = "/api/background-files/remove"
BESTDORI_CHARACTERS_PATH = "/api/bestdori/characters"
BESTDORI_DRAW_PATH = "/api/background-files/draw"
BESTDORI_RECONCILE_PATH = "/api/bestdori/history/reconcile"
BESTDORI_RESET_PATH = "/api/bestdori/history/reset"
BING_DAILY_PATH = "/api/background-files/bing-daily"
CARDS_PATH = "/api/cards"
CARDS_INITIALIZE_PATH = "/api/cards/initialize"
FAVICON_PATH = "/api/favicon"
FAVICON_ASSET_PATH = "/api/favicon.svg"
PAGE_ICON_PATH = "/api/page-icon"
PAGE_ICON_ASSET_PATH = "/api/page-icon.svg"
HEALTH_PATH = "/healthz"
CONTROL_PATH = "/_control/shutdown"
MAX_PATCH_BODY = 4 * 1024
MAX_SERVICE_STATUS_BODY = 16 * 1024
MAX_BACKGROUND_REMOVE_BODY = 4 * 1024
MAX_BESTDORI_REQUEST_BODY = 4 * 1024
MAX_BING_DAILY_REQUEST_BODY = 4 * 1024
HTTP_IDLE_TIMEOUT_SECONDS = 30.0
HTTP_REQUEST_READ_DEADLINE_SECONDS = 300.0
HTTP_ERROR_WRITE_TIMEOUT_SECONDS = 1.0
HTTP_BODY_READ_CHUNK_SIZE = 64 * 1024
HTTP_REJECTED_BODY_DRAIN_TIMEOUT_SECONDS = 0.01
HTTP_REJECTED_BODY_DRAIN_LIMIT = 64 * 1024
HTTP_BUSY_DRAIN_TIMEOUT_SECONDS = 0.05
HTTP_BUSY_DRAIN_LIMIT = 8 * 1024
MAX_HTTP_CONNECTIONS = 16


class RequestBodyReadError(ConnectionError):
    pass


class StateHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        config: AppConfig,
        store: StateStore,
        logger: logging.Logger,
        probe_client: ProbeClient | None = None,
        bestdori_manager: BestdoriDrawManager | None = None,
        cards_store: CardsStore | None = None,
        bing_manager: BingDailyManager | None = None,
        favicon_store: FaviconStore | None = None,
        page_icon_store: FaviconStore | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.logger = logger
        self.notes_service = None
        self._notes_init_lock = threading.Lock()
        self._notes_retry_at = 0.0
        self._notes_init_error = None
        self._notes_closed = False
        self._connection_slots = threading.BoundedSemaphore(MAX_HTTP_CONNECTIONS)
        self.bestdori_manager = bestdori_manager or BestdoriDrawManager(
            store,
            config.state_dir / "BestdoriCardDraws.json",
        )
        self.bing_manager = bing_manager or BingDailyManager(
            store,
            client=BingClient(config.bing_international_proxy_url),
        )
        self.cards_store = cards_store or CardsStore.for_state_directory(
            config.state_dir,
            config.probe_domain_suffix,
        )
        self.favicon_store = favicon_store or FaviconStore.for_state_directory(
            config.state_dir,
        )
        # Reuse the same validated, atomic SVG store with an independent state file.
        self.page_icon_store = page_icon_store or FaviconStore(
            config.state_dir / PAGE_ICON_STATE_FILE_NAME,
        )
        owns_probe_client = probe_client is None
        self.probe_client = probe_client or ProbeClient(config)
        try:
            super().__init__((config.listen_host, config.listen_port), StateRequestHandler)
        except Exception:
            if owns_probe_client:
                self.probe_client.close()
            raise
        self.get_notes_service()

    def get_notes_service(self):
        if self.notes_service is not None or not self.config.notes_enabled or self._notes_closed:
            return self.notes_service
        if time.monotonic() < self._notes_retry_at or not self._notes_init_lock.acquire(blocking=False):
            return None
        try:
            if self.notes_service is not None or self._notes_closed:
                return self.notes_service
            self._notes_retry_at = time.monotonic() + 15.0
            try:
                self.notes_service = NotesService(self.config.notes_primary_dir, self.config.notes_backup_dir, logger=self.logger, forbidden_roots=self.config.public_directories, binding_file=self.config.state_dir / "NotesStorageBindings.json", config_file=self.config.config_path, state_dir=self.config.state_dir)
                self._notes_init_error = None
            except Exception as exc:
                self._notes_init_error = {"code": getattr(exc, "code", "primary_unavailable"), "message": str(exc)}
                self.logger.exception("notes could not initialize; other dashboard features remain available")
            return self.notes_service
        finally:
            self._notes_init_lock.release()

    def server_close(self) -> None:
        with self._notes_init_lock:
            self._notes_closed = True
            if self.notes_service is not None:
                self.notes_service.close()
        super().server_close()

    def get_request(self):
        request, client_address = super().get_request()
        request.settimeout(HTTP_IDLE_TIMEOUT_SECONDS)
        return request, client_address

    @staticmethod
    def _reject_busy_connection(request: socket.socket) -> None:
        body = b'{"error":"server_busy"}\n'
        response = (
            b"HTTP/1.1 503 Service Unavailable\r\n"
            b"Cache-Control: no-store, max-age=0\r\n"
            b"Pragma: no-cache\r\n"
            b"X-Content-Type-Options: nosniff\r\n"
            b"Content-Type: application/json; charset=utf-8\r\n"
            + f"Content-Length: {len(body)}\r\n".encode("ascii")
            + b"Retry-After: 1\r\n"
            b"Connection: close\r\n"
            b"\r\n"
            + body
        )
        try:
            request.settimeout(HTTP_ERROR_WRITE_TIMEOUT_SECONDS)
            request.sendall(response)
            request.shutdown(socket.SHUT_WR)
            request.settimeout(HTTP_BUSY_DRAIN_TIMEOUT_SECONDS)
            drained = 0
            while drained < HTTP_BUSY_DRAIN_LIMIT:
                chunk = request.recv(min(1024, HTTP_BUSY_DRAIN_LIMIT - drained))
                if not chunk:
                    break
                drained += len(chunk)
        except OSError:
            pass

    def process_request(self, request, client_address) -> None:
        if not self._connection_slots.acquire(blocking=False):
            self._reject_busy_connection(request)
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._connection_slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._connection_slots.release()


class StateRequestHandler(BaseHTTPRequestHandler):
    server: StateHTTPServer
    protocol_version = "HTTP/1.1"

    def _shutdown_request_socket(self) -> None:
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def handle_one_request(self) -> None:
        self._request_read_deadline = time.monotonic() + HTTP_REQUEST_READ_DEADLINE_SECONDS
        self._parsed_content_length: int | None = None
        self._request_keep_alive = False
        self._expect_continue_pending = False
        self._rejected_request = False
        self._rejected_body_finalized = False
        self.connection.settimeout(
            min(HTTP_IDLE_TIMEOUT_SECONDS, HTTP_REQUEST_READ_DEADLINE_SECONDS)
        )
        header_timer = threading.Timer(
            HTTP_REQUEST_READ_DEADLINE_SECONDS,
            self._shutdown_request_socket,
        )
        header_timer.daemon = True
        header_timer.start()
        try:
            self.raw_requestline = self.rfile.readline(65537)
            if len(self.raw_requestline) > 65536:
                self.requestline = ""
                self.request_version = ""
                self.command = ""
                self.send_error(HTTPStatus.REQUEST_URI_TOO_LONG)
                return
            if not self.raw_requestline:
                self.close_connection = True
                return
            if not self.parse_request():
                return
            header_timer.cancel()
            method_name = "do_" + self.command
            if not hasattr(self, method_name):
                self.send_error(
                    HTTPStatus.NOT_IMPLEMENTED,
                    "Unsupported method (%r)" % self.command,
                )
                return
            method = getattr(self, method_name)
            method()
            self.wfile.flush()
        except (TimeoutError, OSError) as error:
            self.log_error("request handling failed: %r", error)
            self.close_connection = True
        finally:
            header_timer.cancel()
            self._finalize_rejected_response()

    def handle_expect_100(self) -> bool:
        # BaseHTTPRequestHandler calls this while parsing headers. Delay the
        # interim response until framing, origin, type and size checks pass.
        self._expect_continue_pending = True
        return True

    def _route_accepts_body(self) -> bool:
        if notes_accepts_body(self.command, self.path):
            return True
        if self.command == "PATCH":
            return self.path in {API_PATH, CARDS_PATH, FAVICON_PATH, PAGE_ICON_PATH}
        if self.command != "POST":
            return False
        if urlsplit(self.path).path == BACKGROUND_UPLOAD_PATH:
            return True
        return self.path in {
            BACKGROUND_REMOVE_PATH,
            SERVICE_STATUS_PATH,
            BESTDORI_DRAW_PATH,
            BESTDORI_RECONCILE_PATH,
            BESTDORI_RESET_PATH,
            BING_DAILY_PATH,
            CARDS_INITIALIZE_PATH,
        }

    def _reject_request_framing(self, status: int, message: str) -> bool:
        self._close_rejected_request()
        self._error(status, message)
        return False

    def parse_request(self) -> bool:
        if not super().parse_request():
            return False
        self._request_keep_alive = not self.close_connection

        transfer_encodings = self.headers.get_all("Transfer-Encoding", failobj=[])
        if transfer_encodings:
            return self._reject_request_framing(
                HTTPStatus.BAD_REQUEST,
                "Transfer-Encoding is not supported",
            )

        content_lengths = self.headers.get_all("Content-Length", failobj=[])
        if len(content_lengths) > 1:
            return self._reject_request_framing(
                HTTPStatus.BAD_REQUEST,
                "ambiguous Content-Length",
            )
        if content_lengths:
            raw_length = content_lengths[0]
            if len(raw_length) > 20 or not re.fullmatch(r"[0-9]+", raw_length):
                return self._reject_request_framing(
                    HTTPStatus.BAD_REQUEST,
                    "invalid Content-Length",
                )
            content_length = int(raw_length)
        else:
            content_length = None
        self._parsed_content_length = content_length

        expect_values = self.headers.get_all("Expect", failobj=[])
        if expect_values and (
            len(expect_values) != 1
            or expect_values[0].strip().lower() != "100-continue"
            or self.request_version < "HTTP/1.1"
        ):
            return self._reject_request_framing(
                HTTPStatus.EXPECTATION_FAILED,
                "unsupported Expect header",
            )

        accepts_body = self._route_accepts_body()
        if accepts_body and content_length is None:
            return self._reject_request_framing(
                HTTPStatus.BAD_REQUEST,
                "Content-Length is required",
            )
        if not accepts_body and content_length not in {None, 0}:
            return self._reject_request_framing(
                HTTPStatus.BAD_REQUEST,
                "request body is not allowed for this route",
            )
        if self._expect_continue_pending and not accepts_body:
            return self._reject_request_framing(
                HTTPStatus.EXPECTATION_FAILED,
                "request body is not accepted for this route",
            )

        return True

    def log_message(self, format_string: str, *args: Any) -> None:
        self.server.logger.debug("%s - %s", self.address_string(), format_string % args)

    def _common_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")

    def _write_json(self, status: int, value: Any) -> None:
        body = (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        self.send_response(status)
        self._common_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _write_favicon_svg(self, icon_svg: str, content_hash: str) -> None:
        body = icon_svg.encode("utf-8")
        etag = f'"sha256-{content_hash}"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(HTTPStatus.NOT_MODIFIED)
            self.send_header("Cache-Control", "private, max-age=31536000, immutable")
            self.send_header("ETag", etag)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", "0")
            if self.close_connection:
                self.send_header("Connection", "close")
            self.end_headers()
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Cache-Control", "private, max-age=31536000, immutable")
        self.send_header("ETag", etag)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._write_json(status, {"error": message})

    def _allow(self, methods: str) -> None:
        self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
        self._common_headers()
        self.send_header("Allow", methods)
        self.send_header("Content-Length", "0")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()

    def _close_rejected_request(self) -> None:
        self.close_connection = True
        self._rejected_request = True

    def _finalize_rejected_response(self) -> None:
        if not self._rejected_request or self._rejected_body_finalized:
            return
        self._rejected_body_finalized = True
        try:
            self.wfile.flush()
        except OSError:
            return
        try:
            self.connection.settimeout(HTTP_REJECTED_BODY_DRAIN_TIMEOUT_SECONDS)
            drained = 0
            read = getattr(self.rfile, "read1", self.rfile.read)
            while drained < HTTP_REJECTED_BODY_DRAIN_LIMIT:
                chunk = read(
                    min(
                        HTTP_BODY_READ_CHUNK_SIZE,
                        HTTP_REJECTED_BODY_DRAIN_LIMIT - drained,
                    )
                )
                if not chunk:
                    break
                drained += len(chunk)
        except (TimeoutError, OSError):
            pass

    def _send_continue_if_pending(self) -> None:
        if not self._expect_continue_pending:
            return
        self.send_response_only(HTTPStatus.CONTINUE)
        self.end_headers()
        self._expect_continue_pending = False

    def _remaining_read_seconds(self) -> float:
        remaining = self._request_read_deadline - time.monotonic()
        if remaining <= 0:
            raise RequestBodyReadError("request body read deadline exceeded")
        return remaining

    def _read_body_chunk(self, maximum: int) -> bytes:
        try:
            self.connection.settimeout(
                min(HTTP_IDLE_TIMEOUT_SECONDS, self._remaining_read_seconds())
            )
            read = getattr(self.rfile, "read1", self.rfile.read)
            chunk = read(min(maximum, HTTP_BODY_READ_CHUNK_SIZE))
        except (TimeoutError, OSError):
            raise RequestBodyReadError("request body timed out") from None
        if not chunk:
            raise RequestBodyReadError("request body ended before Content-Length")
        if time.monotonic() > self._request_read_deadline:
            raise RequestBodyReadError("request body read deadline exceeded")
        return chunk

    def _restore_request_connection(self) -> None:
        self.close_connection = not self._request_keep_alive

    def _request_body_timeout(self) -> None:
        self._close_rejected_request()
        try:
            self.connection.settimeout(HTTP_ERROR_WRITE_TIMEOUT_SECONDS)
            self._error(HTTPStatus.REQUEST_TIMEOUT, "request body timed out")
        except OSError:
            pass

    def _validate_dashboard_headers(self, expected_content_type: str) -> bool:
        origin_values = self.headers.get_all("Origin", failobj=[])
        try:
            normalized_origin = normalize_origin(origin_values[0]) if len(origin_values) == 1 else ""
        except ConfigurationError:
            normalized_origin = ""
        if normalized_origin not in self.server.config.allowed_origins:
            self._close_rejected_request()
            self._error(HTTPStatus.FORBIDDEN, "origin is not allowed")
            return False
        if self.headers.get("X-Requested-With") != "homelab-dashboard":
            self._close_rejected_request()
            self._error(HTTPStatus.FORBIDDEN, "missing dashboard request header")
            return False
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != expected_content_type:
            self._close_rejected_request()
            self._error(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                f"Content-Type must be {expected_content_type}",
            )
            return False
        return True

    def _read_content_length(self, maximum_body: int) -> int | None:
        content_length = self._parsed_content_length
        if content_length is None:
            self._close_rejected_request()
            self._error(HTTPStatus.BAD_REQUEST, "invalid Content-Length")
            return None
        if content_length > maximum_body:
            self._close_rejected_request()
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request body is too large")
            return None
        self._send_continue_if_pending()
        return content_length

    def _read_exact_body(self, content_length: int) -> bytes | None:
        self.close_connection = True
        body = bytearray()
        try:
            while len(body) < content_length:
                body.extend(self._read_body_chunk(content_length - len(body)))
        except RequestBodyReadError:
            self._request_body_timeout()
            return None
        self._restore_request_connection()
        return bytes(body)

    class _DeadlineLimitedReader:
        def __init__(self, handler: "StateRequestHandler", content_length: int) -> None:
            self.handler = handler
            self.remaining = content_length
            if self.remaining == 0:
                self.handler._restore_request_connection()

        def read(self, size: int = -1) -> bytes:
            if self.remaining == 0:
                return b""
            requested = self.remaining if size < 0 else min(size, self.remaining)
            chunk = self.handler._read_body_chunk(requested)
            self.remaining -= len(chunk)
            if self.remaining == 0:
                self.handler._restore_request_connection()
            return chunk

    def _read_dashboard_json(self, maximum_body: int) -> Any | None:
        # Close rejected requests whose unread body would otherwise be parsed as a second HTTP request.
        self.close_connection = True
        if not self._validate_dashboard_headers("application/json"):
            return None
        content_length = self._read_content_length(maximum_body)
        if content_length is None:
            return None

        def reject_duplicate_fields(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError(f"duplicate JSON field: {key}")
                value[key] = item
            return value

        body = self._read_exact_body(content_length)
        if body is None:
            return None
        try:
            value = json.loads(body, object_pairs_hook=reject_duplicate_fields)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            self._error(HTTPStatus.BAD_REQUEST, "invalid JSON request")
            return None
        if value is None:
            self._error(HTTPStatus.BAD_REQUEST, "JSON request must not be null")
            return None
        return value

    def _handle_background_upload(self, parsed_path) -> None:
        self.close_connection = True
        if not self._validate_dashboard_headers("application/octet-stream"):
            return
        query = parse_qsl(parsed_path.query, keep_blank_values=True)
        if len(query) != 1 or query[0][0] != "name":
            self._close_rejected_request()
            self._error(HTTPStatus.BAD_REQUEST, "query must contain exactly one name")
            return
        try:
            validate_background_name(query[0][1])
        except InvalidBackground:
            self._close_rejected_request()
            self._error(HTTPStatus.BAD_REQUEST, "invalid background filename")
            return
        content_length = self._read_content_length(MAX_BACKGROUND_UPLOAD_BODY)
        if content_length is None:
            return
        body_reader = self._DeadlineLimitedReader(self, content_length)
        try:
            result = self.server.store.upload_background(query[0][1], body_reader, content_length)
        except RequestBodyReadError:
            self._request_body_timeout()
            return
        except InvalidBackground:
            self._error(HTTPStatus.BAD_REQUEST, "invalid background filename")
            return
        except UploadTooLarge:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "uploaded image is too large")
            return
        except BackgroundQuotaExceeded:
            self._error(HTTPStatus.CONFLICT, "background_file_quota_exceeded")
            return
        except InvalidImage as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
            return
        except Exception:
            self.server.logger.exception("POST %s failed", parsed_path.path)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal server error")
            return
        self._write_json(HTTPStatus.CREATED, result)

    def _handle_background_remove(self) -> None:
        request = self._read_dashboard_json(MAX_BACKGROUND_REMOVE_BODY)
        if request is None:
            return
        if (
            not isinstance(request, dict)
            or set(request) not in ({"background"}, {"background", "confirmationToken"})
            or not isinstance(request.get("background"), str)
            or ("confirmationToken" in request and not isinstance(request["confirmationToken"], str))
        ):
            self._error(
                HTTPStatus.BAD_REQUEST,
                "request must contain background and an optional confirmationToken",
            )
            return
        background = request["background"]
        confirmation_token = request.get("confirmationToken")
        try:
            disposition, preferences = self.server.store.remove_background(background, confirmation_token)
        except (InvalidBackground, ProtectedBackground):
            self._error(HTTPStatus.BAD_REQUEST, "background cannot be removed")
            return
        except BackgroundMissing:
            self._error(HTTPStatus.NOT_FOUND, "background file does not exist")
            return
        except InvalidConfirmationToken:
            self._error(HTTPStatus.FORBIDDEN, "invalid or expired confirmation token")
            return
        except TrashUnavailable as error:
            self._write_json(
                HTTPStatus.CONFLICT,
                {
                    "error": "trash_unavailable",
                    "confirmationToken": error.confirmation_token,
                    "expiresIn": REMOVAL_TOKEN_TTL_SECONDS,
                },
            )
            return
        except Exception:
            self.server.logger.exception("POST %s failed", BACKGROUND_REMOVE_PATH)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal server error")
            return
        self._write_json(
            HTTPStatus.OK,
            {
                "background": background,
                "disposition": disposition,
                "preferences": preferences,
            },
        )

    def _handle_bestdori_draw(self) -> None:
        request = self._read_dashboard_json(MAX_BESTDORI_REQUEST_BODY)
        if request is None:
            return
        if not isinstance(request, dict) or request.get("mode") not in {"character", "random"}:
            self._error(HTTPStatus.BAD_REQUEST, "mode must be character or random")
            return
        mode = request["mode"]
        expected_fields = {"mode", "rarities", "characterId"} if mode == "character" else {"mode", "rarities"}
        rarities = request.get("rarities")
        if (
            set(request) != expected_fields
            or not isinstance(rarities, list)
            or not rarities
            or len(rarities) > len(BESTDORI_RARITIES)
            or any(isinstance(rarity, bool) or not isinstance(rarity, int) for rarity in rarities)
            or set(rarities) - BESTDORI_RARITIES
            or len(set(rarities)) != len(rarities)
        ):
            self._error(HTTPStatus.BAD_REQUEST, "rarities must contain unique values from 3, 4 and 5")
            return
        character_id = request.get("characterId")
        if mode == "character" and (
            isinstance(character_id, bool)
            or not isinstance(character_id, int)
            or character_id not in BESTDORI_CHARACTER_IDS
        ):
            self._error(HTTPStatus.BAD_REQUEST, "characterId must be between 1 and 40")
            return
        try:
            result = self.server.bestdori_manager.draw(mode, set(rarities), character_id)
        except BestdoriDrawBusy:
            self._error(HTTPStatus.CONFLICT, "bestdori_operation_in_progress")
            return
        except BestdoriPoolExhausted:
            self._error(HTTPStatus.CONFLICT, "bestdori_card_pool_exhausted")
            return
        except BackgroundQuotaExceeded:
            self._error(HTTPStatus.CONFLICT, "background_file_quota_exceeded")
            return
        except BestdoriHistoryError:
            self.server.logger.exception("Bestdori draw history is unavailable")
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "bestdori_history_unavailable")
            return
        except BestdoriUnavailable:
            self.server.logger.exception("Bestdori draw failed")
            self._error(HTTPStatus.BAD_GATEWAY, "bestdori_unavailable")
            return
        except (InvalidBackground, InvalidImage, UploadTooLarge):
            self.server.logger.exception("Bestdori returned an invalid card image")
            self._error(HTTPStatus.BAD_GATEWAY, "bestdori_invalid_image")
            return
        except Exception:
            self.server.logger.exception("POST %s failed", BESTDORI_DRAW_PATH)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal server error")
            return
        self._write_json(HTTPStatus.CREATED, result)

    def _handle_bestdori_history(self, action: str) -> None:
        request = self._read_dashboard_json(MAX_BESTDORI_REQUEST_BODY)
        if request is None:
            return
        if not isinstance(request, dict) or request:
            self._error(HTTPStatus.BAD_REQUEST, "request must be an empty JSON object")
            return
        try:
            result = (
                self.server.bestdori_manager.reconcile()
                if action == "reconcile"
                else self.server.bestdori_manager.reset()
            )
        except BestdoriDrawBusy:
            self._error(HTTPStatus.CONFLICT, "bestdori_operation_in_progress")
            return
        except BestdoriHistoryError:
            self.server.logger.exception("Bestdori draw history operation failed")
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "bestdori_history_unavailable")
            return
        except Exception:
            self.server.logger.exception("Bestdori draw history operation failed")
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal server error")
            return
        self._write_json(HTTPStatus.OK, result)

    def _handle_bing_daily(self) -> None:
        request = self._read_dashboard_json(MAX_BING_DAILY_REQUEST_BODY)
        if request is None:
            return
        if not isinstance(request, dict) or request:
            self._error(HTTPStatus.BAD_REQUEST, "request must be an empty JSON object")
            return
        try:
            result = self.server.bing_manager.fetch_daily()
        except BingDailyBusy:
            self._error(HTTPStatus.CONFLICT, "bing_operation_in_progress")
            return
        except Exception:
            self.server.logger.exception("POST %s failed", BING_DAILY_PATH)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal server error")
            return

        results = result.get("results") if isinstance(result, dict) else None
        if not isinstance(results, list) or len(results) != 2:
            self.server.logger.error("Bing daily manager returned an invalid result")
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal server error")
            return
        if any(isinstance(item, dict) and item.get("status") in {"created", "existing"} for item in results):
            status = HTTPStatus.OK
        elif any(
            isinstance(item, dict) and item.get("error") == "background_file_quota_exceeded"
            for item in results
        ):
            status = HTTPStatus.CONFLICT
        else:
            status = HTTPStatus.BAD_GATEWAY
        self._write_json(status, result)

    def _handle_cards_initialize(self) -> None:
        request = self._read_dashboard_json(MAX_CARDS_REQUEST_BODY)
        if request is None:
            return
        try:
            state = self.server.cards_store.initialize(request)
        except InvalidCards as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
            return
        except CardsAlreadyInitialized:
            self._error(HTTPStatus.CONFLICT, "already_initialized")
            return
        except CardsStateUnavailable:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "cards_state_unavailable")
            return
        except Exception:
            self.server.logger.exception("POST %s failed", CARDS_INITIALIZE_PATH)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal server error")
            return
        self._write_json(HTTPStatus.CREATED, state)

    def _handle_cards_update(self) -> None:
        request = self._read_dashboard_json(MAX_CARDS_REQUEST_BODY)
        if request is None:
            return
        try:
            state = self.server.cards_store.update(request)
        except InvalidCards as error:
            payload = {"error": str(error)}
            if error.card_id is not None:
                payload["cardId"] = error.card_id
            self._write_json(HTTPStatus.BAD_REQUEST, payload)
            return
        except CardsRevisionConflict as error:
            self._write_json(
                HTTPStatus.CONFLICT,
                {
                    "error": "revision_conflict",
                    "currentRevision": error.current_revision,
                },
            )
            return
        except CardsNotInitialized:
            self._error(HTTPStatus.CONFLICT, "not_initialized")
            return
        except CardsStateUnavailable:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "cards_state_unavailable")
            return
        except Exception:
            self.server.logger.exception("PATCH %s failed", CARDS_PATH)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal server error")
            return
        self._write_json(HTTPStatus.OK, state)

    def _svg_icon_store(self, path: str) -> tuple[FaviconStore, str]:
        if path in {PAGE_ICON_PATH, PAGE_ICON_ASSET_PATH}:
            return self.server.page_icon_store, "page_icon"
        return self.server.favicon_store, "favicon"

    def _handle_favicon_update(self) -> None:
        store, error_prefix = self._svg_icon_store(urlsplit(self.path).path)
        request = self._read_dashboard_json(MAX_FAVICON_REQUEST_BODY)
        if request is None:
            return
        try:
            state = store.update(request)
        except InvalidFavicon as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
            return
        except FaviconStateUnavailable:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, f"{error_prefix}_state_unavailable")
            return
        except Exception:
            self.server.logger.exception("PATCH %s failed", self.path)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal server error")
            return
        self._write_json(HTTPStatus.OK, state)

    def _handle_favicon_asset(self, parsed_path) -> None:
        store, error_prefix = self._svg_icon_store(parsed_path.path)
        try:
            query = parse_qsl(
                parsed_path.query,
                keep_blank_values=True,
                strict_parsing=True,
                max_num_fields=2,
            )
        except ValueError:
            query = []
        if (
            len(query) != 1
            or query[0][0] != "v"
            or re.fullmatch(r"[0-9a-f]{64}", query[0][1]) is None
            or parsed_path.fragment
        ):
            self._error(HTTPStatus.NOT_FOUND, f"{error_prefix}_not_found")
            return
        try:
            icon_svg, content_hash = store.get_asset(query[0][1])
        except FaviconNotFound:
            self._error(HTTPStatus.NOT_FOUND, f"{error_prefix}_not_found")
            return
        except FaviconStateUnavailable:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, f"{error_prefix}_state_unavailable")
            return
        except Exception:
            self.server.logger.exception("GET %s failed", parsed_path.path)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal server error")
            return
        self._write_favicon_svg(icon_svg, content_hash)

    def do_GET(self) -> None:
        if handle_notes_request(self):
            return
        parsed_path = urlsplit(self.path)
        if self.path in {FAVICON_PATH, PAGE_ICON_PATH}:
            store, error_prefix = self._svg_icon_store(self.path)
            try:
                self._write_json(HTTPStatus.OK, store.get())
            except FaviconStateUnavailable:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, f"{error_prefix}_state_unavailable")
            except Exception:
                self.server.logger.exception("GET %s failed", self.path)
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal server error")
            return
        if parsed_path.path in {FAVICON_ASSET_PATH, PAGE_ICON_ASSET_PATH}:
            self._handle_favicon_asset(parsed_path)
            return
        if self.path == CARDS_PATH:
            try:
                self._write_json(HTTPStatus.OK, self.server.cards_store.get())
            except CardsStateUnavailable:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "cards_state_unavailable")
            except Exception:
                self.server.logger.exception("GET %s failed", self.path)
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal server error")
            return
        if self.path == API_PATH:
            try:
                self._write_json(HTTPStatus.OK, self.server.store.get())
            except Exception as error:
                self.server.logger.exception("GET %s failed", self.path)
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal server error")
            return
        if self.path == BACKGROUND_FILES_PATH:
            try:
                self._write_json(HTTPStatus.OK, self.server.store.list_backgrounds())
            except Exception:
                self.server.logger.exception("GET %s failed", self.path)
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal server error")
            return
        if self.path == HEALTH_PATH:
            try:
                self.server.store.get()
                if self.server.probe_client.executor is None:
                    raise RuntimeError("probe client is unavailable")
            except Exception:
                self.server.logger.exception("health check failed")
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "unhealthy")
                return
            self._write_json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "service": SERVICE_NAME,
                    "version": SERVICE_VERSION,
                    "pid": os.getpid(),
                },
            )
            return
        if self.path == BESTDORI_CHARACTERS_PATH:
            try:
                characters = self.server.bestdori_manager.characters()
            except BestdoriUnavailable:
                self.server.logger.exception("GET %s failed", self.path)
                self._error(HTTPStatus.BAD_GATEWAY, "bestdori_unavailable")
                return
            except Exception:
                self.server.logger.exception("GET %s failed", self.path)
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal server error")
                return
            self._write_json(HTTPStatus.OK, {"characters": characters})
            return
        if self.path == SERVICE_STATUS_PATH:
            self._allow("POST")
            return
        if self.path == CARDS_INITIALIZE_PATH:
            self._allow("POST")
            return
        if urlsplit(self.path).path in {
            BACKGROUND_UPLOAD_PATH,
            BACKGROUND_REMOVE_PATH,
            BESTDORI_DRAW_PATH,
            BESTDORI_RECONCILE_PATH,
            BESTDORI_RESET_PATH,
            BING_DAILY_PATH,
        }:
            self._allow("POST")
            return
        self._error(HTTPStatus.NOT_FOUND, "not found")

    def do_PATCH(self) -> None:
        if handle_notes_request(self):
            return
        if self.path in {FAVICON_PATH, PAGE_ICON_PATH}:
            self._handle_favicon_update()
            return
        if self.path == CARDS_PATH:
            self._handle_cards_update()
            return
        if self.path == CARDS_INITIALIZE_PATH:
            self._allow("POST")
            return
        if self.path == SERVICE_STATUS_PATH:
            self._allow("POST")
            return
        if urlsplit(self.path).path in {
            BACKGROUND_UPLOAD_PATH,
            BACKGROUND_REMOVE_PATH,
            BESTDORI_DRAW_PATH,
            BESTDORI_RECONCILE_PATH,
            BESTDORI_RESET_PATH,
            BING_DAILY_PATH,
        }:
            self._allow("POST")
            return
        if self.path == BESTDORI_CHARACTERS_PATH:
            self._allow("GET")
            return
        if self.path == BACKGROUND_FILES_PATH:
            self._allow("GET")
            return
        if urlsplit(self.path).path in {FAVICON_ASSET_PATH, PAGE_ICON_ASSET_PATH}:
            self._allow("GET")
            return
        if self.path != API_PATH:
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        request = self._read_dashboard_json(MAX_PATCH_BODY)
        if request is None:
            return
        if not isinstance(request, dict) or set(request) != {"mode", "background"}:
            self._error(HTTPStatus.BAD_REQUEST, "request must contain only mode and background")
            return
        if not isinstance(request["mode"], str) or request["mode"] not in {"landscape", "portrait"}:
            self._error(HTTPStatus.BAD_REQUEST, "mode must be landscape or portrait")
            return
        if not isinstance(request["background"], str):
            self._error(HTTPStatus.BAD_REQUEST, "invalid background filename")
            return
        try:
            state = self.server.store.update(request["mode"], request["background"])
        except InvalidBackground:
            self._error(HTTPStatus.BAD_REQUEST, "invalid background filename")
            return
        except BackgroundMissing:
            self._error(HTTPStatus.NOT_FOUND, "background file does not exist")
            return
        except Exception:
            self.server.logger.exception("PATCH %s failed", self.path)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal server error")
            return
        self._write_json(HTTPStatus.OK, state)

    def do_POST(self) -> None:
        if handle_notes_request(self):
            return
        parsed_path = urlsplit(self.path)
        if self.path in {FAVICON_PATH, PAGE_ICON_PATH}:
            self._allow("GET, PATCH")
            return
        if parsed_path.path in {FAVICON_ASSET_PATH, PAGE_ICON_ASSET_PATH}:
            self._allow("GET")
            return
        if self.path == CARDS_INITIALIZE_PATH:
            self._handle_cards_initialize()
            return
        if self.path == CARDS_PATH:
            self._allow("GET, PATCH")
            return
        if parsed_path.path == BACKGROUND_UPLOAD_PATH:
            self._handle_background_upload(parsed_path)
            return
        if self.path == BACKGROUND_REMOVE_PATH:
            self._handle_background_remove()
            return
        if self.path == BESTDORI_DRAW_PATH:
            self._handle_bestdori_draw()
            return
        if self.path == BESTDORI_RECONCILE_PATH:
            self._handle_bestdori_history("reconcile")
            return
        if self.path == BESTDORI_RESET_PATH:
            self._handle_bestdori_history("reset")
            return
        if self.path == BING_DAILY_PATH:
            self._handle_bing_daily()
            return
        if self.path == BESTDORI_CHARACTERS_PATH:
            self._allow("GET")
            return
        if self.path == BACKGROUND_FILES_PATH:
            self._allow("GET")
            return
        if self.path == SERVICE_STATUS_PATH:
            request = self._read_dashboard_json(MAX_SERVICE_STATUS_BODY)
            if request is None:
                return
            if not isinstance(request, dict) or set(request) != {"targets"}:
                self._error(HTTPStatus.BAD_REQUEST, "request must contain only targets")
                return
            targets = request["targets"]
            if (
                not isinstance(targets, list)
                or not targets
                or len(targets) > MAX_PROBE_TARGETS
                or not all(isinstance(target, str) for target in targets)
            ):
                self._error(HTTPStatus.BAD_REQUEST, "targets must contain between 1 and 32 strings")
                return
            results = self.server.probe_client.probe_many(targets)
            self._write_json(HTTPStatus.OK, {"results": [result.as_json() for result in results]})
            return
        if self.path != CONTROL_PATH:
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        if not secrets.compare_digest(self.headers.get("X-Control-Token", ""), self.server.config.control_token):
            self._error(HTTPStatus.FORBIDDEN, "invalid control token")
            return
        self._write_json(HTTPStatus.ACCEPTED, {"status": "stopping"})
        threading.Thread(target=self.server.shutdown, name="shutdown-request", daemon=True).start()

    def do_PUT(self) -> None:
        self._method_not_allowed()

    def do_DELETE(self) -> None:
        self._method_not_allowed()

    def do_HEAD(self) -> None:
        self._method_not_allowed()

    def do_OPTIONS(self) -> None:
        self._method_not_allowed()

    def _method_not_allowed(self) -> None:
        if handle_notes_request(self):
            return
        parsed_path = urlsplit(self.path)
        if self.path == API_PATH:
            self._allow("GET, PATCH")
        elif self.path in {FAVICON_PATH, PAGE_ICON_PATH}:
            self._allow("GET, PATCH")
        elif parsed_path.path in {FAVICON_ASSET_PATH, PAGE_ICON_ASSET_PATH}:
            self._allow("GET")
        elif self.path == CARDS_PATH:
            self._allow("GET, PATCH")
        elif self.path == CARDS_INITIALIZE_PATH:
            self._allow("POST")
        elif self.path == BACKGROUND_FILES_PATH:
            self._allow("GET")
        elif self.path == BESTDORI_CHARACTERS_PATH:
            self._allow("GET")
        elif self.path == SERVICE_STATUS_PATH:
            self._allow("POST")
        elif urlsplit(self.path).path in {
            BACKGROUND_UPLOAD_PATH,
            BACKGROUND_REMOVE_PATH,
            BESTDORI_DRAW_PATH,
            BESTDORI_RECONCILE_PATH,
            BESTDORI_RESET_PATH,
            BING_DAILY_PATH,
        }:
            self._allow("POST")
        else:
            self._error(HTTPStatus.NOT_FOUND, "not found")
