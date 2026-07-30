from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import ctypes
import hashlib
import http.client
import io
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
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener, urlopen

from PIL import Image, ImageOps, UnidentifiedImageError
from send2trash import send2trash


API_PATH = "/api/background-preferences"
SERVICE_STATUS_PATH = "/api/service-status"
BACKGROUND_FILES_PATH = "/api/background-files"
BACKGROUND_UPLOAD_PATH = "/api/background-files/upload"
BACKGROUND_REMOVE_PATH = "/api/background-files/remove"
BESTDORI_CHARACTERS_PATH = "/api/bestdori/characters"
BESTDORI_DRAW_PATH = "/api/background-files/draw"
BESTDORI_RECONCILE_PATH = "/api/bestdori/history/reconcile"
BESTDORI_RESET_PATH = "/api/bestdori/history/reset"
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
SERVICE_VERSION = 5
CONFIG_VERSION = 2
STATE_VERSION = 1
BESTDORI_HISTORY_VERSION = 1
BACKGROUND_FILES_VERSION = 1
MAX_PATCH_BODY = 4 * 1024
MAX_SERVICE_STATUS_BODY = 16 * 1024
MAX_BACKGROUND_REMOVE_BODY = 4 * 1024
MAX_BESTDORI_REQUEST_BODY = 4 * 1024
MAX_BACKGROUND_UPLOAD_BODY = 50 * 1024 * 1024
MAX_BESTDORI_CATALOG_BODY = 24 * 1024 * 1024
MAX_BACKGROUND_PIXELS = 100_000_000
MAX_BACKGROUND_FILES = 512
MAX_BACKGROUND_DIRECTORY_ENTRIES = 2048
MAX_BACKGROUND_NAME_COLLISIONS = 16
BACKGROUND_UPLOAD_CHUNK_SIZE = 1024 * 1024
THUMBNAIL_DIRECTORY_NAME = "thumbnails"
THUMBNAIL_WIDTH = 480
THUMBNAIL_HEIGHT = 270
THUMBNAIL_QUALITY = 75
MAX_THUMBNAIL_GENERATION_ATTEMPTS = 16
MAX_THUMBNAIL_CACHE_ENTRIES = 2048
THUMBNAIL_FILE_PATTERN = re.compile(
    r"^thumbnail-(?P<name>[0-9a-f]{64})-(?P<source>[0-9a-f]{64})\.webp$"
)
REMOVAL_TOKEN_TTL_SECONDS = 60
MAX_REMOVAL_TOKENS = 32
HTTP_IDLE_TIMEOUT_SECONDS = 30.0
HTTP_REQUEST_READ_DEADLINE_SECONDS = 300.0
HTTP_ERROR_WRITE_TIMEOUT_SECONDS = 1.0
HTTP_BODY_READ_CHUNK_SIZE = 64 * 1024
HTTP_REJECTED_BODY_DRAIN_TIMEOUT_SECONDS = 0.01
HTTP_REJECTED_BODY_DRAIN_LIMIT = 64 * 1024
HTTP_BUSY_DRAIN_TIMEOUT_SECONDS = 0.05
HTTP_BUSY_DRAIN_LIMIT = 8 * 1024
MAX_HTTP_CONNECTIONS = 16
MAX_PROBE_TARGETS = 32
MAX_PROBE_WORKERS = 8
MAX_PROBE_REDIRECTS = 5
BESTDORI_BASE_URL = "https://bestdori.com"
BESTDORI_CHARACTERS_URL = f"{BESTDORI_BASE_URL}/api/characters/all.2.json"
BESTDORI_CARDS_URL = f"{BESTDORI_BASE_URL}/api/cards/all.5.json"
BESTDORI_TIMEOUT_SECONDS = 30.0
BESTDORI_CACHE_SECONDS = 300.0
BESTDORI_CHARACTER_IDS = frozenset(range(1, 41))
BESTDORI_RARITIES = frozenset({3, 4, 5})
BESTDORI_SERVERS = ("jp", "en", "tw", "cn", "kr")
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


class BackgroundQuotaExceeded(ValueError):
    pass


class RequestBodyReadError(ConnectionError):
    pass


class ProtectedBackground(ValueError):
    pass


class InvalidConfirmationToken(ValueError):
    pass


class TrashUnavailable(RuntimeError):
    def __init__(self, confirmation_token: str) -> None:
        super().__init__("trash is unavailable")
        self.confirmation_token = confirmation_token


class BestdoriUnavailable(RuntimeError):
    pass


class BestdoriAssetMissing(BestdoriUnavailable):
    pass


class BestdoriHistoryError(RuntimeError):
    pass


class BestdoriPoolExhausted(RuntimeError):
    pass


class BestdoriDrawBusy(RuntimeError):
    pass


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


def validate_application_location(config: AppConfig) -> None:
    if path_is_within(application_directory(), config.background_dir.parent):
        raise ConfigurationError("application directory must be outside the public Web root")


def _parse_config_value(
    config_path: Path,
    raw: Any,
    *,
    create_state_directory: bool,
) -> AppConfig:
    config_path = config_path.resolve()
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
    if create_state_directory:
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


def load_config(path: Path) -> AppConfig:
    config_path = path.resolve()
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigurationError(f"configuration file does not exist: {config_path}") from None
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigurationError(f"unable to read configuration: {error}") from error
    return _parse_config_value(config_path, raw, create_state_directory=True)


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
        self._thumbnail_lock = threading.Lock()
        self.background_dir = background_dir.resolve()
        self.state_file = state_file.resolve()
        self.clock = clock
        self.monotonic = monotonic
        self._removal_tokens: dict[str, tuple[str, float]] = {}
        self.state = self._load_or_initialize()

    @property
    def thumbnail_dir(self) -> Path:
        return self.background_dir / THUMBNAIL_DIRECTORY_NAME

    @staticmethod
    def _thumbnail_name_hash(background: str) -> str:
        return hashlib.sha256(background.encode("utf-8")).hexdigest()

    def _thumbnail_cache_name(self, background: str, source_stat: os.stat_result) -> str:
        name_hash = self._thumbnail_name_hash(background)
        signature = hashlib.sha256(
            f"{background}\0{source_stat.st_size}\0{source_stat.st_mtime_ns}".encode("utf-8")
        ).hexdigest()
        return f"thumbnail-{name_hash}-{signature}.webp"

    @staticmethod
    def _is_link_or_junction(path: Path) -> bool:
        is_junction = getattr(path, "is_junction", None)
        return path.is_symlink() or bool(is_junction and is_junction())

    def _thumbnail_directory_locked(self, *, create: bool) -> Path | None:
        directory = self.thumbnail_dir
        if self._is_link_or_junction(directory):
            raise OSError("thumbnail directory must not be a symbolic link or junction")
        if not directory.exists():
            if not create:
                return None
            directory.mkdir(mode=0o755)
        if self._is_link_or_junction(directory) or not directory.is_dir():
            raise OSError("thumbnail cache path is not a directory")
        if directory.resolve(strict=True).parent != self.background_dir:
            raise OSError("thumbnail directory escapes the background directory")
        return directory

    @staticmethod
    def _validate_thumbnail(path: Path) -> None:
        if path.is_symlink() or not path.is_file():
            raise OSError("thumbnail cache entry is not a regular file")
        try:
            with Image.open(path) as image:
                if image.format != "WEBP" or image.size != (THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT):
                    raise InvalidImage("thumbnail cache entry has invalid properties")
                image.verify()
            with Image.open(path) as image:
                image.load()
        except InvalidImage:
            raise
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
            raise InvalidImage("thumbnail cache entry is damaged") from None

    @staticmethod
    def _write_thumbnail(source: Path, destination: Path) -> None:
        handle, temporary_name = tempfile.mkstemp(
            prefix=".thumbnail-",
            suffix=".tmp",
            dir=destination.parent,
        )
        temporary = Path(temporary_name)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", Image.DecompressionBombWarning)
                with Image.open(source) as image:
                    if (
                        image.width <= 0
                        or image.height <= 0
                        or image.width * image.height > MAX_BACKGROUND_PIXELS
                    ):
                        raise InvalidImage("image dimensions are too large")
                    transposed = ImageOps.exif_transpose(image)
                    try:
                        rgb = transposed.convert("RGB")
                        try:
                            thumbnail = ImageOps.fit(
                                rgb,
                                (THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT),
                                method=Image.Resampling.LANCZOS,
                                centering=(0.5, 0.5),
                            )
                            try:
                                with os.fdopen(handle, "wb") as output:
                                    handle = -1
                                    thumbnail.save(
                                        output,
                                        format="WEBP",
                                        quality=THUMBNAIL_QUALITY,
                                        method=6,
                                    )
                                    output.flush()
                                    os.fsync(output.fileno())
                            finally:
                                thumbnail.close()
                        finally:
                            rgb.close()
                    finally:
                        if transposed is not image:
                            transposed.close()
            os.replace(temporary, destination)
        except (Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise InvalidImage("image dimensions are too large") from None
        except (UnidentifiedImageError, SyntaxError, ValueError):
            raise InvalidImage("invalid or damaged image") from None
        finally:
            if handle >= 0:
                os.close(handle)
            temporary.unlink(missing_ok=True)

    def _thumbnail_for_background_locked(
        self,
        background: str,
        *,
        allow_generate: bool = True,
    ) -> tuple[str | None, str]:
        source = self.background_dir / background
        if source.is_symlink() or not source.is_file():
            raise BackgroundMissing("background file does not exist")
        source_stat = source.stat()
        directory = self._thumbnail_directory_locked(create=True)
        if directory is None:
            raise OSError("thumbnail directory is unavailable")
        cache = directory / self._thumbnail_cache_name(background, source_stat)
        status = "reused"
        try:
            self._validate_thumbnail(cache)
        except (InvalidImage, OSError):
            if not allow_generate:
                return None, "deferred"
            if cache.exists() and not cache.is_symlink():
                cache.unlink(missing_ok=True)
            self._write_thumbnail(source, cache)
            self._validate_thumbnail(cache)
            status = "generated"
        current_stat = source.stat()
        if (
            current_stat.st_size != source_stat.st_size
            or current_stat.st_mtime_ns != source_stat.st_mtime_ns
        ):
            cache.unlink(missing_ok=True)
            raise OSError("background changed while generating thumbnail")
        cache_stat = cache.stat()
        return (
            f"{THUMBNAIL_DIRECTORY_NAME}/{cache.name}"
            f"?v={cache_stat.st_size}-{cache_stat.st_mtime_ns}",
            status,
        )

    def _remove_thumbnail_caches_locked(self, background: str) -> None:
        try:
            directory = self._thumbnail_directory_locked(create=False)
        except OSError:
            return
        if directory is None:
            return
        name_hash = self._thumbnail_name_hash(background)
        prefix = f"thumbnail-{name_hash}-"
        try:
            entries = os.scandir(directory)
        except OSError:
            return
        with entries:
            bounded_entries = []
            for index, entry in enumerate(entries):
                if index >= MAX_THUMBNAIL_CACHE_ENTRIES:
                    break
                bounded_entries.append(entry)
        for entry in bounded_entries:
            if (
                entry.name.startswith(prefix)
                and THUMBNAIL_FILE_PATTERN.fullmatch(entry.name)
                and entry.is_file(follow_symlinks=False)
            ):
                try:
                    Path(entry.path).unlink()
                except OSError:
                    logging.getLogger(SERVICE_NAME).warning(
                        "unable to remove thumbnail cache %s",
                        entry.path,
                        exc_info=True,
                    )

    def _cleanup_orphaned_thumbnails_locked(self, expected: set[str]) -> None:
        try:
            directory = self._thumbnail_directory_locked(create=False)
        except OSError:
            return
        if directory is None:
            return
        try:
            entries = os.scandir(directory)
        except OSError:
            return
        with entries:
            bounded_entries = []
            for index, entry in enumerate(entries):
                if index >= MAX_THUMBNAIL_CACHE_ENTRIES:
                    return
                bounded_entries.append(entry)
        for entry in bounded_entries:
            if (
                entry.name not in expected
                and THUMBNAIL_FILE_PATTERN.fullmatch(entry.name)
                and entry.is_file(follow_symlinks=False)
            ):
                try:
                    Path(entry.path).unlink()
                except OSError:
                    logging.getLogger(SERVICE_NAME).warning(
                        "unable to remove orphaned thumbnail cache %s",
                        entry.path,
                        exc_info=True,
                    )

    def _scan_background_directory(self) -> tuple[list[str], set[str], bool]:
        backgrounds: list[str] = []
        occupied_names: set[str] = set()
        complete = True
        with os.scandir(self.background_dir) as entries:
            for index, entry in enumerate(entries):
                if index >= MAX_BACKGROUND_DIRECTORY_ENTRIES:
                    complete = False
                    break
                occupied_names.add(entry.name.casefold())
                if not entry.is_file(follow_symlinks=False):
                    continue
                try:
                    backgrounds.append(validate_background_name(entry.name))
                except InvalidBackground:
                    continue
        backgrounds.sort(
            key=lambda name: (
                name.casefold() != DEFAULT_BACKGROUND.casefold(),
                name.casefold(),
                name,
            )
        )
        return backgrounds, occupied_names, complete

    @staticmethod
    def _prioritize_backgrounds(
        backgrounds: list[str],
        preferred: tuple[str, ...],
    ) -> list[str]:
        remaining = list(backgrounds)
        prioritized: list[str] = []
        for preferred_name in preferred:
            preferred_key = preferred_name.casefold()
            for index, background in enumerate(remaining):
                if background.casefold() == preferred_key:
                    prioritized.append(remaining.pop(index))
                    break
        prioritized.extend(remaining)
        return prioritized

    def list_backgrounds(self) -> dict[str, Any]:
        with self._lock:
            preferred = (
                DEFAULT_BACKGROUND,
                self.state["landscape"],
                self.state["portrait"],
            )
        backgrounds, _, scan_complete = self._scan_background_directory()
        total = len(backgrounds) if scan_complete else None
        truncated = not scan_complete or len(backgrounds) > MAX_BACKGROUND_FILES
        backgrounds = self._prioritize_backgrounds(backgrounds, preferred)[:MAX_BACKGROUND_FILES]
        with self._thumbnail_lock:
            result: list[dict[str, str | None]] = []
            statistics = {"generated": 0, "reused": 0, "failed": 0, "deferred": 0}
            expected: set[str] = set()
            generation_attempts = 0
            for background in backgrounds:
                source = self.background_dir / background
                try:
                    cache_name = self._thumbnail_cache_name(background, source.stat())
                    expected.add(cache_name)
                    allow_generate = generation_attempts < MAX_THUMBNAIL_GENERATION_ATTEMPTS
                    thumbnail, status = self._thumbnail_for_background_locked(
                        background,
                        allow_generate=allow_generate,
                    )
                    if status == "generated":
                        generation_attempts += 1
                except BackgroundMissing:
                    continue
                except Exception:
                    if generation_attempts < MAX_THUMBNAIL_GENERATION_ATTEMPTS:
                        generation_attempts += 1
                    logging.getLogger(SERVICE_NAME).warning(
                        "unable to prepare thumbnail for %s",
                        background,
                        exc_info=True,
                    )
                    thumbnail = None
                    status = "failed"
                statistics[status] += 1
                result.append({"name": background, "thumbnail": thumbnail})
            if scan_complete and not truncated:
                self._cleanup_orphaned_thumbnails_locked(expected)
            return {
                "version": BACKGROUND_FILES_VERSION,
                "backgrounds": result,
                "thumbnails": statistics,
                "total": total,
                "truncated": truncated,
            }

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

    def _next_available_background_name(
        self,
        requested_name: str,
        existing: set[str],
    ) -> str:
        requested = Path(requested_name)
        for attempt in range(MAX_BACKGROUND_NAME_COLLISIONS):
            candidate = (
                requested_name
                if attempt == 0
                else f"{requested.stem} ({attempt + 1}){requested.suffix}"
            )
            if candidate.casefold() not in existing:
                return candidate
        raise BackgroundQuotaExceeded("unable to allocate a background filename")

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
            width, height = self._validate_uploaded_image(temporary, Path(requested_name).suffix)
            with self._lock:
                backgrounds, occupied_names, scan_complete = self._scan_background_directory()
                if not scan_complete or len(backgrounds) >= MAX_BACKGROUND_FILES:
                    raise BackgroundQuotaExceeded("background file quota is full")
                for _ in range(MAX_BACKGROUND_NAME_COLLISIONS):
                    final_name = self._next_available_background_name(
                        requested_name,
                        occupied_names,
                    )
                    destination = self.background_dir / final_name
                    try:
                        # A hard-link publish is atomic and, unlike replace(), never overwrites
                        # an external file that wins the destination-name race.
                        os.link(temporary, destination)
                        break
                    except FileExistsError:
                        occupied_names.add(final_name.casefold())
                else:
                    raise BackgroundQuotaExceeded("unable to publish a unique background filename")
            temporary.unlink()
            with self._thumbnail_lock:
                try:
                    self._thumbnail_for_background_locked(final_name)
                except Exception:
                    logging.getLogger(SERVICE_NAME).warning(
                        "unable to prepare thumbnail for uploaded background %s",
                        final_name,
                        exc_info=True,
                    )
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
                disposition = "permanent"
                preferences = self._preferences_after_removal()
            else:
                try:
                    send2trash(str(path))
                except Exception:
                    if not path.exists() and not path.is_symlink():
                        disposition = "trash"
                        preferences = self._preferences_after_removal()
                    else:
                        token = self._issue_removal_token(background, now)
                        raise TrashUnavailable(token) from None
                else:
                    disposition = "trash"
                    preferences = self._preferences_after_removal()

        with self._thumbnail_lock:
            self._remove_thumbnail_caches_locked(background)
        return disposition, preferences

    def rollback_uploaded_background(self, background: str, expected_sha256: str) -> None:
        removed = False
        with self._lock:
            path = self.background_dir / background
            try:
                if (
                    path.is_file()
                    and not path.is_symlink()
                    and BestdoriHistoryStore._file_sha256(path) == expected_sha256
                ):
                    path.unlink()
                    removed = True
            except OSError:
                pass
        if removed:
            with self._thumbnail_lock:
                self._remove_thumbnail_caches_locked(background)


class _RejectBestdoriRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        raise HTTPError(request.full_url, code, "Bestdori redirects are not allowed", headers, file_pointer)


class BestdoriClient:
    def __init__(
        self,
        *,
        timeout: float = BESTDORI_TIMEOUT_SECONDS,
        cache_seconds: float = BESTDORI_CACHE_SECONDS,
        monotonic=time.monotonic,
        opener=None,
    ) -> None:
        self.timeout = timeout
        self.cache_seconds = cache_seconds
        self.monotonic = monotonic
        self.opener = opener or build_opener(
            _RejectBestdoriRedirects(),
            HTTPSHandler(context=ssl.create_default_context()),
        )
        self._cache_lock = threading.Lock()
        self._characters_cache: tuple[float, list[dict[str, Any]]] | None = None
        self._cards_cache: tuple[float, list[dict[str, Any]]] | None = None

    @staticmethod
    def _validate_url(url: str) -> None:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "bestdori.com"
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 443}
            or parsed.fragment
        ):
            raise BestdoriUnavailable("invalid Bestdori URL")

    def _read(self, url: str, maximum_body: int, accepted_types: frozenset[str]) -> bytes:
        self._validate_url(url)
        request = Request(
            url,
            headers={
                "Accept": ", ".join(sorted(accepted_types)),
                "User-Agent": f"{SERVICE_NAME}/{SERVICE_VERSION}",
            },
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                self._validate_url(response.geturl())
                content_type = response.headers.get_content_type().lower()
                if content_type not in accepted_types:
                    raise BestdoriUnavailable("Bestdori returned an unexpected content type")
                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        declared_length = int(content_length)
                    except ValueError:
                        raise BestdoriUnavailable("Bestdori returned an invalid response length") from None
                    if declared_length < 0 or declared_length > maximum_body:
                        raise BestdoriUnavailable("Bestdori response is too large")
                payload = response.read(maximum_body + 1)
        except HTTPError as error:
            if error.code == HTTPStatus.NOT_FOUND:
                raise BestdoriAssetMissing("Bestdori card image is unavailable") from None
            raise BestdoriUnavailable(f"Bestdori request failed with HTTP {error.code}") from None
        except BestdoriUnavailable:
            raise
        except (OSError, URLError, ssl.SSLError, TimeoutError):
            raise BestdoriUnavailable("unable to reach Bestdori") from None
        if len(payload) > maximum_body:
            raise BestdoriUnavailable("Bestdori response is too large")
        return payload

    def _read_json(self, url: str) -> dict[str, Any]:
        payload = self._read(
            url,
            MAX_BESTDORI_CATALOG_BODY,
            frozenset({"application/json", "text/json"}),
        )
        try:
            value = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise BestdoriUnavailable("Bestdori returned invalid JSON") from None
        if not isinstance(value, dict):
            raise BestdoriUnavailable("Bestdori returned an invalid catalog")
        return value

    @staticmethod
    def _localized_text(value: Any, fallback: str) -> str:
        selected = ""
        if isinstance(value, list):
            for index in (3, 2, 0, 1, 4):
                if index < len(value) and isinstance(value[index], str) and value[index].strip():
                    selected = value[index].strip()
                    break
        elif isinstance(value, str) and value.strip():
            selected = value.strip()
        return (selected or fallback)[:160]

    def get_characters(self) -> list[dict[str, Any]]:
        now = self.monotonic()
        with self._cache_lock:
            if self._characters_cache is not None and self._characters_cache[0] > now:
                return [dict(character) for character in self._characters_cache[1]]
        raw = self._read_json(BESTDORI_CHARACTERS_URL)
        characters: list[dict[str, Any]] = []
        for character_id in sorted(BESTDORI_CHARACTER_IDS):
            value = raw.get(str(character_id))
            if not isinstance(value, dict):
                raise BestdoriUnavailable("Bestdori character catalog is incomplete")
            name = self._localized_text(
                value.get("characterName", value.get("name")),
                f"角色 {character_id}",
            )
            characters.append({"id": character_id, "name": name})
        with self._cache_lock:
            self._characters_cache = (now + self.cache_seconds, characters)
        return [dict(character) for character in characters]

    @staticmethod
    def _card_server(released_at: Any) -> str | None:
        if not isinstance(released_at, list):
            return None
        for index, server in enumerate(BESTDORI_SERVERS):
            if index < len(released_at) and released_at[index] is not None:
                return server
        return None

    def get_cards(self) -> list[dict[str, Any]]:
        now = self.monotonic()
        with self._cache_lock:
            if self._cards_cache is not None and self._cards_cache[0] > now:
                return [dict(card) for card in self._cards_cache[1]]
        raw = self._read_json(BESTDORI_CARDS_URL)
        cards: list[dict[str, Any]] = []
        for card_id_text, value in raw.items():
            if not isinstance(card_id_text, str) or not card_id_text.isdigit() or not isinstance(value, dict):
                continue
            card_id = int(card_id_text)
            character_id = value.get("characterId")
            rarity = value.get("rarity")
            resource_set = value.get("resourceSetName")
            server = self._card_server(value.get("releasedAt"))
            if (
                isinstance(character_id, bool)
                or not isinstance(character_id, int)
                or character_id not in BESTDORI_CHARACTER_IDS
                or isinstance(rarity, bool)
                or not isinstance(rarity, int)
                or rarity not in BESTDORI_RARITIES
                or not isinstance(resource_set, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]+", resource_set)
                or server is None
            ):
                continue
            cards.append(
                {
                    "id": card_id,
                    "characterId": character_id,
                    "rarity": rarity,
                    "resourceSetName": resource_set,
                    "title": self._localized_text(value.get("prefix"), f"卡面 {card_id}"),
                    "server": server,
                }
            )
        if not cards:
            raise BestdoriUnavailable("Bestdori card catalog contains no supported cards")
        with self._cache_lock:
            self._cards_cache = (now + self.cache_seconds, cards)
        return [dict(card) for card in cards]

    def _asset_url(self, card: dict[str, Any], art: str) -> str:
        if art not in {"after_training", "normal"}:
            raise BestdoriUnavailable("invalid Bestdori card art type")
        server = card.get("server")
        resource_set = card.get("resourceSetName")
        if server not in BESTDORI_SERVERS or not isinstance(resource_set, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]+", resource_set
        ):
            raise BestdoriUnavailable("invalid Bestdori card metadata")
        return (
            f"{BESTDORI_BASE_URL}/assets/{server}/characters/resourceset/"
            f"{resource_set}_rip/card_{art}.png"
        )

    def download_card(self, card: dict[str, Any]) -> tuple[bytes, str]:
        for art in ("after_training", "normal"):
            try:
                payload = self._read(
                    self._asset_url(card, art),
                    MAX_BACKGROUND_UPLOAD_BODY,
                    frozenset({"image/png"}),
                )
                return payload, art
            except BestdoriAssetMissing:
                continue
        raise BestdoriAssetMissing("Bestdori card image is unavailable")


class BestdoriHistoryStore:
    def __init__(self, path: Path, clock=utc_timestamp) -> None:
        self.path = path.resolve()
        self.clock = clock
        self._lock = threading.Lock()

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {"version": BESTDORI_HISTORY_VERSION, "records": {}}

    @staticmethod
    def _validate_record(card_id_text: str, record: Any) -> dict[str, Any]:
        if not card_id_text.isdigit() or not isinstance(record, dict):
            raise BestdoriHistoryError("Bestdori draw history is invalid")
        if set(record) != {"filename", "sha256", "drawnAt", "card"}:
            raise BestdoriHistoryError("Bestdori draw history is invalid")
        card = record.get("card")
        expected_card_fields = {"id", "characterId", "characterName", "rarity", "title", "art"}
        if (
            not isinstance(record.get("filename"), str)
            or not isinstance(record.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"])
            or not isinstance(record.get("drawnAt"), str)
            or not isinstance(card, dict)
            or set(card) != expected_card_fields
            or isinstance(card.get("id"), bool)
            or not isinstance(card.get("id"), int)
            or str(card["id"]) != card_id_text
            or isinstance(card.get("characterId"), bool)
            or not isinstance(card.get("characterId"), int)
            or card["characterId"] not in BESTDORI_CHARACTER_IDS
            or not isinstance(card.get("characterName"), str)
            or isinstance(card.get("rarity"), bool)
            or not isinstance(card.get("rarity"), int)
            or card["rarity"] not in BESTDORI_RARITIES
            or not isinstance(card.get("title"), str)
            or card.get("art") not in {"after_training", "normal"}
        ):
            raise BestdoriHistoryError("Bestdori draw history is invalid")
        try:
            validate_background_name(record["filename"])
            datetime.fromisoformat(record["drawnAt"].replace("Z", "+00:00"))
        except (InvalidBackground, ValueError):
            raise BestdoriHistoryError("Bestdori draw history is invalid") from None
        return {
            "filename": record["filename"],
            "sha256": record["sha256"],
            "drawnAt": record["drawnAt"],
            "card": dict(card),
        }

    def _read_locked(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return self._empty()
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise BestdoriHistoryError("Bestdori draw history is unavailable") from None
        if not isinstance(value, dict) or set(value) != {"version", "records"}:
            raise BestdoriHistoryError("Bestdori draw history is invalid")
        if value["version"] != BESTDORI_HISTORY_VERSION or not isinstance(value["records"], dict):
            raise BestdoriHistoryError("Bestdori draw history is invalid")
        records = {
            card_id: self._validate_record(card_id, record)
            for card_id, record in value["records"].items()
            if isinstance(card_id, str)
        }
        if len(records) != len(value["records"]):
            raise BestdoriHistoryError("Bestdori draw history is invalid")
        return {"version": BESTDORI_HISTORY_VERSION, "records": records}

    def used_card_ids(self) -> set[int]:
        with self._lock:
            return {int(card_id) for card_id in self._read_locked()["records"]}

    def record(self, filename: str, sha256: str, card: dict[str, Any]) -> None:
        with self._lock:
            value = self._read_locked()
            card_id = str(card["id"])
            if card_id in value["records"]:
                raise BestdoriPoolExhausted("card has already been drawn")
            value["records"][card_id] = {
                "filename": filename,
                "sha256": sha256,
                "drawnAt": self.clock(),
                "card": dict(card),
            }
            try:
                atomic_write_json(self.path, value)
            except OSError:
                raise BestdoriHistoryError("unable to save Bestdori draw history") from None

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(BACKGROUND_UPLOAD_CHUNK_SIZE):
                digest.update(chunk)
        return digest.hexdigest()

    def reconcile(self, background_dir: Path) -> dict[str, int]:
        with self._lock:
            value = self._read_locked()
            removed = 0
            for card_id, record in list(value["records"].items()):
                path = background_dir / record["filename"]
                try:
                    valid = path.is_file() and not path.is_symlink() and self._file_sha256(path) == record["sha256"]
                except OSError:
                    raise BestdoriHistoryError("unable to inspect a recorded background") from None
                if not valid:
                    del value["records"][card_id]
                    removed += 1
            if removed:
                try:
                    atomic_write_json(self.path, value)
                except OSError:
                    raise BestdoriHistoryError("unable to save Bestdori draw history") from None
            return {"removed": removed, "remaining": len(value["records"])}

    def reset(self) -> dict[str, int]:
        with self._lock:
            value = self._read_locked()
            removed = len(value["records"])
            try:
                atomic_write_json(self.path, self._empty())
            except OSError:
                raise BestdoriHistoryError("unable to save Bestdori draw history") from None
            return {"removed": removed, "remaining": 0}


class BestdoriDrawManager:
    def __init__(
        self,
        store: StateStore,
        history_path: Path,
        *,
        client: BestdoriClient | None = None,
        history: BestdoriHistoryStore | None = None,
        choice=secrets.choice,
    ) -> None:
        self.store = store
        self.client = client or BestdoriClient()
        self.history = history or BestdoriHistoryStore(history_path)
        self.choice = choice
        self._operation_lock = threading.Lock()

    def characters(self) -> list[dict[str, Any]]:
        return self.client.get_characters()

    @staticmethod
    def _safe_filename_component(value: str, fallback: str) -> str:
        cleaned = INVALID_BACKGROUND_NAME_PATTERN.sub("_", value)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._")
        return cleaned or fallback

    def _background_name(self, card: dict[str, Any], art: str) -> str:
        rarity_names = {3: "三星", 4: "四星", 5: "五星"}
        character = self._safe_filename_component(card["characterName"], f"角色{card['characterId']}")
        title = self._safe_filename_component(card["title"], "无标题")
        suffix = f"_{card['id']}_{art}.png"
        prefix = f"{character}_{rarity_names[card['rarity']]}_"
        title = title[: max(1, 220 - len(prefix) - len(suffix))]
        return validate_background_name(f"{prefix}{title}{suffix}")

    def draw(
        self,
        mode: str,
        rarities: set[int],
        character_id: int | None = None,
    ) -> dict[str, Any]:
        if (
            mode not in {"character", "random"}
            or not rarities
            or not rarities <= BESTDORI_RARITIES
            or (
                mode == "character"
                and (
                    isinstance(character_id, bool)
                    or not isinstance(character_id, int)
                    or character_id not in BESTDORI_CHARACTER_IDS
                )
            )
            or (mode == "random" and character_id is not None)
        ):
            raise ValueError("invalid Bestdori draw filters")
        if not self._operation_lock.acquire(blocking=False):
            raise BestdoriDrawBusy("another Bestdori operation is in progress")
        try:
            used_ids = self.history.used_card_ids()
            cards = self.client.get_cards()
            try:
                character_names = {
                    character["id"]: character["name"]
                    for character in self.client.get_characters()
                }
            except BestdoriUnavailable:
                # The card catalog already contains the stable character ID.
                # Keep random draws usable when only the display-name catalog is unavailable.
                character_names = {}
            eligible = [
                card
                for card in cards
                if card["id"] not in used_ids
                and card["rarity"] in rarities
                and (mode == "random" or card["characterId"] == character_id)
            ]
            if not eligible:
                raise BestdoriPoolExhausted("no undrawn cards match the selected filters")
            selected = dict(self.choice(eligible))
            selected["characterName"] = character_names.get(
                selected["characterId"],
                f"角色 {selected['characterId']}",
            )
            image, art = self.client.download_card(selected)
            card_metadata = {
                "id": selected["id"],
                "characterId": selected["characterId"],
                "characterName": selected["characterName"],
                "rarity": selected["rarity"],
                "title": selected["title"],
                "art": art,
            }
            filename = self._background_name(card_metadata, art)
            background = self.store.upload_background(filename, io.BytesIO(image), len(image))
            digest = hashlib.sha256(image).hexdigest()
            try:
                self.history.record(background["background"], digest, card_metadata)
            except Exception:
                self.store.rollback_uploaded_background(
                    background["background"],
                    digest,
                )
                raise
            return {"background": background, "card": card_metadata}
        finally:
            self._operation_lock.release()

    def reconcile(self) -> dict[str, int]:
        if not self._operation_lock.acquire(blocking=False):
            raise BestdoriDrawBusy("another Bestdori operation is in progress")
        try:
            return self.history.reconcile(self.store.background_dir)
        finally:
            self._operation_lock.release()

    def reset(self) -> dict[str, int]:
        if not self._operation_lock.acquire(blocking=False):
            raise BestdoriDrawBusy("another Bestdori operation is in progress")
        try:
            return self.history.reset()
        finally:
            self._operation_lock.release()


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
        bestdori_manager: BestdoriDrawManager | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.logger = logger
        self._connection_slots = threading.BoundedSemaphore(MAX_HTTP_CONNECTIONS)
        self.bestdori_manager = bestdori_manager or BestdoriDrawManager(
            store,
            config.state_dir / "BestdoriCardDraws.json",
        )
        owns_probe_client = probe_client is None
        self.probe_client = probe_client or ProbeClient(config)
        try:
            super().__init__((config.listen_host, config.listen_port), StateRequestHandler)
        except Exception:
            if owns_probe_client:
                self.probe_client.close()
            raise

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
        if self.command == "PATCH":
            return self.path == API_PATH
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

    def do_GET(self) -> None:
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
        if urlsplit(self.path).path in {
            BACKGROUND_UPLOAD_PATH,
            BACKGROUND_REMOVE_PATH,
            BESTDORI_DRAW_PATH,
            BESTDORI_RECONCILE_PATH,
            BESTDORI_RESET_PATH,
        }:
            self._allow("POST")
            return
        self._error(HTTPStatus.NOT_FOUND, "not found")

    def do_PATCH(self) -> None:
        if self.path == SERVICE_STATUS_PATH:
            self._allow("POST")
            return
        if urlsplit(self.path).path in {
            BACKGROUND_UPLOAD_PATH,
            BACKGROUND_REMOVE_PATH,
            BESTDORI_DRAW_PATH,
            BESTDORI_RECONCILE_PATH,
            BESTDORI_RESET_PATH,
        }:
            self._allow("POST")
            return
        if self.path == BESTDORI_CHARACTERS_PATH:
            self._allow("GET")
            return
        if self.path == BACKGROUND_FILES_PATH:
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
        parsed_path = urlsplit(self.path)
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
        if self.path == API_PATH:
            self._allow("GET, PATCH")
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
        }:
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
    if (
        payload
        and payload.get("status") == "ok"
        and payload.get("service") == SERVICE_NAME
        and payload.get("version") == SERVICE_VERSION
    ):
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
        raise RuntimeError(f"{config.listen} is occupied by another or incompatible HTTP service")
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


def confirm_monitor_shutdown() -> bool:
    clear_console()
    print("停止 API 并关闭 Monitor")
    print("=" * 60)
    print("此操作将停止 HomeLab Wallpaper State API，并关闭当前 Monitor。")
    print("请输入大写 Y 并按 Enter 确认；输入其它任何内容将取消操作。")
    try:
        return input("\n确认大退：") == "Y"
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def monitor(config: AppConfig) -> int:
    failure_count = 0
    recovery_attempted = False
    runtime_message = ""
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
                    runtime_message = f"{time.strftime('%H:%M:%S')} 后端已自动恢复"
                    status = health(config)
                    preferences = read_preferences(config) if status else None
                except Exception as error:
                    runtime_message = f"{time.strftime('%H:%M:%S')} 自动恢复失败：{error}"

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
        if runtime_message:
            print(f"运行信息      ：{runtime_message}")
        print("\n最近日志：")
        logs = tail_lines(config.log_file)
        if logs:
            for line in logs:
                print(line.rstrip())
        else:
            print("（暂无日志）")
        print("\n按 R 立即刷新；按 Q 关闭监视器（API 将继续运行）；按 X 停止 API 并关闭监视器。")

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
                if key == "x":
                    if not confirm_monitor_shutdown():
                        runtime_message = f"{time.strftime('%H:%M:%S')} 已取消大退，API 继续运行"
                        force_refresh = True
                        break
                    try:
                        stopped = request_stop(config)
                    except Exception as error:
                        runtime_message = f"{time.strftime('%H:%M:%S')} 大退失败：{error}"
                        force_refresh = True
                        break
                    print("\nAPI 已停止，Monitor 已关闭。" if stopped else "\nAPI 当前未运行，Monitor 已关闭。")
                    return 0
            time.sleep(0.1)
            waited += 0.1


def prompt_value(label: str, default: str) -> str:
    value = input(f"{label} [{default}]：").lstrip("\ufeff").strip()
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
            config = _parse_config_value(
                config_path,
                raw,
                create_state_directory=False,
            )
            validate_application_location(config)
            config.state_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_json(config.config_path, raw)
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

        validate_application_location(config)
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
