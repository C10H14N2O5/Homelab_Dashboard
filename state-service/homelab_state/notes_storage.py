"""Entry binding and live primary handoff for NotesService."""
from __future__ import annotations

from contextlib import contextmanager
from functools import wraps
import hashlib
import json
from pathlib import Path
import threading
import time

from homelab_state.core import atomic_write_json
from homelab_state.notes_paths import StoragePathError, absolute_entry, normalized, run_probe, validate_target
from homelab_state.notes_worker import safe_path, read_json


def primary_operation(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._storage_operation():
            return method(self, *args, **kwargs)
    return call


class NotesStorage:
    def _storage_error(self, code, message, status=503):
        from homelab_state.notes import NotesError
        return NotesError(status, code, message)

    def _storage_setup(self, primary, backup, forbidden_roots, binding_file, config_file, state_dir):
        self.primary_entry = absolute_entry(primary)
        self.backup_dir = absolute_entry(backup)
        self.forbidden_roots = [str(root) for root in forbidden_roots]
        self.config_file = str(config_file) if config_file else None
        self.state_dir = str(state_dir) if state_dir else None
        self._transition_lock = threading.RLock()
        self._binding_lock = threading.RLock()
        self._active_condition = threading.Condition()
        self._active_operations = 0
        self._operation_local = threading.local()
        self._primary_probe_state = {}
        self._status_probe_state = {}
        self._binding_probe_state = {}
        self._status_probe_lock = threading.Lock()
        self._probe_done = threading.Event()
        self._probe_checked = 0.0
        self._probe_cache = {}
        self._primary_error = None
        self._backup_error_code = None
        self._primary_generation = 0
        self._switching_load = False
        self._loading = True
        self._bindings = {"version": 1, "collectionId": None, "primaryTarget": None, "backupTarget": None}
        try:
            resolved = run_probe(self._path_request(True), self._primary_probe_state)
        except (StoragePathError, OSError) as exc:
            raise self._storage_error(getattr(exc, "code", "primary_unavailable"), str(exc)) from exc
        self.primary_dir = Path(resolved["target"])
        self._primary_linked = resolved["linked"]
        suffix = hashlib.sha256(normalized(self.primary_entry).encode()).hexdigest()[:16]
        self.binding_file = Path(binding_file) if binding_file else self.primary_dir.parent / f"NotesStorageBindings-{suffix}.json"
        try:
            safe_path(self.binding_file.parent, self.binding_file.name)
            if self.binding_file.exists():
                value = read_json(self.binding_file)
                if (set(value) != set(self._bindings) or value["version"] != 1
                        or not isinstance(value["collectionId"], str) or not value["primaryTarget"]
                        or any(value[key] is not None and (not isinstance(value[key], str) or not Path(value[key]).is_absolute())
                               for key in ("primaryTarget", "backupTarget"))):
                    raise ValueError("invalid binding record")
                self._bindings = value
        except (OSError, ValueError, TypeError) as exc:
            raise self._storage_error("storage_binding_invalid", "笔记目标绑定记录损坏，已停止写入") from exc

    def _path_request(self, primary=False, op="probe"):
        return {"op": op, "root": str(self.primary_entry if primary else self.backup_dir), "primary": primary,
                "forbiddenRoots": self.forbidden_roots,
                "protectedDirs": [self.state_dir] if self.state_dir else [], "configFile": self.config_file,
                "otherTarget": self._bindings.get("backupTarget") if primary else str(getattr(self, "primary_dir", ""))}

    def _write_binding(self, **changes):
        with self._binding_lock:
            value = {**self._bindings, **changes}
            try:
                safe_path(self.binding_file.parent, self.binding_file.name)
                atomic_write_json(self.binding_file, value)
            except (OSError, ValueError) as exc:
                raise self._storage_error("storage_binding_invalid", "无法保存笔记目标绑定，已停止写入") from exc
            self._bindings = value
            for field, key in (("primaryTarget", "primary"), ("backupTarget", "backup")):
                if changes.get(field):
                    old = self._probe_cache.get(key, {})
                    self._probe_cache = {**self._probe_cache, key: {"target": changes[field], "linked": old.get("linked")}}

    def _initialize_binding(self):
        validate_target(self.primary_entry, self.primary_dir, self._path_request(True))
        manifest = safe_path(self.primary_dir, "collection.json")
        if self._bindings["collectionId"]:
            if not manifest.exists() or read_json(manifest).get("collectionId") != self._bindings["collectionId"]:
                raise self._storage_error("primary_target_changed", "主目录目标必须是完整的同一笔记库")
        else:
            from homelab_state.notes import identifier, revision
            collection = identifier(read_json(manifest)["collectionId"]) if manifest.exists() else revision()
            self._write_binding(collectionId=collection, primaryTarget=str(self.primary_dir))

    def _prepare_primary(self):
        try:
            with self._binding_lock:
                try:
                    safe_path(self.binding_file.parent, self.binding_file.name)
                    if read_json(self.binding_file) != self._bindings:
                        raise ValueError("binding changed")
                except (OSError, ValueError) as exc:
                    raise self._storage_error("storage_binding_invalid", "笔记目标绑定记录已损坏或改变，已停止写入") from exc
            result = run_probe(self._path_request(True), self._primary_probe_state)
            self._primary_linked = result["linked"]
            target = Path(result["target"])
            if normalized(target) != normalized(self.primary_dir):
                with self._active_condition:
                    self._active_condition.wait_for(lambda: self._active_operations == 0)
                with self._lock:
                    self._switch_primary(target)
            # Cached reads still validate the data directory before acknowledging
            # availability; never follow an internal link to a different store.
            safe_path(self.primary_dir, "notes")
            self._probe_cache = {**self._probe_cache, "primary": result}
            self._primary_error = None
            # A returning original target may recover after a temporary disappearance.
            self._primary_available = True
        except Exception as exc:
            self._primary_available = False
            self._primary_error = {"code": getattr(exc, "code", "primary_unavailable"), "message": str(exc)}
            if hasattr(exc, "status"):
                raise
            raise self._storage_error(self._primary_error["code"], str(exc)) from exc

    def _switch_primary(self, target):
        # Validate before creating even a lock file in a new target.
        if read_json(safe_path(target, "collection.json")).get("collectionId") != self.collection_id:
            raise self._storage_error("primary_target_changed", "主目录目标必须是完整的同一笔记库")
        for key in ("order.json", "settings.json", "sync.json"):
            read_json(safe_path(target, key))
        if not safe_path(target, "notes").is_dir():
            raise self._storage_error("primary_target_changed", "新主目录缺少笔记数据目录")
        attributes = ("primary_dir", "_lock_file", "_notes", "_corrupt", "_disk_signatures", "_sync", "_order", "_settings", "_new_collection")
        previous = {key: getattr(self, key) for key in attributes}
        self.primary_dir = target
        self._lock_file = None
        self._notes, self._corrupt, self._disk_signatures = {}, {}, {}
        self._switching_load = True
        self._loading = True
        try:
            self._acquire_writer()
            self._load()
            self._write_binding(primaryTarget=str(target))
        except Exception:
            self._release_writer()
            for key, value in previous.items():
                setattr(self, key, value)
            raise
        finally:
            self._switching_load = False
            self._loading = False
        self._release_writer(previous["_lock_file"])
        self._primary_generation += 1

    @contextmanager
    def _storage_operation(self):
        depth = getattr(self._operation_local, "depth", 0)
        if depth:
            self._operation_local.depth += 1
            try:
                yield
            finally:
                self._operation_local.depth -= 1
            return
        with self._transition_lock:
            if self._closed:
                raise self._storage_error("primary_unavailable", "笔记服务已关闭")
            self._prepare_primary()
            with self._active_condition:
                self._active_operations += 1
        self._operation_local.depth = 1
        try:
            yield
        finally:
            self._operation_local.depth = 0
            with self._active_condition:
                self._active_operations -= 1
                self._active_condition.notify_all()

    def _release_writer(self, handle=None):
        current = handle or self._lock_file
        if current:
            try:
                current.seek(0)
                import os
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(current.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            current.close()
        if handle is None:
            self._lock_file = None

    def _probe_status(self):
        try:
            generation = self._primary_generation
            request = {"op": "probe-pair", "primaryRequest": self._path_request(True), "backupRequest": self._path_request()}
            result = run_probe(request, self._status_probe_state)
            if generation != self._primary_generation:
                result["primary"] = {"target": str(self.primary_dir), "linked": self._primary_linked}
            self._probe_cache = result
        except Exception as exc:
            self._probe_cache = {key: {"error": str(exc), "code": getattr(exc, "code", "path_probe_timeout")} for key in ("primary", "backup")}
        finally:
            self._probe_checked = time.monotonic()
            self._probe_done.set()
            self._status_probe_lock.release()

    def _path_status(self, refresh=False):
        if (refresh or time.monotonic() - self._probe_checked > 30) and self._status_probe_lock.acquire(blocking=False):
            self._probe_done.clear()
            threading.Thread(target=self._probe_status, name="notes-path-probe", daemon=True).start()
        if refresh:
            self._probe_done.wait(5)
        cache = self._probe_cache.copy()
        primary = cache.get("primary", {})
        backup = cache.get("backup", {})
        primary_code = (self._primary_error or {}).get("code") or primary.get("code")
        if primary.get("target") and normalized(primary["target"]) != normalized(self.primary_dir):
            primary_code = "primary_target_changed"
        backup_code = self._backup_error_code or backup.get("code")
        changed = backup.get("target") and self._bindings["backupTarget"] and normalized(backup["target"]) != normalized(self._bindings["backupTarget"])
        if changed:
            backup_code = "backup_target_changed"
        message = "备份目录目标已变化，请检查／恢复备份后应用重新绑定" if changed else backup.get("error") or self._backup_error
        return {"paths": {"primaryDir": str(self.primary_entry), "backupDir": str(self.backup_dir)},
                "resolvedPaths": {"primaryDir": primary.get("target", str(self.primary_dir)), "backupDir": backup.get("target")},
                "entryLinks": {"primaryDir": primary.get("linked", self._primary_linked), "backupDir": backup.get("linked")},
                "primaryErrorCode": primary_code, "backupErrorCode": backup_code,
                "primaryError": (self._primary_error or {}).get("message") or primary.get("error"),
                "pathProbePending": not self._probe_done.is_set(), "backupError": message,
                **({"backupAvailable": False} if backup_code else {}),
                **({"primaryAvailable": False} if primary_code and primary_code not in ("path_probe_timeout", "worker_unreaped", "worker_failed") else {})}
