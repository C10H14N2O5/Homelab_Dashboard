"""Bounded, disposable worker for Notes NAS I/O and image decoding.

The parent never waits for NAS while holding its primary-store lock. Each request
runs in its own process so a stuck SMB call can be stopped before another starts.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import warnings

from homelab_state.core import atomic_write_json, file_sha256


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def safe_path(root: Path, name: str) -> Path:
    candidate = root / name
    def linked(path):
        return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())
    if linked(root) or linked(candidate) or any(linked(parent) for parent in root.parents):
        raise ValueError("symbolic links are not supported in Notes storage")
    resolved = root.resolve()
    result = candidate.resolve()
    if not result.is_relative_to(resolved):
        raise ValueError("invalid storage path")
    current = candidate.parent
    while current != root and current != current.parent:
        if linked(current):
            raise ValueError("symbolic links are not supported in Notes storage")
        current = current.parent
    return candidate


def read_json(path: Path):
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("oversized state file")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream, object_pairs_hook=unique)


def record_hash(path: Path):
    if not path.exists():
        return None
    try:
        return fingerprint(read_json(path))
    except (ValueError, UnicodeError):
        return "raw:" + file_sha256(path)


def ensure_collection(root: Path, collection_id: str, allow_foreign=False):
    root.mkdir(parents=True, exist_ok=True)
    manifest = safe_path(root, "collection.json")
    if manifest.exists():
        existing = read_json(manifest)
        if existing.get("collectionId") != collection_id and not allow_foreign:
            raise ValueError("backup belongs to another collection; inspect and restore it explicitly")
        return existing
    # Never claim an existing unidentified backup without a manual check.
    if any(root.iterdir()):
        if not allow_foreign:
            raise ValueError("unidentified backup directory requires a manual check")
        return {"collectionId": None}
    atomic_write_json(manifest, {"version": 1, "collectionId": collection_id})
    return {"collectionId": collection_id}


def inspect_image(data: bytes):
    from PIL import Image, UnidentifiedImageError
    allowed = {"JPEG": "image/jpeg", "PNG": "image/png", "GIF": "image/gif", "WEBP": "image/webp", "AVIF": "image/avif"}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as picture:
                fmt = picture.format
                if fmt not in allowed:
                    raise ValueError("unsupported image format")
                frames = getattr(picture, "n_frames", 1)
                if frames > 500:
                    raise ValueError("image has too many frames")
                pixels = 0
                for frame in range(frames):
                    picture.seek(frame)
                    count = picture.width * picture.height
                    if count > 40_000_000:
                        raise ValueError("image frame dimensions are too large")
                    pixels += count
                    if pixels > 250_000_000:
                        raise ValueError("image decoded size is too large")
                    picture.load()
                return {"mime": allowed[fmt], "frames": frames, "size": len(data)}
    except (UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning, OSError, SyntaxError) as exc:
        raise ValueError("invalid or damaged image") from exc


def dispatch(request):
    op = request["op"]
    if op == "validate":
        return inspect_image(base64.b64decode(request["data"], validate=True))
    root = Path(request["root"])
    forbidden = request.get("forbiddenRoot")
    if forbidden and root.resolve().is_relative_to(Path(forbidden).resolve()):
        raise ValueError("Notes storage must not be located inside the public Web Root")
    if op == "adopt":
        root.mkdir(parents=True, exist_ok=True)
        manifest = safe_path(root, "collection.json")
        existing = read_json(manifest).get("collectionId") if manifest.exists() else None
        if existing not in (request.get("expectedCollectionId"), request["collectionId"]):
            return {"conflict": True}
        atomic_write_json(manifest, {"version": 1, "collectionId": request["collectionId"]})
        return {"adopted": True}
    if op == "scan":
        # A manual check must never initialize an absent backup.
        if not root.exists():
            return {"collectionId": None, "records": {}}
        manifest = safe_path(root, "collection.json")
        collection = read_json(manifest) if manifest.exists() else {}
        records = {}
        paths = list(safe_path(root, "notes").glob("*.json")) if safe_path(root, "notes").exists() else []
        paths.extend(root / name for name in ("order.json", "settings.json") if (root / name).exists())
        if len(paths) > 10_000 or sum(path.stat().st_size for path in paths) > 64 * 1024 * 1024:
            raise ValueError("backup is too large for one review; inspect the directory manually")
        for path in paths:
            name = path.relative_to(root).as_posix()
            try:
                value = read_json(safe_path(root, name))
                records[name] = {"value": value, "hash": fingerprint(value)}
            except (ValueError, OSError, TypeError):
                records[name] = {"error": "invalid or unreadable backup record", "hash": record_hash(path)}
        return {"collectionId": collection.get("collectionId"), "records": records}
    if op == "image-read":
        path = safe_path(root, "images/" + request["id"])
        if not path.exists():
            return {"missing": True}
        if path.stat().st_size > 20 * 1024 * 1024:
            raise ValueError("oversized image file")
        return {"data": base64.b64encode(path.read_bytes()).decode("ascii")}
    if op == "image-delete":
        # Never delete from a directory that has since been replaced by a
        # different collection, even when a stale cleanup job was persisted.
        ensure_collection(root, request["collectionId"])
        safe_path(root, "images/" + request["id"]).unlink(missing_ok=True)
        return {"deleted": True}
    ensure_collection(root, request["collectionId"], allow_foreign=op == "restore-read")
    if op == "image-write":
        data = base64.b64decode(request["data"], validate=True)
        path = safe_path(root, "images/" + request["id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest() != request["sha256"]:
                raise ValueError("upload id already contains different data")
            return {"written": True}
        handle, temporary = tempfile.mkstemp(prefix=".upload-", dir=path.parent)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return {"written": True}
    path = safe_path(root, request["path"])
    current_hash = record_hash(path)
    if current_hash != request.get("expectedHash"):
        if op == "backup" and current_hash == fingerprint(request["value"]):
            return {"hash": current_hash}
        return {"conflict": True, "hash": current_hash}
    if op == "restore-read":
        return {"value": read_json(path) if path.exists() else None, "hash": current_hash}
    if op == "backup":
        atomic_write_json(path, request["value"])
        return {"hash": fingerprint(request["value"])}
    raise ValueError("unknown worker operation")


def main():
    try:
        request = json.loads(sys.stdin.buffer.read())
        result = {"ok": True, "result": dispatch(request)}
    except Exception as exc:
        result = {"ok": False, "error": str(exc), "kind": type(exc).__name__}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
