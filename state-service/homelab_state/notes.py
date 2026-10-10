"""Private Notes state, optimistic writes, managed images and NAS backups."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
import uuid

from homelab_state.core import atomic_write_json, file_sha256, utc_timestamp
from homelab_state.notes_worker import fingerprint, safe_path
from homelab_state.notes_storage import NotesStorage, primary_operation
from homelab_state.notes_paths import StoragePathError, normalized, run_probe


MAX_NOTES = 500
MAX_TITLE = 200
MAX_BODY = 256 * 1024
MAX_TEXT = 32 * 1024 * 1024
MAX_IMAGES = 2000
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGES_BYTES = 5 * 1024 * 1024 * 1024
LEASE_SECONDS = 300
SETTINGS_DEFAULTS = {"autoSaveEnabled": True, "autoSaveSeconds": 60, "saveOnSwitch": True, "collapseOnBlur": False}
IMAGE_PATH = re.compile(r"^/api/notes/images/([0-9a-f-]{36})$")


class NotesError(Exception):
    def __init__(self, status: int, code: str, message: str, current=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.current = current


def identifier(value):
    if not isinstance(value, str):
        raise NotesError(400, "invalid_id", "invalid identifier")
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError()
    except ValueError:
        raise NotesError(400, "invalid_id", "invalid identifier") from None
    return value


def revision():
    return str(uuid.uuid4())


def extract_attachments(body):
    """Use the Markdown parser so inline code and fenced samples are not refs."""
    from markdown_it import MarkdownIt
    parser = MarkdownIt("commonmark", {"html": False})
    result = set()
    def inspect(tokens):
        for token in tokens:
            if token.type in ("image", "link_open"):
                value = token.attrGet("src" if token.type == "image" else "href") or ""
                try:
                    parts = urlsplit(value)
                except ValueError:
                    continue
                match = IMAGE_PATH.fullmatch(parts.path) if not parts.scheme and not parts.netloc else None
                if match and not parts.query:
                    try:
                        result.add(identifier(match.group(1)))
                    except NotesError:
                        pass
            if token.children:
                inspect(token.children)
    inspect(parser.parse(body))
    return sorted(result)


def _read(path):
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("oversized Notes state file")
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON key")
            value[key] = item
        return value
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)


class NotesService(NotesStorage):
    def __init__(self, primary_dir: Path, backup_dir: Path, logger=None, *, start_worker=True, forbidden_roots=(), binding_file=None, config_file=None, state_dir=None):
        self._storage_setup(primary_dir, backup_dir, forbidden_roots, binding_file, config_file, state_dir)
        self.logger = logger
        self._lock = threading.RLock()
        self._worker_lock = threading.Lock()
        self._maintenance = threading.Lock()
        self._slots = threading.BoundedSemaphore(8)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._process = None
        self._primary_available = True
        self._backup_available = None
        self._backup_error = None
        self._retry_index = 0
        self._next_retry = 0.0
        self._jobs = {}
        self._notes = {}
        self._corrupt = {}
        self._disk_signatures = {}
        self._closed = False
        self._lock_file = None
        self._initialize_binding()
        self._acquire_writer()
        try:
            self._switching_load = normalized(self._bindings["primaryTarget"]) != normalized(self.primary_dir)
            self._load()
            self._write_binding(primaryTarget=str(self.primary_dir))
            self._loading = False
            self._switching_load = False
        except Exception:
            self.close()
            raise
        self._thread = None
        if start_worker:
            self._thread = threading.Thread(target=self._background, name="notes-backup", daemon=True)
            self._thread.start()

    def _acquire_writer(self):
        self._lock_file = safe_path(self.primary_dir, ".writer.lock").open("a+b")
        self._lock_file.seek(0)
        self._lock_file.write(b"0")
        self._lock_file.flush()
        self._lock_file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lock_file.close()
            self._lock_file = None
            raise NotesError(503, "store_locked", "another backend already owns this Notes directory") from None

    def _load(self):
        manifest = safe_path(self.primary_dir, "collection.json")
        self._new_collection = not manifest.exists()
        if not manifest.exists():
            existing = [p for p in self.primary_dir.iterdir() if p.name != ".writer.lock"]
            if existing:
                raise NotesError(503, "invalid_store", "existing Notes directory has no collection manifest")
            atomic_write_json(manifest, {"version": 1, "collectionId": self._bindings["collectionId"]})
        collection = _read(manifest)
        if collection.get("version") != 1:
            raise NotesError(503, "invalid_store", "unsupported Notes collection version")
        self.collection_id = identifier(collection["collectionId"])
        directory = safe_path(self.primary_dir, "notes")
        directory.mkdir(exist_ok=True)
        for path in directory.glob("*.json"):
            identifier(path.stem)
            key = "notes/" + path.name
            try:
                note = _read(safe_path(self.primary_dir, key))
                self._validate_backup_record(key, note)
                self._notes[note["id"]] = note
            except (NotesError, ValueError, OSError, KeyError, TypeError) as exc:
                self._mark_corrupt(key, str(exc))
        defaults = {"order.json": {"revision": revision(), "ids": []}, "settings.json": {"revision": revision(), **SETTINGS_DEFAULTS}}
        loaded = {}
        for key, default in defaults.items():
            try:
                value = self._load_or_create(key, default)
                self._validate_backup_record(key, value)
                loaded[key] = value
            except (NotesError, ValueError, OSError, KeyError, TypeError) as exc:
                self._mark_corrupt(key, str(exc))
                loaded[key] = default
        self._order = loaded["order.json"]
        self._settings = loaded["settings.json"]
        self._sync = self._load_or_create("sync.json", {"version": 1, "ack": {}, "pending": {}, "blocked": {}, "uploads": {}})
        for key in ("ack", "pending", "blocked", "uploads"):
            if not isinstance(self._sync.get(key), dict):
                raise NotesError(503, "invalid_store", "invalid sync state")
        if self._sync.get("version") != 1:
            raise NotesError(503, "invalid_store", "unsupported Notes sync version")
        for group in ("ack", "pending", "blocked"):
            for key, value in self._sync[group].items():
                if key not in ("order.json", "settings.json"):
                    if not key.startswith("notes/") or not key.endswith(".json"):
                        raise NotesError(503, "invalid_store", "invalid backup record path")
                    identifier(key[6:-5])
                if not isinstance(value, str) or (group != "blocked" and not re.fullmatch(r"[0-9a-f]{64}", value)):
                    raise NotesError(503, "invalid_store", "invalid backup tracking state")
        for image_id, upload in self._sync["uploads"].items():
            identifier(image_id)
            if not isinstance(upload, dict) or upload.get("status") not in ("uploading", "uploaded", "committed", "failed", "deleting"):
                raise NotesError(503, "invalid_store", "invalid managed image state")
            if type(upload.get("size")) is not int or not 0 < upload["size"] <= MAX_IMAGE_BYTES or not isinstance(upload.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", upload["sha256"]):
                raise NotesError(503, "invalid_store", "invalid managed image metadata")
            if upload.get("mime") not in ("image/jpeg", "image/png", "image/gif", "image/webp", "image/avif") or type(upload.get("frames")) is not int or not 1 <= upload["frames"] <= 500:
                raise NotesError(503, "invalid_store", "invalid managed image format metadata")
            if type(upload.get("lastTouch")) not in (int, float) or not math.isfinite(upload["lastTouch"]):
                raise NotesError(503, "invalid_store", "invalid upload lease")
        for key in set(self._sync["ack"]) | set(self._sync["pending"]):
            if key.startswith("notes/") and not safe_path(self.primary_dir, key).exists():
                self._mark_corrupt(key, "previously saved note record is missing")
        for key, value in self._records().items():
            digest = fingerprint(value)
            if digest != self._sync["ack"].get(key):
                self._sync["pending"][key] = digest
        # Leases survive a service restart; incomplete writes are recoverable by ID.
        for upload in self._sync["uploads"].values():
            if upload.get("status") == "uploading":
                upload["status"] = "failed"
        for image_id in self._references():
            if image_id in self._sync["uploads"]:
                self._sync["uploads"][image_id]["status"] = "committed"
        self._refresh_primary_health()
        if self._switching_load and self._corrupt:
            raise NotesError(503, "primary_target_changed", "新主目录数据不完整或损坏")
        self._save_sync()

    def _load_or_create(self, name, value):
        path = safe_path(self.primary_dir, name)
        if path.exists():
            return _read(path)
        if not self._new_collection:
            raise NotesError(503, "invalid_store", "existing Notes state file is missing: " + name)
        atomic_write_json(path, value)
        return value

    def _validate_record(self, note):
        if not isinstance(note, dict):
            raise NotesError(400, "invalid_note", "invalid note record")
        identifier(note.get("id"))
        identifier(note.get("revision"))
        if note.get("deleted") is True:
            if "body" in note or "title" in note:
                raise NotesError(400, "invalid_note", "deleted records must not retain content")
            if not isinstance(note.get("deletedAt"), str):
                raise NotesError(400, "invalid_note", "deleted record needs a deletion timestamp")
            return
        self._validate_content(note.get("title"), note.get("body"))
        if not all(isinstance(note.get(key), str) for key in ("createdAt", "updatedAt")):
            raise NotesError(400, "invalid_note", "note timestamps are invalid")
        expected_refs = extract_attachments(note["body"])
        if note.get("attachments") != expected_refs:
            raise NotesError(400, "invalid_note", "note attachment references are inconsistent")

    def _validate_content(self, title, body):
        if not isinstance(title, str) or not isinstance(body, str):
            raise NotesError(400, "invalid_note", "title and body must be text")
        if len(title) > MAX_TITLE or len(body.encode("utf-8")) > MAX_BODY:
            raise NotesError(413, "note_limit", "title or note body exceeds the limit")

    def _validate_settings(self, values):
        for key in ("autoSaveEnabled", "saveOnSwitch", "collapseOnBlur"):
            if key in values and type(values[key]) is not bool:
                raise NotesError(400, "invalid_settings", key + " must be a boolean")
        if "autoSaveSeconds" in values and (type(values["autoSaveSeconds"]) is not int or not 10 <= values["autoSaveSeconds"] <= 3600):
            raise NotesError(400, "invalid_settings", "autoSaveSeconds must be an integer between 10 and 3600")

    def _records(self):
        records = {**{"notes/" + key + ".json": value for key, value in self._notes.items()}, "order.json": self._order, "settings.json": self._settings}
        return {key: value for key, value in records.items() if key not in self._corrupt}

    def _disk_hash(self, key):
        path = safe_path(self.primary_dir, key)
        if not path.exists():
            return None
        try:
            return fingerprint(_read(path))
        except (ValueError, UnicodeError):
            return "raw:" + file_sha256(path)

    def _mark_corrupt(self, key, message):
        self._corrupt[key] = {"hash": self._disk_hash(key), "message": message}

    def _refresh_primary_health(self):
        keys = set(self._records()) | set(self._corrupt)
        keys.update("notes/" + path.name for path in safe_path(self.primary_dir, "notes").glob("*.json"))
        for key in keys:
            try:
                path = safe_path(self.primary_dir, key)
                stat = path.stat()
                signature = (stat.st_mtime_ns, stat.st_size)
                if self._disk_signatures.get(key) == signature and key not in self._corrupt:
                    continue
                value = _read(path)
                self._validate_backup_record(key, value)
                if key.startswith("notes/"):
                    self._notes[value["id"]] = value
                elif key == "order.json":
                    self._order = value
                else:
                    self._settings = value
                self._corrupt.pop(key, None)
                self._disk_signatures[key] = signature
            except (NotesError, ValueError, OSError, KeyError, TypeError) as exc:
                self._mark_corrupt(key, str(exc))

    def _ensure_available(self, *, allow_corrupt=False):
        if self._closed:
            raise NotesError(503, "primary_unavailable", "Notes service is closed")
        try:
            if _read(safe_path(self.primary_dir, "collection.json")).get("collectionId") != self.collection_id:
                raise OSError("Notes collection changed")
        except (OSError, ValueError):
            self._primary_available = False
        if not self._primary_available:
            raise NotesError(503, "primary_unavailable", "Notes primary storage is unavailable")
        if self._corrupt and not allow_corrupt:
            raise NotesError(503, "primary_unavailable", "Notes records are damaged; use backup check and restore before editing")

    def _persist(self, path, value):
        if self._closed:
            raise NotesError(503, "primary_unavailable", "Notes service is closed")
        try:
            atomic_write_json(safe_path(self.primary_dir, path), value)
            if path.startswith("notes/") or path in ("order.json", "settings.json"):
                stat = (self.primary_dir / path).stat()
                self._disk_signatures[path] = (stat.st_mtime_ns, stat.st_size)
        except (OSError, ValueError) as exc:
            self._primary_available = False
            raise NotesError(503, "primary_unavailable", "could not save to Notes primary storage") from exc

    def _save_sync(self):
        self._persist("sync.json", self._sync)

    def _changed(self, key, value):
        self._sync["pending"][key] = fingerprint(value)
        self._save_sync()
        self._wake.set()

    def _request(self, payload, allowed):
        if not isinstance(payload, dict) or set(payload) - (set(allowed) | {"expectedRevision", "operationId"}):
            raise NotesError(400, "invalid_request", "unknown or invalid request fields")
        if "expectedRevision" not in payload:
            raise NotesError(400, "invalid_request", "expectedRevision is required")
        identifier(payload.get("operationId"))
        expected = payload["expectedRevision"]
        if expected is not None:
            identifier(expected)

    def _check_revision(self, current, payload):
        operation = current.get("lastOperation") if current else None
        digest = fingerprint(payload)
        if operation and operation.get("id") == payload["operationId"]:
            if operation.get("hash") != digest:
                raise NotesError(409, "operation_conflict", "operation ID has already been used with different content")
            return True
        if (current.get("revision") if current else None) != payload["expectedRevision"]:
            raise NotesError(409, "revision_conflict", "the server copy has changed", self._public(current))
        return False

    def _public(self, value):
        return {key: copy.deepcopy(item) for key, item in value.items() if key != "lastOperation"} if value else None

    def _operation(self, payload):
        return {"id": payload["operationId"], "hash": fingerprint(payload)}

    def _effective_order(self):
        active = {key: value for key, value in self._notes.items() if not value.get("deleted")}
        ordered = [key for key in self._order["ids"] if key in active]
        extras = sorted(set(active) - set(ordered), key=lambda key: active[key]["createdAt"], reverse=True)
        return {"revision": self._order["revision"], "ids": extras + ordered}

    def _note_result(self, value):
        return {"note": self._public(value), "backupPending": "notes/" + value["id"] + ".json" in self._sync["pending"], "order": self._effective_order()}

    @primary_operation
    def list_notes(self):
        with self._lock:
            self._ensure_available()
            active = {key: value for key, value in self._notes.items() if not value.get("deleted")}
            ordered = [key for key in self._order["ids"] if key in active]
            extras = sorted(set(active) - set(ordered), key=lambda key: active[key]["createdAt"], reverse=True)
            notes = []
            for key in extras + ordered:
                value = active[key]
                notes.append({field: value[field] for field in ("id", "title", "revision", "createdAt", "updatedAt")})
                notes[-1]["bodyBytes"] = len(value["body"].encode("utf-8"))
            order = {"revision": self._order["revision"], "ids": extras + ordered}
            return {"collectionId": self.collection_id, "notes": notes, "order": order, "orderRevision": order["revision"], "settings": self._public(self._settings)}

    @primary_operation
    def get_note(self, note_id):
        identifier(note_id)
        with self._lock:
            self._ensure_available()
            note = self._notes.get(note_id)
            if not note:
                raise NotesError(404, "note_missing", "note does not exist")
            if note.get("deleted"):
                raise NotesError(410, "note_deleted", "note has been deleted", self._public(note))
            return {"note": self._public(note)}

    @primary_operation
    def save_note(self, note_id, payload):
        identifier(note_id)
        self._request(payload, {"title", "body"})
        self._validate_content(payload.get("title"), payload.get("body"))
        refs = extract_attachments(payload["body"])
        with self._lock:
            self._ensure_available()
            current = self._notes.get(note_id)
            if self._check_revision(current, payload):
                return self._note_result(current)
            if current and current.get("deleted"):
                raise NotesError(410, "note_deleted", "note has been deleted", self._public(current))
            active = [value for key, value in self._notes.items() if key != note_id and not value.get("deleted")]
            if len(active) >= MAX_NOTES:
                raise NotesError(413, "notes_limit", "maximum number of notes reached")
            total = sum(len((item["title"] + item["body"]).encode("utf-8")) for item in active)
            if total + len((payload["title"] + payload["body"]).encode("utf-8")) > MAX_TEXT:
                raise NotesError(413, "text_limit", "total Notes text limit reached")
            for image_id in refs:
                upload = self._sync["uploads"].get(image_id)
                if upload and upload["status"] in ("deleting", "uploading", "failed"):
                    raise NotesError(409, "image_not_ready", "a managed image is unavailable or is being removed")
            if current and current["title"] == payload["title"] and current["body"] == payload["body"]:
                return self._note_result(current)
            now = utc_timestamp()
            note = {"id": note_id, "revision": revision(), "title": payload["title"], "body": payload["body"], "createdAt": current["createdAt"] if current else now, "updatedAt": now, "attachments": refs, "lastOperation": self._operation(payload)}
            key = "notes/" + note_id + ".json"
            self._persist(key, note)
            self._notes[note_id] = note
            for image_id in refs:
                upload = self._sync["uploads"].get(image_id)
                if upload:
                    upload["status"] = "committed"
            self._changed(key, note)
            self._queue_unused()
            return self._note_result(note)

    @primary_operation
    def delete_note(self, note_id, payload):
        identifier(note_id)
        self._request(payload, set())
        with self._lock:
            self._ensure_available()
            current = self._notes.get(note_id)
            if not current:
                raise NotesError(404, "note_missing", "note does not exist")
            if self._check_revision(current, payload):
                return self._note_result(current)
            if current.get("deleted"):
                raise NotesError(410, "note_deleted", "note has already been deleted")
            note = {"id": note_id, "revision": revision(), "deleted": True, "deletedAt": utc_timestamp(), "lastOperation": self._operation(payload)}
            key = "notes/" + note_id + ".json"
            self._persist(key, note)
            self._notes[note_id] = note
            self._changed(key, note)
            self._queue_unused()
            return self._note_result(note)

    @primary_operation
    def update_order(self, payload):
        self._request(payload, {"ids"})
        values = payload.get("ids")
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values) or len(values) != len(set(values)):
            raise NotesError(400, "invalid_order", "ids must be a unique list")
        for item in values:
            identifier(item)
        with self._lock:
            self._ensure_available()
            if self._check_revision(self._order, payload):
                return {"order": self._public(self._order)}
            active = {key for key, value in self._notes.items() if not value.get("deleted")}
            if set(values) != active:
                raise NotesError(409, "order_conflict", "note list has changed; reload before sorting")
            if values == self._order["ids"]:
                return {"order": self._public(self._order)}
            value = {"revision": revision(), "ids": values, "lastOperation": self._operation(payload)}
            self._persist("order.json", value)
            self._order = value
            self._changed("order.json", value)
            return {"order": self._public(value)}

    @primary_operation
    def get_settings(self):
        with self._lock:
            self._ensure_available()
            return {"settings": self._public(self._settings)}

    @primary_operation
    def update_settings(self, payload):
        self._request(payload, SETTINGS_DEFAULTS)
        self._validate_settings(payload)
        with self._lock:
            self._ensure_available()
            if self._check_revision(self._settings, payload):
                return {"settings": self._public(self._settings)}
            changes = {key: payload.get(key, self._settings[key]) for key in SETTINGS_DEFAULTS}
            if all(self._settings[key] == changes[key] for key in SETTINGS_DEFAULTS):
                return {"settings": self._public(self._settings)}
            value = {"revision": revision(), **changes, "lastOperation": self._operation(payload)}
            self._persist("settings.json", value)
            self._settings = value
            self._changed("settings.json", value)
            return {"settings": self._public(value)}

    def status(self, refresh=False):
        paths = self._path_status(refresh)
        with self._lock:
            try:
                if not self._primary_error and _read(safe_path(self.primary_dir, "collection.json")).get("collectionId") == self.collection_id:
                    self._primary_available = True
                self._ensure_available(allow_corrupt=True)
                self._refresh_primary_health()
            except (NotesError, OSError, ValueError):
                self._primary_available = False
            return {"enabled": True, "collectionId": self.collection_id, "primaryAvailable": self._primary_available and not self._corrupt, "primaryCorrupt": len(self._corrupt), "backupAvailable": self._backup_available, "backupPending": len(self._sync["pending"]), "backupError": self._backup_error, "backupBlocked": len(self._sync["blocked"]), "paths": {"primaryDir": str(self.primary_entry), "backupDir": str(self.backup_dir)}, "limits": {"notes": MAX_NOTES, "titleCharacters": MAX_TITLE, "bodyBytes": MAX_BODY, "totalTextBytes": MAX_TEXT, "imageBytes": MAX_IMAGE_BYTES, "images": MAX_IMAGES, "totalImageBytes": MAX_IMAGES_BYTES}, **paths}

    def _worker(self, op, *, timeout=60, **values):
        if not self._slots.acquire(blocking=False):
            raise NotesError(503, "worker_busy", "Notes storage worker is busy; try again")
        try:
            with self._worker_lock:
                if self._stop.is_set():
                    raise NotesError(503, "worker_stopped", "Notes worker is stopped")
                if self._process is not None:
                    if self._process.poll() is None:
                        raise NotesError(503, "worker_unreaped", "previous Notes worker has not exited; restart may be required")
                    self._process = None
                if op not in ("validate", "scan", "adopt") and not self._bindings["backupTarget"] and not values.get("authorizedTarget"):
                    try:
                        target = run_probe(self._path_request(), self._binding_probe_state)
                    except (StoragePathError, OSError) as exc:
                        code = getattr(exc, "code", "backup_unavailable")
                        raise NotesError(403 if code == "storage_permission" else 503, code, str(exc)) from exc
                    self._write_binding(backupTarget=target["target"])
                request = {**self._path_request(op=op), "collectionId": self.collection_id,
                           "boundTarget": self._bindings["backupTarget"], **values}
                command = [sys.executable, "--notes-worker"] if getattr(sys, "frozen", False) else [sys.executable, "-m", "homelab_state.notes_worker"]
                environment = os.environ.copy()
                environment["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent) + os.pathsep + environment.get("PYTHONPATH", "")
                process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self._process = process
                try:
                    output, _ = process.communicate(json.dumps(request).encode("utf-8"), timeout=timeout)
                except subprocess.TimeoutExpired:
                    process.kill()
                    try:
                        process.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        raise NotesError(503, "worker_unreaped", "Notes worker did not exit after termination; no further worker will be started") from None
                    raise NotesError(503, "worker_timeout", "Notes storage operation timed out") from None
                finally:
                    if process.poll() is not None:
                        self._process = None
                if process.returncode:
                    raise NotesError(503, "worker_failed", "Notes storage worker failed")
                response = json.loads(output)
                if not response.get("ok"):
                    if response.get("kind") == "PermissionError":
                        raise NotesError(403, "storage_permission", "the backend account does not have permission to access the image/backup directory")
                    status = 400 if response.get("kind") == "ValueError" else 503
                    code = response.get("code") or ("invalid_image" if op == "validate" else "backup_unavailable")
                    self._backup_error_code = code if op != "validate" else self._backup_error_code
                    if code == "check_stale":
                        status = 409
                    raise NotesError(status, code, response.get("error", "Notes storage operation failed"))
                if op != "validate":
                    self._backup_error_code = None
                return response["result"]
        finally:
            self._slots.release()

    @primary_operation
    def upload(self, upload_id, name, data):
        identifier(upload_id)
        if not isinstance(name, str) or len(name) > 255 or not isinstance(data, bytes):
            raise NotesError(400, "invalid_upload", "invalid upload")
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise NotesError(413, "image_limit", "image must be at most 20 MiB")
        # Idempotent upload retries must obey the same target gate as new uploads.
        self._worker("availability")
        digest = hashlib.sha256(data).hexdigest()
        with self._lock:
            self._ensure_available()
            existing = self._sync["uploads"].get(upload_id)
            if existing:
                if existing.get("sha256") != digest:
                    raise NotesError(409, "upload_conflict", "upload ID already belongs to another image")
                if existing["status"] in ("uploaded", "committed"):
                    return self._upload_public(upload_id, existing)
                if existing["status"] == "deleting":
                    raise NotesError(409, "upload_removed", "upload is being removed; use a new upload ID")
            uploads = [value for key, value in self._sync["uploads"].items() if key != upload_id]
            if len(uploads) >= MAX_IMAGES or sum(item.get("size", 0) for item in uploads) + len(data) > MAX_IMAGES_BYTES:
                raise NotesError(413, "images_limit", "managed image capacity reached")
        checked = self._worker("validate", timeout=15, data=base64.b64encode(data).decode("ascii"))
        with self._lock:
            self._ensure_available()
            # Recheck after decoding because other pages may have uploaded in
            # the meantime. A retry must never overwrite a committed record.
            latest = self._sync["uploads"].get(upload_id)
            if latest:
                if latest.get("sha256") != digest:
                    raise NotesError(409, "upload_conflict", "upload ID already belongs to another image")
                if latest["status"] in ("uploaded", "committed"):
                    return self._upload_public(upload_id, latest)
                if latest["status"] in ("uploading", "deleting"):
                    raise NotesError(409, "upload_active", "this upload is already being processed")
            uploads = [value for key, value in self._sync["uploads"].items() if key != upload_id]
            if len(uploads) >= MAX_IMAGES or sum(item.get("size", 0) for item in uploads) + len(data) > MAX_IMAGES_BYTES:
                raise NotesError(413, "images_limit", "managed image capacity reached")
            record = {**checked, "sha256": digest, "status": "uploading", "lastTouch": time.time()}
            self._sync["uploads"][upload_id] = record
            self._save_sync()
        try:
            self._worker("image-write", timeout=300, id=upload_id, sha256=digest, data=base64.b64encode(data).decode("ascii"))
        except NotesError:
            with self._lock:
                record["status"] = "failed"
                self._save_sync()
            raise
        with self._lock:
            record["status"] = "uploaded"
            record["lastTouch"] = time.time()
            self._save_sync()
            return self._upload_public(upload_id, record)

    def _upload_public(self, upload_id, record):
        result = {"id": upload_id, "status": "uploaded" if record["status"] == "committed" else record["status"], "mime": record.get("mime"), "size": record.get("size"), "frames": record.get("frames")}
        if record["status"] in ("uploaded", "committed"):
            result["url"] = "/api/notes/images/" + upload_id
        return result

    @primary_operation
    def get_upload(self, upload_id):
        identifier(upload_id)
        with self._lock:
            self._ensure_available()
            record = copy.deepcopy(self._sync["uploads"].get(upload_id))
        if not record:
            return {"id": upload_id, "status": "missing"}
        if record["status"] in ("failed", "uploading"):
            result = self._worker("image-read", id=upload_id)
            if not result.get("missing") and hashlib.sha256(base64.b64decode(result["data"])).hexdigest() == record["sha256"]:
                with self._lock:
                    current = self._sync["uploads"].get(upload_id)
                    if current and current["status"] in ("failed", "uploading"):
                        current["status"] = "uploaded"
                        current["lastTouch"] = time.time()
                        self._save_sync()
                        record = current
        return self._upload_public(upload_id, record)

    @primary_operation
    def touch_upload(self, upload_id):
        identifier(upload_id)
        with self._lock:
            self._ensure_available()
            record = self._sync["uploads"].get(upload_id)
            if not record or record["status"] == "deleting":
                raise NotesError(404, "upload_missing", "upload is unavailable")
            if record["status"] != "committed":
                record["lastTouch"] = time.time()
                self._save_sync()
            return self._upload_public(upload_id, record)

    def _references(self):
        return {ref for item in self._notes.values() if not item.get("deleted") for ref in item["attachments"]}

    def _queue_unused(self):
        references = self._references()
        changed = False
        for image_id, upload in self._sync["uploads"].items():
            if image_id in references:
                continue
            expired = time.time() - upload.get("lastTouch", 0) >= LEASE_SECONDS
            if upload["status"] == "committed" or (upload["status"] in ("uploaded", "failed") and expired):
                upload["status"] = "deleting"
                changed = True
        if changed:
            self._save_sync()
            self._wake.set()

    @primary_operation
    def discard_upload(self, upload_id):
        identifier(upload_id)
        with self._lock:
            self._ensure_available()
            if upload_id in self._references():
                raise NotesError(409, "image_referenced", "image is still referenced by a saved note")
            record = self._sync["uploads"].get(upload_id)
            if record:
                if record["status"] == "uploading":
                    raise NotesError(409, "upload_active", "wait for the active upload before removing it")
                record["status"] = "deleting"
                self._save_sync()
                self._wake.set()
            return {"id": upload_id, "status": "removed"}

    @primary_operation
    def get_image(self, image_id):
        identifier(image_id)
        with self._lock:
            record = copy.deepcopy(self._sync["uploads"].get(image_id))
            if record and record["status"] == "deleting":
                raise NotesError(404, "image_missing", "image has been removed")
        result = self._worker("image-read", id=image_id)
        if result.get("missing"):
            raise NotesError(404, "image_missing", "image is unavailable")
        data = base64.b64decode(result["data"])
        if record:
            if hashlib.sha256(data).hexdigest() != record["sha256"]:
                raise NotesError(503, "image_changed", "managed image content has changed")
            return data, record["mime"]
        checked = self._worker("validate", timeout=15, data=result["data"])
        return data, checked["mime"]

    def retry_backup(self):
        with self._lock:
            self._next_retry = 0
            self._wake.set()
        return {"scheduled": True}

    @primary_operation
    def _backup_once(self):
        with self._maintenance:
            self._worker("availability")
            with self._lock:
                self._ensure_available()
                self._queue_unused()
                pending = [key for key in self._sync["pending"] if key not in self._sync["blocked"]]
            for key in pending:
                with self._lock:
                    value = copy.deepcopy(self._records().get(key))
                    expected = self._sync["ack"].get(key)
                if value is None:
                    continue
                result = self._worker("backup", path=key, value=value, expectedHash=expected)
                with self._lock:
                    if result.get("conflict"):
                        self._sync["blocked"][key] = "backup has changed; check and reconcile before overwriting"
                        self._backup_error = "备份内容存在差异，请检查并恢复备份"
                    else:
                        self._sync["ack"][key] = result["hash"]
                        if self._sync["pending"].get(key) == result["hash"]:
                            self._sync["pending"].pop(key, None)
                    self._save_sync()
            with self._lock:
                removing = [key for key, value in self._sync["uploads"].items() if value["status"] == "deleting"]
                if any(key.startswith("notes/") for key in self._sync["blocked"]):
                    removing = []  # Unreviewed backup notes may still reference these images.
            for image_id in removing:
                with self._lock:
                    if image_id in self._references():
                        self._sync["uploads"][image_id]["status"] = "committed"
                        self._save_sync()
                        continue
                self._worker("image-delete", id=image_id)
                with self._lock:
                    self._sync["uploads"].pop(image_id, None)
                    self._save_sync()
            with self._lock:
                self._backup_available = True
                if not self._sync["blocked"]:
                    self._backup_error = None
                self._retry_index = 0
                self._next_retry = 0

    def _background(self):
        while not self._stop.is_set():
            with self._lock:
                ready = time.monotonic() >= self._next_retry
            if ready:
                try:
                    self._backup_once()
                except Exception as exc:
                    with self._lock:
                        self._backup_available = False
                        self._backup_error = str(exc)
                        self._backup_error_code = getattr(exc, "code", "backup_unavailable")
                        delays = (30, 60, 120, 300)
                        self._next_retry = time.monotonic() + delays[min(self._retry_index, 3)]
                        self._retry_index += 1
                    if self.logger:
                        try:
                            self.logger.warning("Notes backup unavailable: %s: %s", type(exc).__name__, exc)
                        except AttributeError:
                            pass
            self._wake.wait(5)
            self._wake.clear()

    def _start_job(self, target):
        with self._lock:
            if sum(job["status"] == "running" for job in self._jobs.values()) >= 1:
                raise NotesError(409, "job_busy", "a Notes maintenance job is already running")
            job = {"id": revision(), "status": "running"}
            if len(self._jobs) >= 20:
                oldest = next((key for key, value in self._jobs.items() if value["status"] != "running"), None)
                if oldest:
                    del self._jobs[oldest]
            self._jobs[job["id"]] = job
        def run():
            try:
                with self._storage_operation():
                    result = target(job["id"])
                with self._lock:
                    job.update(status="complete", result=result)
            except Exception as exc:
                with self._lock:
                    job.update(status="failed", message=str(exc), error=getattr(exc, "code", "notes_internal_error"))
        threading.Thread(target=run, name="notes-reconcile", daemon=True).start()
        return {"job": {"id": job["id"], "status": "running"}}

    def get_job(self, job_id):
        identifier(job_id)
        with self._lock:
            if job_id not in self._jobs:
                raise NotesError(404, "job_missing", "maintenance job expired; check again")
            return {"job": {key: copy.deepcopy(value) for key, value in self._jobs[job_id].items() if not key.startswith("_")}}

    @primary_operation
    def reconcile_check(self):
        def run(job_id):
            with self._maintenance:
                with self._lock:
                    self._ensure_available(allow_corrupt=True)
                    self._refresh_primary_health()
                    primary = copy.deepcopy(self._records())
                    damaged = copy.deepcopy(self._corrupt)
                    primary_snapshot = fingerprint({key: self._disk_hash(key) for key in set(primary) | set(damaged)})
                scanned = self._worker("scan")
                backup = scanned["records"]
                items, snapshots = [], {}
                for key in sorted(set(primary) | set(backup) | set(damaged)):
                    local = primary.get(key)
                    remote = backup.get(key)
                    local_hash = damaged[key]["hash"] if key in damaged else fingerprint(local) if local else None
                    remote_hash = remote.get("hash") if remote else None
                    if key not in damaged and local_hash is not None and local_hash == remote_hash:
                        continue
                    kind = "note" if key.startswith("notes/") else key.removesuffix(".json")
                    item_id = key[6:-5] if kind == "note" else kind
                    if kind == "note":
                        try:
                            identifier(item_id)
                        except NotesError:
                            continue
                    remote_value = remote.get("value") if remote else None
                    remote_corrupt = bool(remote and remote.get("error"))
                    try:
                        if remote_value is not None:
                            self._validate_backup_record(key, remote_value)
                    except (NotesError, ValueError, KeyError, TypeError):
                        remote_corrupt = True
                    remote_meta = remote_value if isinstance(remote_value, dict) else {}
                    deleted = bool((local or {}).get("deleted") or remote_meta.get("deleted"))
                    item_type = "corrupt" if remote_corrupt or key in damaged else "deleted" if deleted else "only-backup" if not local else "only-primary" if not remote else "different"
                    items.append({"id": item_id, "kind": kind, "type": item_type, "title": (local or remote_meta).get("title", item_id), "primaryRevision": (local or {}).get("revision"), "backupRevision": remote_meta.get("revision"), "primaryDeleted": bool((local or {}).get("deleted")), "backupDeleted": bool(remote_meta.get("deleted")), "primaryCorrupt": key in damaged, "backupCorrupt": remote_corrupt})
                    snapshots[item_id] = {"path": key, "primaryHash": local_hash, "backupHash": remote_hash, "primaryCorrupt": key in damaged, "backupCorrupt": remote_corrupt}
                with self._lock:
                    self._jobs[job_id]["_snapshots"] = snapshots
                    self._jobs[job_id]["_backupCollectionId"] = scanned["collectionId"]
                    self._jobs[job_id]["_target"] = scanned["target"]
                    self._jobs[job_id]["_scanHash"] = fingerprint(scanned["records"])
                    self._jobs[job_id]["_generation"] = self._primary_generation
                    self._jobs[job_id]["_primarySnapshot"] = primary_snapshot
                    self._jobs[job_id]["_rebind"] = bool(self._bindings["backupTarget"] and normalized(scanned["target"]) != normalized(self._bindings["backupTarget"]))
                    for snapshot in snapshots.values():
                        self._sync["blocked"][snapshot["path"]] = "manual review pending"
                    self._save_sync()
                return {"collectionId": self.collection_id, "backupCollectionId": scanned["collectionId"], "items": items, "target": scanned["target"], "requiresRebind": self._jobs[job_id]["_rebind"]}
        return self._start_job(run)

    def _validate_backup_record(self, key, value):
        if key.startswith("notes/"):
            self._validate_record(value)
            if value["id"] != key[6:-5]:
                raise NotesError(400, "invalid_note", "backup record identifier mismatch")
        elif key == "settings.json":
            identifier(value["revision"])
            self._validate_settings({field: value[field] for field in SETTINGS_DEFAULTS})
        elif key == "order.json":
            identifier(value["revision"])
            if not isinstance(value.get("ids"), list) or len(value["ids"]) != len(set(value["ids"])):
                raise NotesError(400, "invalid_order", "invalid backup order")
            for item in value["ids"]:
                identifier(item)
        else:
            raise NotesError(400, "invalid_record", "unknown backup record")

    @primary_operation
    def reconcile_apply(self, payload):
        if not isinstance(payload, dict) or set(payload) != {"checkId", "choices"}:
            raise NotesError(400, "invalid_request", "checkId and choices are required")
        check_id = identifier(payload["checkId"])
        choices = payload["choices"]
        if not isinstance(choices, list) or len(choices) > MAX_NOTES + 2:
            raise NotesError(400, "invalid_request", "select at least one backup action")
        with self._lock:
            checked = self._jobs.get(check_id)
            if not checked or checked["status"] != "complete" or "_snapshots" not in checked:
                raise NotesError(409, "check_expired", "check the backup before applying changes")
            snapshots = copy.deepcopy(checked["_snapshots"])
            backup_collection = checked["_backupCollectionId"]
            checked_target = checked["_target"]
            checked_generation = checked["_generation"]
            rebind = checked["_rebind"]
            scan_hash = checked["_scanHash"]
            primary_snapshot = checked["_primarySnapshot"]
            if not choices and not rebind:
                raise NotesError(400, "invalid_request", "select at least one backup action")
        seen = set()
        for choice in choices:
            if not isinstance(choice, dict) or set(choice) != {"id", "action"} or not isinstance(choice.get("id"), str) or choice.get("action") not in ("use-primary", "use-backup") or choice.get("id") not in snapshots or choice["id"] in seen:
                raise NotesError(400, "invalid_request", "invalid backup action")
            seen.add(choice["id"])
        def run(job_id):
            results = []
            with self._maintenance:
                if checked_generation != self._primary_generation:
                    raise NotesError(409, "check_stale", "主目录已切换，请重新检查备份")
                current = self._worker("scan", authorizedTarget=checked_target)
                if current["collectionId"] != backup_collection:
                    raise NotesError(409, "check_stale", "备份集合已变化，请重新检查")
                if rebind:
                    if fingerprint(current["records"]) != scan_hash:
                        raise NotesError(409, "check_stale", "备份内容已变化，请重新检查")
                    with self._lock:
                        self._refresh_primary_health()
                        current_primary = fingerprint({key: self._disk_hash(key) for key in set(self._records()) | set(self._corrupt)})
                        if current_primary != primary_snapshot:
                            raise NotesError(409, "check_stale", "主目录内容已变化，请重新检查")
                if rebind or backup_collection != self.collection_id:
                    # Applying an explicit reviewed merge authorizes the new
                    # collection identity. Unselected differences stay blocked.
                    adopted = self._worker("adopt", expectedCollectionId=backup_collection, authorizedTarget=checked_target)
                    if adopted.get("conflict"):
                        raise NotesError(409, "check_stale", "backup collection changed; check again")
                for choice in choices:
                    item_id = choice["id"]
                    snapshot = snapshots[item_id]
                    key = snapshot["path"]
                    try:
                        if snapshot["primaryCorrupt"] and choice["action"] == "use-primary" or snapshot["backupCorrupt"] and choice["action"] == "use-backup":
                            raise NotesError(409, "source_corrupt", "a damaged record cannot be used as the recovery source")
                        with self._lock:
                            self._ensure_available(allow_corrupt=True)
                            local = copy.deepcopy(self._records().get(key))
                            local_hash = self._disk_hash(key)
                            if local_hash != snapshot["primaryHash"]:
                                raise NotesError(409, "check_stale", "primary content changed; check again")
                        if choice["action"] == "use-primary":
                            if local is None:
                                raise NotesError(400, "missing_source", "there is no primary record to copy")
                            result = self._worker("backup", path=key, value=local, expectedHash=snapshot["backupHash"], authorizedTarget=checked_target)
                            if result.get("conflict"):
                                raise NotesError(409, "check_stale", "backup content changed; check again")
                            with self._lock:
                                self._sync["ack"][key] = result["hash"]
                                if self._sync["pending"].get(key) == result["hash"]:
                                    self._sync["pending"].pop(key, None)
                                self._sync["blocked"].pop(key, None)
                                self._save_sync()
                        else:
                            result = self._worker("restore-read", path=key, expectedHash=snapshot["backupHash"], authorizedTarget=checked_target)
                            if result.get("conflict"):
                                raise NotesError(409, "check_stale", "backup content changed; check again")
                            value = result.get("value")
                            if value is None:
                                raise NotesError(400, "missing_source", "there is no backup record to restore")
                            self._validate_backup_record(key, value)
                            images = self._recover_images(value, checked_target) if key.startswith("notes/") and not value.get("deleted") else {}
                            with self._lock:
                                if self._disk_hash(key) != snapshot["primaryHash"]:
                                    raise NotesError(409, "check_stale", "primary content changed; check again")
                                uploads = self._sync["uploads"]
                                new_images = {image_id: item for image_id, item in images.items() if image_id not in uploads}
                                if len(uploads) + len(new_images) > MAX_IMAGES or sum(item.get("size", 0) for item in uploads.values()) + sum(item["size"] for item in new_images.values()) > MAX_IMAGES_BYTES:
                                    raise NotesError(413, "images_limit", "restored images exceed managed image capacity")
                                self._restore_record(key, value)
                                uploads.update(new_images)
                                self._sync["ack"][key] = result["hash"]
                                self._sync["blocked"].pop(key, None)
                                self._changed(key, self._records()[key])
                                self._queue_unused()
                        results.append({"id": item_id, "status": "applied"})
                    except NotesError as exc:
                        results.append({"id": item_id, "status": "conflict" if exc.status == 409 else "failed", "message": str(exc)})
                if rebind and any(item["status"] != "applied" for item in results):
                    raise NotesError(503, "rebind_failed", "重新绑定未完成，目标写入仍暂停；请重新检查备份")
                # Re-resolve before committing the binding, even for an empty selection.
                self._worker("scan", authorizedTarget=checked_target)
                self._write_binding(backupTarget=checked_target)
                self._backup_error_code = None
                self._backup_error = None
            self._wake.set()
            return {"items": results, "adoptedCollection": backup_collection != self.collection_id, "reboundTarget": checked_target if rebind else None}
        return self._start_job(run)

    def _recover_images(self, value, target=None):
        recovered = {}
        with self._lock:
            unknown = [image_id for image_id in value["attachments"] if image_id not in self._sync["uploads"]]
        for image_id in unknown:
            result = self._worker("image-read", id=image_id, **({"authorizedTarget": target} if target else {}))
            if result.get("missing"):
                continue
            try:
                checked = self._worker("validate", timeout=15, data=result["data"])
            except NotesError as exc:
                if exc.code == "invalid_image":
                    continue
                raise
            data = base64.b64decode(result["data"])
            recovered[image_id] = {**checked, "sha256": hashlib.sha256(data).hexdigest(), "status": "committed", "lastTouch": time.time()}
        return recovered

    def _restore_record(self, key, source):
        value = copy.deepcopy(source)
        value.pop("lastOperation", None)
        value["revision"] = revision()
        if key.startswith("notes/"):
            active = [item for item in self._notes.values() if item["id"] != value["id"] and not item.get("deleted")]
            if not value.get("deleted"):
                if len(active) >= MAX_NOTES or sum(len((item["title"] + item["body"]).encode("utf-8")) for item in active) + len((value["title"] + value["body"]).encode("utf-8")) > MAX_TEXT:
                    raise NotesError(413, "notes_limit", "restoring this note exceeds storage limits")
                for image_id in value["attachments"]:
                    if self._sync["uploads"].get(image_id, {}).get("status") == "deleting":
                        # Restoration keeps missing references; it must not revive deletion work.
                        continue
                value["updatedAt"] = utc_timestamp()
            self._persist(key, value)
            self._notes[value["id"]] = value
        elif key == "order.json":
            self._persist(key, value)
            self._order = value
        else:
            self._persist(key, value)
            self._settings = value
        self._corrupt.pop(key, None)

    def close(self):
        self._closed = True
        self._stop.set()
        self._wake.set()
        process = self._process
        if process is not None:
            try:
                process.kill()
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass
        thread = getattr(self, "_thread", None)
        if thread and thread.is_alive():
            thread.join(timeout=6)
        self._release_writer()
        for state in (self._primary_probe_state, self._status_probe_state, self._binding_probe_state):
            process = state.get("process")
            if process is not None:
                try:
                    process.kill()
                    process.wait(timeout=1)
                except (OSError, subprocess.TimeoutExpired):
                    pass
