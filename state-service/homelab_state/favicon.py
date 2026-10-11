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
from homelab_state.icon_images import decode_base64, encode, process_image, MAX_ANIMATION, MAX_STATIC


FAVICON_VERSION = 1
FAVICON_STATE_FILE_NAME = "DashboardFavicon.json"
PAGE_ICON_STATE_FILE_NAME = "DashboardPageIcon.json"
MAX_FAVICON_REQUEST_BODY = 8 * 1024 * 1024
MAX_FAVICON_STATE_FILE_BYTES = 16 * 1024 * 1024
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
        self.allow_animation = self.state_file.name == PAGE_ICON_STATE_FILE_NAME
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

    def _validate_persisted_state(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict) and type(value.get("version")) is int and value["version"] == 2:
            if set(value) != {"version", "updatedAt", "icon"}:
                raise InvalidFavicon("invalid icon state fields")
            icon = value["icon"]
            if icon is not None:
                self._validate_icon(icon)
            return {"version": 2, "updatedAt": _validate_timestamp(value["updatedAt"]), "icon": icon}
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

    def get(self, modern=False) -> dict[str, Any]:
        with self._lock:
            self._require_available()
            if modern or (self._state and self._state["version"] == 2):
                return self._modern_metadata(self._icon(), self._state["updatedAt"] if self._state else None)
            return self._metadata(self._state)

    def _icon(self):
        if self._state is None:
            return None
        if self._state["version"] == 2:
            return self._state["icon"]
        svg = self._state["iconSvg"]
        return self._svg_icon(svg) if svg is not None else None

    @staticmethod
    def _svg_icon(svg):
        return {"mimeType": "image/svg+xml", "contentBase64": encode(svg.encode("utf-8")),
                "posterBase64": None, "width": None, "height": None, "animated": False, "displayBounds": None}

    @staticmethod
    def _modern_metadata(icon, timestamp):
        return {"version": 2, "configured": icon is not None, "updatedAt": timestamp,
                "contentHash": hashlib.sha256(decode_base64(icon["contentBase64"], MAX_ANIMATION)).hexdigest() if icon else None,
                "mimeType": icon["mimeType"] if icon else None, "width": icon["width"] if icon else None,
                "height": icon["height"] if icon else None, "animated": icon["animated"] if icon else False,
                "posterHash": hashlib.sha256(decode_base64(icon["posterBase64"])).hexdigest() if icon and icon["posterBase64"] else None,
                "displayBounds": icon["displayBounds"] if icon else None}

    def prepare(self, request):
        if not isinstance(request, dict) or set(request) != {"version", "fileName", "contentBase64"} or type(request["version"]) is not int or request["version"] != 2:
            raise InvalidFavicon("request must contain version 2, fileName and contentBase64")
        name = request["fileName"]
        if not isinstance(name, str) or not 0 < len(name) <= 255 or "\x00" in name:
            raise InvalidFavicon("invalid icon file name")
        extension = Path(name).suffix.lower()
        if extension not in {".svg", ".png", ".apng", ".jpg", ".jpeg", ".webp", ".gif", ".ico"}:
            raise InvalidFavicon("不支持的图标格式")
        try:
            data = decode_base64(request["contentBase64"], 64 * 1024 if extension == ".svg" else 5 * 1024 * 1024)
            if extension == ".svg":
                icon_svg = data.decode("utf-8-sig")
                icon = self._svg_icon(sanitize_svg(icon_svg, FAVICON_SANITIZER_SCOPE))
            else:
                icon = process_image(data, self.allow_animation)
        except (ValueError, InvalidCards) as exc:
            raise InvalidFavicon(str(exc)) from exc
        return {"icon": icon, "metadata": self._modern_metadata(icon, None)}

    def _validate_icon(self, icon):
        if not isinstance(icon, dict) or set(icon) != {"mimeType", "contentBase64", "posterBase64", "width", "height", "animated", "displayBounds"}:
            raise InvalidFavicon("invalid icon record")
        data = decode_base64(icon["contentBase64"], MAX_ANIMATION)
        if icon["mimeType"] == "image/svg+xml":
            clean = self._svg_icon(sanitize_svg(data.decode("utf-8"), FAVICON_SANITIZER_SCOPE))
            if clean != icon:
                raise InvalidFavicon("invalid SVG icon record")
            return
        if icon["mimeType"] not in {"image/png", "image/webp"}:
            raise InvalidFavicon("invalid icon MIME type")
        if icon["mimeType"] == "image/png" and len(data) > MAX_STATIC:
            raise InvalidFavicon("stored PNG exceeds size limit")
        checked = process_image(data, self.allow_animation, limit=MAX_ANIMATION, canonical=True)
        for field in ("mimeType", "width", "height", "animated", "displayBounds"):
            if type(checked[field]) is not type(icon[field]) or checked[field] != icon[field]:
                raise InvalidFavicon("invalid icon metadata")
        if icon["posterBase64"] != checked["posterBase64"]:
            raise InvalidFavicon("invalid animation poster")

    def update(self, request: Any) -> dict[str, Any]:
        if isinstance(request, dict) and type(request.get("version")) is int and request["version"] == 2:
            if set(request) != {"version", "icon"}:
                raise InvalidFavicon("request must contain version 2 and icon")
            # Decode outside the store lock; only the final atomic write holds it.
            icon = None
            if request["icon"] is not None:
                if not isinstance(request["icon"], dict):
                    raise InvalidFavicon("invalid icon upload")
                icon = self.prepare({"version": 2, **request["icon"]})["icon"]
            with self._lock:
                self._require_available()
                timestamp = _validate_timestamp(self.clock())
                state = {"version": 2, "updatedAt": timestamp, "icon": icon}
                atomic_write_json(self.state_file, state)
                self._state = state
                return self._modern_metadata(icon, timestamp)
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
        data, _, content_hash = self.get_image(expected_hash, svg_only=True)
        return data.decode("utf-8"), content_hash

    def get_image(self, expected_hash, *, poster=False, svg_only=False):
        with self._lock:
            self._require_available()
            icon = self._icon()
            if icon is None or (svg_only and icon["mimeType"] != "image/svg+xml"):
                raise FaviconNotFound("icon is not configured")
            content = icon["posterBase64"] if poster else icon["contentBase64"]
            if content is None:
                raise FaviconNotFound("poster is not configured")
            data = decode_base64(content, MAX_ANIMATION)
            content_hash = hashlib.sha256(data).hexdigest()
            if expected_hash != content_hash:
                raise FaviconNotFound("icon content hash does not match")
            return data, "image/png" if poster else icon["mimeType"], content_hash
