from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from homelab_state.cards import InvalidCards, sanitize_svg
from homelab_state.core import atomic_write_json, utc_timestamp


FAVICON_VERSION = 1
FAVICON_STATE_FILE_NAME = "DashboardFavicon.json"
MAX_FAVICON_REQUEST_BODY = 128 * 1024
MAX_FAVICON_STATE_FILE_BYTES = 64 * 1024
FAVICON_SANITIZER_SCOPE = "site-favicon"


class InvalidFavicon(ValueError):
    pass


class FaviconNotFound(FileNotFoundError):
    pass


class FaviconStateUnavailable(RuntimeError):
    pass


def _strict_json_loads(value: str) -> Any:
    def reject_duplicate_fields(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON field: {key}")
            result[key] = item
        return result

    return json.loads(value, object_pairs_hook=reject_duplicate_fields)


def _validate_timestamp(value: Any) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise InvalidFavicon("updatedAt must be a UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise InvalidFavicon("updatedAt must be a UTC timestamp") from None
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise InvalidFavicon("updatedAt must be a UTC timestamp")
    return value


def _content_hash(icon_svg: str) -> str:
    return hashlib.sha256(icon_svg.encode("utf-8")).hexdigest()


class FaviconStore:
    def __init__(self, state_file: Path, clock=utc_timestamp) -> None:
        self.state_file = Path(os.path.abspath(os.fspath(state_file)))
        self.clock = clock
        self._lock = threading.Lock()
        self._state: dict[str, Any] | None = None
        self._load_error: Exception | None = None
        try:
            self._state = self._read_state_file()
        except FileNotFoundError:
            pass
        except Exception as error:
            self._load_error = error

    @classmethod
    def for_state_directory(cls, state_dir: Path, clock=utc_timestamp) -> "FaviconStore":
        return cls(state_dir / FAVICON_STATE_FILE_NAME, clock)

    @staticmethod
    def _validate_persisted_state(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) != {"version", "updatedAt", "iconSvg"}:
            raise InvalidFavicon("favicon state contains missing or unknown fields")
        if value["version"] != FAVICON_VERSION or isinstance(value["version"], bool):
            raise InvalidFavicon("unsupported favicon state version")
        icon_svg = value["iconSvg"]
        if icon_svg is not None:
            try:
                icon_svg = sanitize_svg(icon_svg, FAVICON_SANITIZER_SCOPE)
            except InvalidCards as error:
                raise InvalidFavicon(str(error)) from error
        return {
            "version": FAVICON_VERSION,
            "updatedAt": _validate_timestamp(value["updatedAt"]),
            "iconSvg": icon_svg,
        }

    def _read_state_file(self) -> dict[str, Any]:
        try:
            file_stat = os.lstat(self.state_file)
        except FileNotFoundError:
            raise
        if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
            raise FaviconStateUnavailable("favicon state path is not a regular file")
        if file_stat.st_size > MAX_FAVICON_STATE_FILE_BYTES:
            raise FaviconStateUnavailable("favicon state file is too large")
        try:
            raw = _strict_json_loads(self.state_file.read_text(encoding="utf-8"))
            return self._validate_persisted_state(raw)
        except FaviconStateUnavailable:
            raise
        except Exception as error:
            raise FaviconStateUnavailable("favicon state file is damaged or incompatible") from error

    def _require_available(self) -> None:
        if self._load_error is not None:
            raise FaviconStateUnavailable(
                "favicon state file is damaged or incompatible"
            ) from self._load_error

    @staticmethod
    def _metadata(state: dict[str, Any] | None) -> dict[str, Any]:
        if state is None:
            return {
                "version": FAVICON_VERSION,
                "configured": False,
                "updatedAt": None,
                "contentHash": None,
            }
        icon_svg = state["iconSvg"]
        return {
            "version": FAVICON_VERSION,
            "configured": icon_svg is not None,
            "updatedAt": state["updatedAt"],
            "contentHash": _content_hash(icon_svg) if icon_svg is not None else None,
        }

    def get(self) -> dict[str, Any]:
        with self._lock:
            self._require_available()
            return self._metadata(self._state)

    def update(self, request: Any) -> dict[str, Any]:
        with self._lock:
            self._require_available()
            if (
                not isinstance(request, dict)
                or set(request) != {"version", "iconSvg"}
                or request.get("version") != FAVICON_VERSION
                or isinstance(request.get("version"), bool)
            ):
                raise InvalidFavicon("request must contain version 1 and iconSvg")
            icon_svg = request["iconSvg"]
            if icon_svg is not None:
                try:
                    icon_svg = sanitize_svg(icon_svg, FAVICON_SANITIZER_SCOPE)
                except InvalidCards as error:
                    raise InvalidFavicon(str(error)) from error
            timestamp = self.clock()
            _validate_timestamp(timestamp)
            state = {
                "version": FAVICON_VERSION,
                "updatedAt": timestamp,
                "iconSvg": icon_svg,
            }
            atomic_write_json(self.state_file, state)
            self._state = state
            return self._metadata(state)

    def get_asset(self, expected_hash: str) -> tuple[str, str]:
        with self._lock:
            self._require_available()
            if self._state is None or self._state["iconSvg"] is None:
                raise FaviconNotFound("favicon is not configured")
            icon_svg = self._state["iconSvg"]
            content_hash = _content_hash(icon_svg)
            if expected_hash != content_hash:
                raise FaviconNotFound("favicon content hash does not match")
            return icon_svg, content_hash
