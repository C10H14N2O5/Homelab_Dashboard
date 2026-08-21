from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import tempfile
import threading
import time
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError
from send2trash import send2trash

from homelab_state.core import (
    DEFAULT_BACKGROUND,
    SERVICE_NAME,
    atomic_write_json,
    file_sha256,
    utc_timestamp,
)


STATE_VERSION = 1
BACKGROUND_FILES_VERSION = 1
MAX_BACKGROUND_UPLOAD_BODY = 50 * 1024 * 1024
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
THUMBNAIL_NAME_HASH_LENGTH = 24
THUMBNAIL_SOURCE_HASH_LENGTH = 32
THUMBNAIL_FILE_PATTERN = re.compile(
    rf"^thumbnail-(?:[0-9a-f]{{{THUMBNAIL_NAME_HASH_LENGTH}}}-"
    rf"[0-9a-f]{{{THUMBNAIL_SOURCE_HASH_LENGTH}}}|[0-9a-f]{{64}}-[0-9a-f]{{64}})\.webp$"
)
REMOVAL_TOKEN_TTL_SECONDS = 60
MAX_REMOVAL_TOKENS = 32
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".avif"}
INVALID_BACKGROUND_NAME_PATTERN = re.compile(r'[\x00-\x1f<>:"/\\|?*]')
MAX_BACKGROUND_NAME_LENGTH = 255
IMAGE_FORMAT_EXTENSIONS = {
    "JPEG": {".jpg", ".jpeg"},
    "PNG": {".png"},
    "WEBP": {".webp"},
    "AVIF": {".avif"},
}


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


class ProtectedBackground(ValueError):
    pass


class InvalidConfirmationToken(ValueError):
    pass


class TrashUnavailable(RuntimeError):
    def __init__(self, confirmation_token: str) -> None:
        super().__init__("trash is unavailable")
        self.confirmation_token = confirmation_token


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
        return hashlib.sha256(background.encode("utf-8")).hexdigest()[:THUMBNAIL_NAME_HASH_LENGTH]

    def _thumbnail_cache_name(self, background: str, source_stat: os.stat_result) -> str:
        name_hash = self._thumbnail_name_hash(background)
        signature = hashlib.sha256(
            f"{source_stat.st_size}\0{source_stat.st_mtime_ns}".encode("utf-8")
        ).hexdigest()[:THUMBNAIL_SOURCE_HASH_LENGTH]
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
        legacy_name_hash = hashlib.sha256(background.encode("utf-8")).hexdigest()
        prefixes = (f"thumbnail-{name_hash}-", f"thumbnail-{legacy_name_hash}-")
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
                entry.name.startswith(prefixes)
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

    def background_names(self) -> tuple[str, ...]:
        """Return a complete, validated first-level background filename snapshot."""
        with self._lock:
            backgrounds, _, scan_complete = self._scan_background_directory()
        if not scan_complete:
            raise BackgroundQuotaExceeded("background directory scan limit exceeded")
        return tuple(backgrounds)

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
                    and file_sha256(path) == expected_sha256
                ):
                    path.unlink()
                    removed = True
            except OSError:
                pass
        if removed:
            with self._thumbnail_lock:
                self._remove_thumbnail_caches_locked(background)
