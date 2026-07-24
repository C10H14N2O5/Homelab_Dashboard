from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import ctypes
import http.client
import ipaddress
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import re
import secrets
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urljoin, urlsplit
from urllib.request import Request, urlopen

from PIL import Image, UnidentifiedImageError
from send2trash import send2trash


API_PATH = "/api/background-preferences"
SERVICE_STATUS_PATH = "/api/service-status"
BACKGROUND_UPLOAD_PATH = "/api/background-files/upload"
BACKGROUND_REMOVE_PATH = "/api/background-files/remove"
HEALTH_PATH = "/healthz"
CONTROL_PATH = "/_control/shutdown"
DEFAULT_CONFIG_NAME = "HomeLabWallpaperState.json"
DEFAULT_DATA_DIRECTORY = "HomeLabWallpaperState-data"
DEFAULT_BACKGROUND_DIRECTORY = r"D:\Zoraxy\www\html\backgrounds"
DEFAULT_BACKGROUND = "bg.jpg"
DEFAULT_ORIGIN = "https://home.c10h14n2o5.com"
DEFAULT_LISTEN = "127.0.0.1:8787"
DEFAULT_PROBE_CONNECT_ADDRESS = "127.0.0.1:443"
DEFAULT_PROBE_DOMAIN_SUFFIX = "home.c10h14n2o5.com"
DEFAULT_PROBE_TIMEOUT_SECONDS = 5.0
MONITOR_REFRESH_SECONDS = 60.0
LOG_MAX_BYTES = 48 * 1024
LOG_BACKUP_COUNT = 1
SERVICE_NAME = "HomeLabWallpaperState"
SERVICE_VERSION = 3
CONFIG_VERSION = 2
STATE_VERSION = 1
MAX_PATCH_BODY = 4 * 1024
MAX_SERVICE_STATUS_BODY = 16 * 1024
MAX_BACKGROUND_REMOVE_BODY = 4 * 1024
MAX_BACKGROUND_UPLOAD_BODY = 50 * 1024 * 1024
MAX_BACKGROUND_PIXELS = 100_000_000
BACKGROUND_UPLOAD_CHUNK_SIZE = 1024 * 1024
REMOVAL_TOKEN_TTL_SECONDS = 60
MAX_REMOVAL_TOKENS = 32
MAX_REJECTED_BODY_DRAIN = 64 * 1024
MAX_PROBE_TARGETS = 32
MAX_PROBE_WORKERS = 8
MAX_PROBE_REDIRECTS = 5
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".avif"}
INVALID_BACKGROUND_NAME_PATTERN = re.compile(r'[\x00-\x1f<>:"/\\|?*]')
MAX_BACKGROUND_NAME_LENGTH = 255
IMAGE_FORMAT_EXTENSIONS = {
    "JPEG": {".jpg", ".jpeg"},
    "PNG": {".png"},
    "WEBP": {".webp"},
    "AVIF": {".avif"},
}
DOMAIN_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class ConfigurationError(ValueError):
    pass


class InvalidBackground(ValueError):
    pass


class BackgroundMissing(FileNotFoundError):
    pass


class InvalidImage(ValueError):
    pass


class UploadTooLarge(ValueError):
    pass


class ProtectedBackground(ValueError):
    pass


class InvalidConfirmationToken(ValueError):
    pass


class TrashUnavailable(RuntimeError):
    def __init__(self, confirmation_token: str) -> None:
        super().__init__("trash is unavailable")
        self.confirmation_token = confirmation_token


def application_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def default_config_path() -> Path:
    return application_directory() / DEFAULT_CONFIG_NAME


def configure_console_encoding() -> None:
    if os.name != "nt":
        return
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleCP(65001)
        kernel32.SetConsoleOutputCP(65001)
    except (AttributeError, OSError):
        # Redirected and detached processes may not have an attached console.
        pass
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            # Keep startup working for streams supplied by non-console hosts.
            pass


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_origin(raw: str) -> str:
    value = raw.strip()
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ConfigurationError("origin must use http or https")
    if not parsed.netloc or parsed.username or parsed.password:
        raise ConfigurationError("origin must contain a host and no credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ConfigurationError("origin must not contain a path, query, or fragment")
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def parse_listen(value: str) -> tuple[str, int]:
    text = value.strip()
    if text.count(":") != 1:
        raise ConfigurationError("listen must use 127.0.0.1:<port>")
    host, port_text = text.rsplit(":", 1)
    if host != "127.0.0.1":
        raise ConfigurationError("listen must bind to 127.0.0.1")
    try:
        port = int(port_text)
    except ValueError as error:
        raise ConfigurationError("listen port must be an integer") from error
    if not 1 <= port <= 65535:
        raise ConfigurationError("listen port must be between 1 and 65535")
    return host, port


def parse_probe_connect_address(value: str) -> tuple[str, int]:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError("probeConnectAddress must be a non-empty string")
    text = value.strip()
    try:
        parsed = urlsplit(f"//{text}")
        host = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise ConfigurationError("probeConnectAddress must contain a loopback IP and port") from error
    if not host or port is None or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise ConfigurationError("probeConnectAddress must contain a loopback IP and port")
    try:
        address = ipaddress.ip_address(host)
    except ValueError as error:
        raise ConfigurationError("probeConnectAddress host must be a loopback IP address") from error
    if not address.is_loopback:
        raise ConfigurationError("probeConnectAddress host must be a loopback IP address")
    if not 1 <= port <= 65535:
        raise ConfigurationError("probeConnectAddress port must be between 1 and 65535")
    return str(address), port


def normalize_probe_domain_suffix(value: str) -> str:
    if not isinstance(value, str) or value != value.strip() or not value:
        raise ConfigurationError("probeDomainSuffix must be a canonical domain name")
    domain = value.lower()
    if len(domain) > 253 or domain.endswith(".") or "." not in domain:
        raise ConfigurationError("probeDomainSuffix must be a canonical domain name")
    if any(not DOMAIN_LABEL_PATTERN.fullmatch(label) for label in domain.split(".")):
        raise ConfigurationError("probeDomainSuffix must be a canonical domain name")
    return domain


def normalize_probe_timeout(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError("probeTimeoutSeconds must be a number")
    timeout = float(value)
    if not 1.0 <= timeout <= 5.0:
        raise ConfigurationError("probeTimeoutSeconds must be between 1 and 5")
    return timeout


def path_is_within(candidate: Path, directory: Path) -> bool:
    try:
        candidate.resolve().relative_to(directory.resolve())
        return True
    except ValueError:
        return False


def resolve_config_path(value: str, base: Path) -> Path:
    path = Path(os.path.expandvars(value)).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


@dataclass(frozen=True)
class AppConfig:
    config_path: Path
    background_dir: Path
    allowed_origins: frozenset[str]
    listen_host: str
    listen_port: int
    state_dir: Path
    control_token: str
    probe_connect_host: str
    probe_connect_port: int
    probe_domain_suffix: str
    probe_timeout_seconds: float

    @property
    def listen(self) -> str:
        return f"{self.listen_host}:{self.listen_port}"

    @property
    def state_file(self) -> Path:
        return self.state_dir / "state.json"

    @property
    def log_file(self) -> Path:
        return self.state_dir / "service.log"

    @property
    def local_base_url(self) -> str:
        return f"http://{self.listen}"

    @property
    def probe_connect_address(self) -> str:
        host = f"[{self.probe_connect_host}]" if ":" in self.probe_connect_host else self.probe_connect_host
        return f"{host}:{self.probe_connect_port}"


def load_config(path: Path) -> AppConfig:
    config_path = path.resolve()
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigurationError(f"configuration file does not exist: {config_path}") from None
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigurationError(f"unable to read configuration: {error}") from error
    if not isinstance(raw, dict):
        raise ConfigurationError("configuration must be a JSON object")
    expected = {
        "version",
        "backgroundDir",
        "allowedOrigins",
        "listen",
        "stateDir",
        "controlToken",
        "probeConnectAddress",
        "probeDomainSuffix",
        "probeTimeoutSeconds",
    }
    if set(raw) != expected:
        missing = sorted(expected - set(raw))
        unknown = sorted(set(raw) - expected)
        details = []
        if missing:
            details.append(f"missing fields: {', '.join(missing)}")
        if unknown:
            details.append(f"unknown fields: {', '.join(unknown)}")
        raise ConfigurationError("; ".join(details))
    if raw["version"] != CONFIG_VERSION:
        raise ConfigurationError(f"unsupported configuration version: {raw['version']!r}")
    if not isinstance(raw["backgroundDir"], str) or not raw["backgroundDir"].strip():
        raise ConfigurationError("backgroundDir must be a non-empty string")
    if not isinstance(raw["stateDir"], str) or not raw["stateDir"].strip():
        raise ConfigurationError("stateDir must be a non-empty string")
    origins = raw["allowedOrigins"]
    if not isinstance(origins, list) or not origins or not all(isinstance(item, str) for item in origins):
        raise ConfigurationError("allowedOrigins must be a non-empty string array")
    if not isinstance(raw["listen"], str):
        raise ConfigurationError("listen must be a string")
    token = raw["controlToken"]
    if not isinstance(token, str) or len(token) < 32:
        raise ConfigurationError("controlToken must contain at least 32 characters")

    base = config_path.parent
    background_dir = resolve_config_path(raw["backgroundDir"], base)
    state_dir = resolve_config_path(raw["stateDir"], base)
    host, port = parse_listen(raw["listen"])
    probe_connect_host, probe_connect_port = parse_probe_connect_address(raw["probeConnectAddress"])
    probe_domain_suffix = normalize_probe_domain_suffix(raw["probeDomainSuffix"])
    probe_timeout_seconds = normalize_probe_timeout(raw["probeTimeoutSeconds"])
    normalized_origins = frozenset(normalize_origin(item) for item in origins)
    if not background_dir.is_dir():
        raise ConfigurationError(f"background directory does not exist: {background_dir}")
    if not (background_dir / DEFAULT_BACKGROUND).is_file():
        raise ConfigurationError(f"background directory must contain {DEFAULT_BACKGROUND}: {background_dir}")
    public_root = background_dir.parent
    if path_is_within(config_path, public_root):
        raise ConfigurationError("configuration file must be outside the public Web root")
    if path_is_within(state_dir, public_root):
        raise ConfigurationError("stateDir must be outside the public Web root")
    state_dir.mkdir(parents=True, exist_ok=True)
    return AppConfig(
        config_path=config_path,
        background_dir=background_dir,
        allowed_origins=normalized_origins,
        listen_host=host,
        listen_port=port,
        state_dir=state_dir,
        control_token=token,
        probe_connect_host=probe_connect_host,
        probe_connect_port=probe_connect_port,
        probe_domain_suffix=probe_domain_suffix,
        probe_timeout_seconds=probe_timeout_seconds,
    )


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_background_name(name: str) -> str:
    if (
        not isinstance(name, str)
        or not name
        or len(name) > MAX_BACKGROUND_NAME_LENGTH
        or name.strip() != name
        or name.endswith(".")
    ):
        raise InvalidBackground("invalid background filename")
    if name in {".", ".."} or INVALID_BACKGROUND_NAME_PATTERN.search(name):
        raise InvalidBackground("invalid background filename")
    if Path(name).name != name or Path(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise InvalidBackground("invalid background filename")
    return name


class StateStore:
    def __init__(
        self,
        background_dir: Path,
        state_file: Path,
        clock=utc_timestamp,
        monotonic=time.monotonic,
    ) -> None:
        self._lock = threading.Lock()
        self.background_dir = background_dir.resolve()
        self.state_file = state_file.resolve()
        self.clock = clock
        self.monotonic = monotonic
        self._removal_tokens: dict[str, tuple[str, float]] = {}
        self.state = self._load_or_initialize()

    def _require_background(self, name: str) -> None:
        validate_background_name(name)
        path = self.background_dir / name
        if not path.is_file() or path.is_symlink():
            raise BackgroundMissing("background file does not exist")

    def _new_state(self) -> dict[str, Any]:
        self._require_background(DEFAULT_BACKGROUND)
        return {
            "version": STATE_VERSION,
            "landscape": DEFAULT_BACKGROUND,
            "portrait": DEFAULT_BACKGROUND,
            "updatedAt": self.clock(),
        }

    def _validate_state_shape(self, state: Any) -> dict[str, Any]:
        if not isinstance(state, dict):
            raise ValueError("state file must contain a JSON object")
        expected = {"version", "landscape", "portrait", "updatedAt"}
        if set(state) != expected:
            raise ValueError("state file contains missing or unknown fields")
        if state["version"] != STATE_VERSION:
            raise ValueError(f"unsupported state version {state['version']!r}")
        if not all(isinstance(state[key], str) for key in ("landscape", "portrait", "updatedAt")):
            raise ValueError("state fields have invalid types")
        return dict(state)

    def _load_or_initialize(self) -> dict[str, Any]:
        try:
            state = self._validate_state_shape(json.loads(self.state_file.read_text(encoding="utf-8")))
        except FileNotFoundError:
            state = self._new_state()
            atomic_write_json(self.state_file, state)
            return state
        changed = self._heal(state)
        try:
            datetime.fromisoformat(state["updatedAt"].replace("Z", "+00:00"))
        except ValueError:
            changed = True
        if changed:
            state["updatedAt"] = self.clock()
            atomic_write_json(self.state_file, state)
        return state

    def _heal(self, state: dict[str, Any]) -> bool:
        self._require_background(DEFAULT_BACKGROUND)
        changed = False
        for mode in ("landscape", "portrait"):
            try:
                self._require_background(state[mode])
            except (InvalidBackground, BackgroundMissing):
                state[mode] = DEFAULT_BACKGROUND
                changed = True
        return changed

    def get(self) -> dict[str, Any]:
        with self._lock:
            previous = dict(self.state)
            changed = self._heal(self.state)
            if changed:
                self.state["updatedAt"] = self.clock()
                try:
                    atomic_write_json(self.state_file, self.state)
                except OSError:
                    self.state = previous
                    raise
            return dict(self.state)

    def update(self, mode: str, background: str) -> dict[str, Any]:
        if mode not in {"landscape", "portrait"}:
            raise ValueError("mode must be landscape or portrait")
        with self._lock:
            self._require_background(background)
            previous = dict(self.state)
            changed = self._heal(self.state)
            if self.state[mode] != background:
                self.state[mode] = background
                changed = True
            if changed:
                self.state["updatedAt"] = self.clock()
                try:
                    atomic_write_json(self.state_file, self.state)
                except OSError:
                    self.state = previous
                    raise
            return dict(self.state)

    def _next_available_background_name(self, requested_name: str) -> str:
        existing = {path.name.casefold() for path in self.background_dir.iterdir()}
        if requested_name.casefold() not in existing:
            return requested_name
        requested = Path(requested_name)
        index = 2
        while True:
            candidate = f"{requested.stem} ({index}){requested.suffix}"
            if candidate.casefold() not in existing:
                return candidate
            index += 1

    def _validate_uploaded_image(self, path: Path, suffix: str) -> tuple[int, int]:
        try:
            with warnings.catch_warnings():
                # Pillow's default warning threshold is lower than this service's
                # explicit 100 MP contract. Ignore only that warning and enforce
                # the exact limit before any pixel data is decoded.
                warnings.simplefilter("ignore", Image.DecompressionBombWarning)
                with Image.open(path) as image:
                    image_format = image.format
                    width, height = image.size
                    if image_format not in IMAGE_FORMAT_EXTENSIONS:
                        raise InvalidImage("unsupported image format")
                    if suffix.lower() not in IMAGE_FORMAT_EXTENSIONS[image_format]:
                        raise InvalidImage("image format does not match filename extension")
                    if width <= 0 or height <= 0 or width * height > MAX_BACKGROUND_PIXELS:
                        raise InvalidImage("image dimensions are too large")
                    image.verify()
                with Image.open(path) as image:
                    image.load()
        except InvalidImage:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise InvalidImage("image dimensions are too large") from None
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
            raise InvalidImage("invalid or damaged image") from None
        return width, height

    def upload_background(self, requested_name: str, stream: Any, content_length: int) -> dict[str, Any]:
        validate_background_name(requested_name)
        if content_length < 0:
            raise InvalidImage("invalid upload length")
        if content_length > MAX_BACKGROUND_UPLOAD_BODY:
            raise UploadTooLarge("uploaded image is too large")
        with self._lock:
            final_name = self._next_available_background_name(requested_name)
            handle, temporary_name = tempfile.mkstemp(prefix=".upload-", suffix=".tmp", dir=self.background_dir)
            temporary = Path(temporary_name)
            try:
                remaining = content_length
                with os.fdopen(handle, "wb") as output:
                    while remaining:
                        chunk = stream.read(min(BACKGROUND_UPLOAD_CHUNK_SIZE, remaining))
                        if not chunk:
                            raise InvalidImage("incomplete upload")
                        output.write(chunk)
                        remaining -= len(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                width, height = self._validate_uploaded_image(temporary, Path(final_name).suffix)
                while True:
                    destination = self.background_dir / final_name
                    try:
                        # A hard-link publish is atomic and, unlike replace(), never overwrites
                        # an external file that wins the destination-name race.
                        os.link(temporary, destination)
                        break
                    except FileExistsError:
                        # An external writer may have won the name after the directory scan.
                        final_name = self._next_available_background_name(requested_name)
                temporary.unlink()
                return {
                    "background": final_name,
                    "size": content_length,
                    "width": width,
                    "height": height,
                }
            finally:
                temporary.unlink(missing_ok=True)

    def _purge_expired_removal_tokens(self, now: float) -> None:
        for token, (_, expires_at) in list(self._removal_tokens.items()):
            if expires_at <= now:
                del self._removal_tokens[token]

    def _issue_removal_token(self, background: str, now: float) -> str:
        self._purge_expired_removal_tokens(now)
        while len(self._removal_tokens) >= MAX_REMOVAL_TOKENS:
            oldest = min(self._removal_tokens, key=lambda token: self._removal_tokens[token][1])
            del self._removal_tokens[oldest]
        token = secrets.token_urlsafe(32)
        self._removal_tokens[token] = (background, now + REMOVAL_TOKEN_TTL_SECONDS)
        return token

    def _require_removable_background(self, background: str) -> Path:
        validate_background_name(background)
        if background.casefold() == DEFAULT_BACKGROUND.casefold():
            raise ProtectedBackground("the default background cannot be removed")
        path = self.background_dir / background
        if path.is_symlink():
            raise InvalidBackground("symbolic links cannot be removed")
        if not path.is_file():
            raise BackgroundMissing("background file does not exist")
        return path

    def _preferences_after_removal(self) -> dict[str, Any]:
        previous = dict(self.state)
        changed = self._heal(self.state)
        if changed:
            self.state["updatedAt"] = self.clock()
            try:
                atomic_write_json(self.state_file, self.state)
            except OSError:
                self.state = previous
                raise
        return dict(self.state)

    def remove_background(
        self,
        background: str,
        confirmation_token: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        with self._lock:
            path = self._require_removable_background(background)
            now = self.monotonic()
            self._purge_expired_removal_tokens(now)
            if confirmation_token is not None:
                token_record = self._removal_tokens.pop(confirmation_token, None)
                if token_record is None or token_record[0] != background or token_record[1] <= now:
                    raise InvalidConfirmationToken("invalid or expired confirmation token")
                if path.is_symlink():
                    raise InvalidBackground("symbolic links cannot be removed")
                path.unlink()
                return "permanent", self._preferences_after_removal()

            try:
                send2trash(str(path))
            except Exception:
                if not path.exists() and not path.is_symlink():
                    return "trash", self._preferences_after_removal()
                token = self._issue_removal_token(background, now)
                raise TrashUnavailable(token) from None
            return "trash", self._preferences_after_removal()


@dataclass(frozen=True)
class ProbeResult:
    target: str
    status: int | None
    latency_ms: int
    error: str | None

    def as_json(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "status": self.status,
            "latencyMs": self.latency_ms,
            "error": self.error,
        }


def validate_probe_target(target: str, domain_suffix: str):
    try:
        parsed = urlsplit(target)
        port = parsed.port
        hostname = parsed.hostname.lower() if parsed.hostname else ""
    except (AttributeError, ValueError):
        return None
    if (
        "#" in target
        or parsed.scheme.lower() != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port not in {None, 443}
    ):
        return None
    try:
        ipaddress.ip_address(hostname)
        return None
    except ValueError:
        pass
    suffix = f".{domain_suffix}"
    if not hostname.endswith(suffix):
        return None
    label = hostname[: -len(suffix)]
    if not DOMAIN_LABEL_PATTERN.fullmatch(label):
        return None
    return parsed


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(
        self,
        target_host: str,
        connect_host: str,
        connect_port: int,
        timeout: float,
        context: ssl.SSLContext,
    ) -> None:
        super().__init__(target_host, port=443, timeout=timeout, context=context)
        self._connect_host = connect_host
        self._connect_port = connect_port

    def connect(self) -> None:
        raw_socket = socket.create_connection(
            (self._connect_host, self._connect_port),
            self.timeout,
            self.source_address,
        )
        try:
            self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)
        except Exception:
            raw_socket.close()
            raise


class ProbeClient:
    def __init__(
        self,
        config: AppConfig,
        *,
        ssl_context: ssl.SSLContext | None = None,
        connection_factory=None,
        clock=time.monotonic,
    ) -> None:
        self.connect_host = config.probe_connect_host
        self.connect_port = config.probe_connect_port
        self.domain_suffix = config.probe_domain_suffix
        self.timeout_seconds = config.probe_timeout_seconds
        self.ssl_context = ssl_context or ssl.create_default_context()
        self.connection_factory = connection_factory or PinnedHTTPSConnection
        self.clock = clock
        self.executor = ThreadPoolExecutor(max_workers=MAX_PROBE_WORKERS, thread_name_prefix="service-probe")

    def close(self) -> None:
        self.executor.shutdown(wait=True, cancel_futures=True)

    def _result(self, target: str, started: float, status: int | None, error: str | None) -> ProbeResult:
        latency_ms = max(0, round((self.clock() - started) * 1000))
        return ProbeResult(target, status, latency_ms, error)

    def probe_one(
        self,
        target: str,
        started: float | None = None,
        deadline: float | None = None,
    ) -> ProbeResult:
        started = self.clock() if started is None else started
        deadline = started + self.timeout_seconds if deadline is None else deadline
        current_target = target
        last_status: int | None = None
        for redirect_count in range(MAX_PROBE_REDIRECTS + 1):
            parsed = validate_probe_target(current_target, self.domain_suffix)
            if parsed is None:
                error = "invalid_target" if redirect_count == 0 else "redirect_rejected"
                return self._result(target, started, last_status, error)
            remaining = deadline - self.clock()
            if remaining <= 0:
                return self._result(target, started, last_status, "timeout")
            connection = self.connection_factory(
                parsed.hostname,
                self.connect_host,
                self.connect_port,
                remaining,
                self.ssl_context,
            )
            try:
                request_path = parsed.path or "/"
                if parsed.query:
                    request_path = f"{request_path}?{parsed.query}"
                connection.request(
                    "GET",
                    request_path,
                    headers={
                        "Host": parsed.netloc,
                        "Accept": "*/*",
                        "User-Agent": f"{SERVICE_NAME}/{SERVICE_VERSION}",
                        "Connection": "close",
                    },
                )
                response = connection.getresponse()
                last_status = response.status
                location = response.getheader("Location")
                response.close()
            except (socket.timeout, TimeoutError):
                return self._result(target, started, last_status, "timeout")
            except (ssl.SSLCertVerificationError, ssl.SSLError):
                return self._result(target, started, last_status, "tls_error")
            except http.client.HTTPException:
                return self._result(target, started, last_status, "request_error")
            except OSError:
                return self._result(target, started, last_status, "connection_error")
            except Exception:
                return self._result(target, started, last_status, "request_error")
            finally:
                connection.close()

            if last_status in {301, 302, 303, 307, 308} and location:
                if redirect_count >= MAX_PROBE_REDIRECTS:
                    return self._result(target, started, last_status, "redirect_rejected")
                next_target = urljoin(current_target, location)
                if validate_probe_target(next_target, self.domain_suffix) is None:
                    return self._result(target, started, last_status, "redirect_rejected")
                current_target = next_target
                continue
            return self._result(target, started, last_status, None)
        return self._result(target, started, last_status, "redirect_rejected")

    def probe_many(self, targets: list[str]) -> list[ProbeResult]:
        started = self.clock()
        deadline = started + self.timeout_seconds
        results: list[ProbeResult | None] = [None] * len(targets)
        futures = {
            self.executor.submit(self.probe_one, target, started, deadline): index
            for index, target in enumerate(targets)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception:
                results[index] = ProbeResult(targets[index], None, 0, "request_error")
        return [result for result in results if result is not None]


class StateHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        config: AppConfig,
        store: StateStore,
        logger: logging.Logger,
        probe_client: ProbeClient | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.logger = logger
        owns_probe_client = probe_client is None
        self.probe_client = probe_client or ProbeClient(config)
        try:
            super().__init__((config.listen_host, config.listen_port), StateRequestHandler)
        except Exception:
            if owns_probe_client:
                self.probe_client.close()
            raise


class StateRequestHandler(BaseHTTPRequestHandler):
    server: StateHTTPServer
    protocol_version = "HTTP/1.1"

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

    def _error(self, status: int, message: str) -> None:
        self._write_json(status, {"error": message})

    def _allow(self, methods: str) -> None:
        self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
        self._common_headers()
        self.send_header("Allow", methods)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _discard_rejected_body(self) -> None:
        try:
            content_length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return
        if 0 <= content_length <= MAX_REJECTED_BODY_DRAIN:
            self.rfile.read(content_length)
            self.close_connection = False

    def _validate_dashboard_headers(self, expected_content_type: str) -> bool:
        origin_values = self.headers.get_all("Origin", failobj=[])
        try:
            normalized_origin = normalize_origin(origin_values[0]) if len(origin_values) == 1 else ""
        except ConfigurationError:
            normalized_origin = ""
        if normalized_origin not in self.server.config.allowed_origins:
            self._discard_rejected_body()
            self._error(HTTPStatus.FORBIDDEN, "origin is not allowed")
            return False
        if self.headers.get("X-Requested-With") != "homelab-dashboard":
            self._discard_rejected_body()
            self._error(HTTPStatus.FORBIDDEN, "missing dashboard request header")
            return False
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != expected_content_type:
            self._discard_rejected_body()
            self._error(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                f"Content-Type must be {expected_content_type}",
            )
            return False
        return True

    def _read_content_length(self, maximum_body: int) -> int | None:
        try:
            content_length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "invalid Content-Length")
            return None
        if content_length < 0:
            self._error(HTTPStatus.BAD_REQUEST, "invalid Content-Length")
            return None
        if content_length > maximum_body:
            self._discard_rejected_body()
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request body is too large")
            return None
        return content_length

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

        body = self.rfile.read(content_length)
        self.close_connection = False
        try:
            return json.loads(body, object_pairs_hook=reject_duplicate_fields)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            self._error(HTTPStatus.BAD_REQUEST, "invalid JSON request")
            return None

    def _handle_background_upload(self, parsed_path) -> None:
        self.close_connection = True
        if not self._validate_dashboard_headers("application/octet-stream"):
            return
        content_length = self._read_content_length(MAX_BACKGROUND_UPLOAD_BODY)
        if content_length is None:
            return
        query = parse_qsl(parsed_path.query, keep_blank_values=True)
        if len(query) != 1 or query[0][0] != "name":
            self._discard_rejected_body()
            self._error(HTTPStatus.BAD_REQUEST, "query must contain exactly one name")
            return
        try:
            result = self.server.store.upload_background(query[0][1], self.rfile, content_length)
        except InvalidBackground:
            self._error(HTTPStatus.BAD_REQUEST, "invalid background filename")
            return
        except UploadTooLarge:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "uploaded image is too large")
            return
        except InvalidImage as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
            return
        except Exception:
            self.server.logger.exception("POST %s failed", parsed_path.path)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal server error")
            return
        self.close_connection = False
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

    def do_GET(self) -> None:
        if self.path == API_PATH:
            try:
                self._write_json(HTTPStatus.OK, self.server.store.get())
            except Exception as error:
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
        if self.path == SERVICE_STATUS_PATH:
            self._allow("POST")
            return
        if urlsplit(self.path).path in {BACKGROUND_UPLOAD_PATH, BACKGROUND_REMOVE_PATH}:
            self._allow("POST")
            return
        self._error(HTTPStatus.NOT_FOUND, "not found")

    def do_PATCH(self) -> None:
        if self.path == SERVICE_STATUS_PATH:
            self._allow("POST")
            return
        if urlsplit(self.path).path in {BACKGROUND_UPLOAD_PATH, BACKGROUND_REMOVE_PATH}:
            self._allow("POST")
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
        parsed_path = urlsplit(self.path)
        if parsed_path.path == BACKGROUND_UPLOAD_PATH:
            self._handle_background_upload(parsed_path)
            return
        if self.path == BACKGROUND_REMOVE_PATH:
            self._handle_background_remove()
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
        if self.path == API_PATH:
            self._allow("GET, PATCH")
        elif self.path == SERVICE_STATUS_PATH:
            self._allow("POST")
        elif urlsplit(self.path).path in {BACKGROUND_UPLOAD_PATH, BACKGROUND_REMOVE_PATH}:
            self._allow("POST")
        else:
            self._error(HTTPStatus.NOT_FOUND, "not found")


def configure_logging(log_file: Path, include_console: bool = True) -> logging.Logger:
    logger = logging.getLogger(SERVICE_NAME)
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)sZ %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S")
    for path in (log_file, Path(f"{log_file}.1")):
        try:
            if path.stat().st_size > LOG_MAX_BYTES:
                path.unlink()
        except FileNotFoundError:
            pass
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    if include_console:
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        logger.addHandler(console)
    return logger


def serve(config: AppConfig) -> int:
    logger = configure_logging(config.log_file)
    try:
        store = StateStore(config.background_dir, config.state_file)
        server = StateHTTPServer(config, store, logger)
    except Exception as error:
        logger.exception("unable to start service: %s", error)
        return 1

    stopping = threading.Event()

    def request_shutdown(signum: int, _frame: Any) -> None:
        if stopping.is_set():
            return
        stopping.set()
        logger.info("shutdown requested by signal %s", signum)
        threading.Thread(target=server.shutdown, name="signal-shutdown", daemon=True).start()

    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(signum, request_shutdown)
        except (AttributeError, ValueError):
            pass
    logger.info("wallpaper state service listening on %s", config.listen)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        server.probe_client.close()
        logger.info("wallpaper state service stopped")
    return 0


def read_json_url(url: str, timeout: float = 2.0, request: Request | None = None) -> dict[str, Any] | None:
    try:
        with urlopen(request or url, timeout=timeout) as response:
            payload = json.load(response)
    except (OSError, HTTPError, URLError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def health(config: AppConfig, timeout: float = 1.5) -> dict[str, Any] | None:
    payload = read_json_url(f"{config.local_base_url}{HEALTH_PATH}", timeout)
    if payload and payload.get("status") == "ok" and payload.get("service") == SERVICE_NAME:
        return payload
    return None


def port_has_http_listener(config: AppConfig) -> bool:
    try:
        with urlopen(f"{config.local_base_url}{HEALTH_PATH}", timeout=1.0):
            return True
    except HTTPError:
        return True
    except (OSError, URLError):
        return False


def executable_command(*arguments: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, *arguments]
    return [sys.executable, str(Path(__file__).resolve()), *arguments]


def spawn_backend(config: AppConfig) -> subprocess.Popen[Any] | None:
    if health(config):
        return None
    if port_has_http_listener(config):
        raise RuntimeError(f"{config.listen} is occupied by another HTTP service")
    creationflags = 0
    start_new_session = False
    if os.name == "nt":
        creationflags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    else:
        start_new_session = True
    return subprocess.Popen(
        executable_command("serve", "--config", str(config.config_path)),
        cwd=application_directory(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
        start_new_session=start_new_session,
        close_fds=True,
    )


def ensure_backend(config: AppConfig, timeout: float = 15.0) -> dict[str, Any]:
    current = health(config)
    if current:
        return current
    spawn_backend(config)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = health(config)
        if current:
            return current
        time.sleep(0.25)
    raise RuntimeError(f"API did not become healthy within {timeout:g} seconds; inspect {config.log_file}")


def request_stop(config: AppConfig, timeout: float = 10.0) -> bool:
    if not health(config):
        return False
    request = Request(
        f"{config.local_base_url}{CONTROL_PATH}",
        data=b"",
        method="POST",
        headers={"X-Control-Token": config.control_token},
    )
    payload = read_json_url(request.full_url, 2.0, request)
    if not payload or payload.get("status") != "stopping":
        raise RuntimeError("backend rejected the shutdown request")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not health(config, 0.5):
            return True
        time.sleep(0.2)
    raise RuntimeError("backend did not stop within the timeout")


def tail_lines(path: Path, count: int = 12) -> list[str]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            return stream.readlines()[-count:]
    except OSError:
        return []


def read_preferences(config: AppConfig) -> dict[str, Any] | None:
    return read_json_url(f"{config.local_base_url}{API_PATH}", timeout=1.5)


def clear_console() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def monitor(config: AppConfig) -> int:
    failure_count = 0
    recovery_attempted = False
    recovery_message = ""
    force_refresh = True
    try:
        import msvcrt
    except ImportError:
        msvcrt = None

    while True:
        status = health(config)
        if status:
            failure_count = 0
            recovery_attempted = False
            preferences = read_preferences(config)
        else:
            failure_count += 1
            preferences = None
            if failure_count >= 3 and not recovery_attempted:
                recovery_attempted = True
                try:
                    ensure_backend(config)
                    recovery_message = f"{time.strftime('%H:%M:%S')} 后端已自动恢复"
                    status = health(config)
                    preferences = read_preferences(config) if status else None
                except Exception as error:
                    recovery_message = f"{time.strftime('%H:%M:%S')} 自动恢复失败：{error}"

        clear_console()
        print("HomeLab Wallpaper State Monitor")
        print("=" * 60)
        print(f"API 状态      ：{'正常运行' if status else '不可用'}")
        print(f"监听地址      ：{config.listen}")
        print(f"进程 PID      ：{status.get('pid') if status else '-'}")
        print(f"背景目录      ：{config.background_dir}")
        print(f"状态目录      ：{config.state_dir}")
        print(f"探测入口      ：{config.probe_connect_address}")
        print(f"探测域后缀    ：{config.probe_domain_suffix}")
        if preferences:
            print(f"横屏背景      ：{preferences.get('landscape', '-')}")
            print(f"竖屏背景      ：{preferences.get('portrait', '-')}")
            print(f"最后更新时间  ：{preferences.get('updatedAt', '-')}")
        else:
            print("横屏背景      ：-")
            print("竖屏背景      ：-")
            print("最后更新时间  ：-")
        if recovery_message:
            print(f"恢复信息      ：{recovery_message}")
        print("\n最近日志：")
        logs = tail_lines(config.log_file)
        if logs:
            for line in logs:
                print(line.rstrip())
        else:
            print("（暂无日志）")
        print("\n按 R 立即刷新；按 Q 关闭监视器（API 将继续运行）。")

        waited = 0.0
        force_refresh = False
        while waited < MONITOR_REFRESH_SECONDS and not force_refresh:
            if msvcrt and msvcrt.kbhit():
                key = msvcrt.getwch().lower()
                if key == "q":
                    return 0
                if key == "r":
                    force_refresh = True
                    break
            time.sleep(0.1)
            waited += 0.1


def prompt_value(label: str, default: str) -> str:
    value = input(f"{label} [{default}]：").strip()
    return value or default


def prompt_yes_no(label: str, default: bool = True) -> bool:
    suffix = "Y/n" if default else "y/N"
    value = input(f"{label} [{suffix}]：").strip().lower()
    if not value:
        return default
    return value in {"y", "yes", "是"}


def powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def create_startup_shortcut(executable: Path) -> Path:
    if os.name != "nt":
        raise RuntimeError("Startup shortcuts are only supported on Windows")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA is unavailable")
    startup = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    startup.mkdir(parents=True, exist_ok=True)
    shortcut = startup / f"{SERVICE_NAME}.lnk"
    script = (
        "$shell = New-Object -ComObject WScript.Shell\n"
        f"$shortcut = $shell.CreateShortcut({powershell_literal(str(shortcut))})\n"
        f"$shortcut.TargetPath = {powershell_literal(str(executable))}\n"
        f"$shortcut.WorkingDirectory = {powershell_literal(str(executable.parent))}\n"
        "$shortcut.Description = 'HomeLab wallpaper API and monitor'\n"
        "$shortcut.Save()\n"
    )
    encoded = __import__("base64").b64encode(script.encode("utf-16le")).decode("ascii")
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not shortcut.is_file():
        raise RuntimeError(f"unable to create Startup shortcut: {result.stderr.strip()}")
    return shortcut


def run_configuration_wizard(config_path: Path) -> AppConfig:
    app_dir = config_path.resolve().parent
    print("HomeLab Wallpaper State 首次配置")
    print("=" * 60)
    print("配置、状态和日志不会放入公开的 web\\html 目录。\n")

    existing: dict[str, Any] = {}
    if config_path.is_file():
        try:
            existing = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    background_default = str(existing.get("backgroundDir", DEFAULT_BACKGROUND_DIRECTORY))
    origin_default = ",".join(existing.get("allowedOrigins", [DEFAULT_ORIGIN]))
    listen_default = str(existing.get("listen", DEFAULT_LISTEN))
    state_default = str(existing.get("stateDir", DEFAULT_DATA_DIRECTORY))
    probe_connect_default = str(existing.get("probeConnectAddress", DEFAULT_PROBE_CONNECT_ADDRESS))
    probe_domain_default = str(existing.get("probeDomainSuffix", DEFAULT_PROBE_DOMAIN_SUFFIX))
    probe_timeout_default = str(existing.get("probeTimeoutSeconds", DEFAULT_PROBE_TIMEOUT_SECONDS)).removesuffix(".0")

    while True:
        background_text = prompt_value("背景目录", background_default)
        origin_text = prompt_value("允许的首页 Origin（多个值用逗号分隔）", origin_default)
        listen_text = prompt_value("监听地址", listen_default)
        state_text = prompt_value("状态目录", state_default)
        probe_connect_text = prompt_value("Zoraxy 回环探测入口", probe_connect_default)
        probe_domain_text = prompt_value("允许探测的域名后缀", probe_domain_default)
        probe_timeout_text = prompt_value("单目标探测超时秒数（1-5）", probe_timeout_default)
        origins = [item.strip() for item in origin_text.split(",") if item.strip()]
        try:
            raw = {
                "version": CONFIG_VERSION,
                "backgroundDir": background_text,
                "allowedOrigins": origins,
                "listen": listen_text,
                "stateDir": state_text,
                "controlToken": existing.get("controlToken") or secrets.token_urlsafe(32),
                "probeConnectAddress": probe_connect_text,
                "probeDomainSuffix": probe_domain_text,
                "probeTimeoutSeconds": float(probe_timeout_text),
            }
            atomic_write_json(config_path, raw)
            config = load_config(config_path)
            if path_is_within(app_dir, config.background_dir.parent):
                raise ConfigurationError("EXE directory must be outside the public Web root")
            break
        except Exception as error:
            print(f"\n配置无效：{error}")
            print("请重新输入。\n")

    legacy_state = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / SERVICE_NAME / "state.json"
    if not config.state_file.exists() and legacy_state.is_file() and prompt_yes_no(f"发现旧状态 {legacy_state}，是否导入"):
        shutil.copy2(legacy_state, config.state_file)
        StateStore(config.background_dir, config.state_file)
        print(f"已导入状态：{config.state_file}")

    if getattr(sys, "frozen", False) and prompt_yes_no("是否创建当前用户的开机启动快捷方式"):
        shortcut = create_startup_shortcut(Path(sys.executable).resolve())
        print(f"已创建快捷方式：{shortcut}")
    elif not getattr(sys, "frozen", False):
        print("源码运行模式不创建 Startup 快捷方式；打包后的 EXE 会提供该选项。")
    print(f"配置已保存：{config.config_path}\n")
    return config


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HomeLab dashboard state API and monitor")
    subparsers = parser.add_subparsers(dest="command")
    for name in ("serve", "monitor", "stop", "restart"):
        command = subparsers.add_parser(name)
        command.add_argument("--config", type=Path, default=default_config_path())
    configure = subparsers.add_parser("configure")
    configure.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--config", type=Path, default=default_config_path())
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    configure_console_encoding()
    arguments = parse_arguments(list(sys.argv[1:] if argv is None else argv))
    config_path = arguments.config.resolve()
    try:
        if arguments.command == "configure":
            run_configuration_wizard(config_path)
            return 0
        if not config_path.is_file():
            if arguments.command in {None, "monitor"}:
                config = run_configuration_wizard(config_path)
            else:
                raise ConfigurationError(f"configuration file does not exist: {config_path}")
        else:
            config = load_config(config_path)

        if arguments.command == "serve":
            return serve(config)
        if arguments.command == "stop":
            stopped = request_stop(config)
            print("API 已停止。" if stopped else "API 当前未运行。")
            return 0
        if arguments.command == "restart":
            request_stop(config)
            ensure_backend(config)
            print("API 已重新启动。")
            return 0
        if arguments.command == "monitor":
            return monitor(config)
        ensure_backend(config)
        return monitor(config)
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        print(f"{SERVICE_NAME}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
