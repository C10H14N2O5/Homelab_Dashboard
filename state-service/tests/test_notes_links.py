from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import tempfile
import stat
from types import SimpleNamespace
import threading
import time
import unittest
from unittest import mock
import uuid

from homelab_state import notes, notes_paths
from tests.helpers import image_bytes, link_directory, repoint_directory


def uid():
    return str(uuid.uuid4())


class NotesLinksTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.primary = self.root / "primary"
        self.backup = self.root / "backup"
        self.state = self.root / "state"
        for path in (self.primary, self.backup, self.state):
            path.mkdir()
        self.primary_entry = self.root / "primary-link"
        self.backup_entry = self.root / "backup-link"
        link_directory(self.primary_entry, self.primary)
        link_directory(self.backup_entry, self.backup)
        self.service = self.open()
        self.addCleanup(lambda: self.service.close())

    def open(self):
        return notes.NotesService(self.primary_entry, self.backup_entry, start_worker=False,
                                  binding_file=self.state / "bindings.json", state_dir=self.state)

    def create(self, body="hello"):
        return self.service.save_note(uid(), {"expectedRevision": None, "operationId": uid(), "title": "test", "body": body})["note"]

    def job(self, response):
        end = time.monotonic() + 20
        while time.monotonic() < end:
            result = self.service.get_job(response["job"]["id"])["job"]
            if result["status"] != "running":
                return result
            time.sleep(.02)
        self.fail("job did not complete")

    def copy_primary(self):
        target = self.root / "new-primary"
        shutil.copytree(self.primary, target, ignore=shutil.ignore_patterns(".writer.lock"))
        return target

    def move_backup(self, copy=False):
        target = self.root / "new-backup"
        if copy:
            shutil.copytree(self.backup, target)
        else:
            target.mkdir()
        repoint_directory(self.backup_entry, target)
        return target

    def test_link_upload_read_backup_and_status(self):
        identity = uid()
        self.service.upload(identity, "test.png", image_bytes())
        self.create(f"![test](/api/notes/images/{identity})")
        self.service._backup_once()
        self.assertEqual(self.service.get_image(identity)[0], image_bytes())
        status = self.service.status(refresh=True)
        self.assertEqual(status["paths"]["primaryDir"], str(self.primary_entry))
        self.assertEqual(status["resolvedPaths"]["backupDir"], str(self.backup))
        self.assertTrue(all(status["entryLinks"].values()))
        self.assertEqual(self.job(self.service.reconcile_check())["result"]["items"], [])

    def test_multi_hop_and_parent_link(self):
        parent = self.root / "parent-link"
        link_directory(parent, self.root)
        hop = self.root / "hop"
        link_directory(hop, self.backup_entry)
        self.assertEqual(notes_paths.resolve_entry(parent / "hop")[0], self.backup)

    def test_missing_primary_entry_never_created(self):
        missing = self.root / "missing"
        with self.assertRaises(notes.NotesError) as caught:
            notes.NotesService(missing, self.backup, start_worker=False)
        self.assertEqual(caught.exception.code, "storage_target_missing")
        self.assertFalse(missing.exists())

    def test_missing_backup_distinct_from_empty_and_never_created(self):
        self.assertEqual(self.service._worker("scan")["records"], {})
        missing = self.root / "missing-target"
        missing.mkdir()
        repoint_directory(self.backup_entry, missing)
        missing.rmdir()
        with self.assertRaises(notes.NotesError) as caught:
            self.service._worker("scan")
        self.assertEqual(caught.exception.code, "storage_target_missing")
        self.assertFalse((self.root / "missing-target").exists())
        self.assertFalse(self.service.status(refresh=True)["backupAvailable"])
        self.create("primary still works")

    def test_retarget_empty_stops_all_normal_io_but_not_primary(self):
        self.service._backup_once()
        target = self.move_backup()
        for op, values in (("backup", {"path": "settings.json", "value": {}}), ("image-delete", {"id": uid()}), ("image-read", {"id": uid()})):
            with self.subTest(op=op), self.assertRaises(notes.NotesError) as caught:
                self.service._worker(op, **values)
            self.assertEqual(caught.exception.code, "backup_target_changed")
        with self.assertRaises(notes.NotesError):
            self.service.upload(uid(), "test.png", image_bytes())
        self.assertEqual(list(target.iterdir()), [])
        self.create("local only")
        self.assertEqual(self.service.status(refresh=True)["backupErrorCode"], "backup_target_changed")

    def test_target_gate_survives_restart(self):
        self.service._backup_once()
        target = self.move_backup()
        self.service.close()
        self.service = self.open()
        with self.assertRaises(notes.NotesError) as caught:
            self.service._backup_once()
        self.assertEqual(caught.exception.code, "backup_target_changed")
        self.assertFalse((target / "collection.json").exists())

    def test_identical_copy_rebind_without_choices(self):
        self.create()
        self.service._backup_once()
        target = self.move_backup(copy=True)
        checked = self.job(self.service.reconcile_check())
        self.assertTrue(checked["result"]["requiresRebind"])
        self.assertEqual(checked["result"]["items"], [])
        applied = self.job(self.service.reconcile_apply({"checkId": checked["id"], "choices": []}))
        self.assertEqual(applied["status"], "complete")
        self.assertEqual(applied["result"]["reboundTarget"], str(target))
        self.service._backup_once()

    def test_empty_rebind_leaves_unselected_differences_blocked(self):
        self.create()
        self.service._backup_once()
        target = self.move_backup()
        checked = self.job(self.service.reconcile_check())
        applied = self.job(self.service.reconcile_apply({"checkId": checked["id"], "choices": []}))
        self.assertEqual(applied["status"], "complete")
        self.service._backup_once()
        self.assertEqual([p.name for p in target.iterdir()], ["collection.json"])
        self.assertGreater(self.service.status()["backupBlocked"], 0)

    def test_changed_target_after_check_is_stale(self):
        self.service._backup_once()
        self.move_backup(copy=True)
        checked = self.job(self.service.reconcile_check())
        other = self.root / "third-backup"
        other.mkdir()
        repoint_directory(self.backup_entry, other)
        applied = self.job(self.service.reconcile_apply({"checkId": checked["id"], "choices": []}))
        self.assertEqual(applied["error"], "check_stale")
        self.assertEqual(list(other.iterdir()), [])

    def test_primary_hot_switch_reloads_and_releases_old_writer(self):
        note = self.create("migrated")
        target = self.copy_primary()
        repoint_directory(self.primary_entry, target)
        self.assertEqual(self.service.get_note(note["id"])["note"]["body"], "migrated")
        self.assertEqual(self.service.primary_dir, target)
        self.assertEqual(self.service._primary_generation, 1)
        old = notes.NotesService(self.primary, self.backup, start_worker=False)
        old.close()

    def test_primary_empty_target_pauses_and_returns_to_original(self):
        self.create()
        target = self.root / "empty-primary"
        target.mkdir()
        repoint_directory(self.primary_entry, target)
        with self.assertRaises(notes.NotesError):
            self.create("must not reach either directory")
        self.assertEqual(list(target.iterdir()), [])
        repoint_directory(self.primary_entry, self.primary)
        self.assertEqual(len(self.service.list_notes()["notes"]), 1)

    def test_primary_damaged_copy_or_other_collection_rejected(self):
        self.create()
        target = self.copy_primary()
        (target / "settings.json").write_text("{}")
        repoint_directory(self.primary_entry, target)
        with self.assertRaises(notes.NotesError):
            self.service.list_notes()
        (target / "collection.json").write_text(json.dumps({"version": 1, "collectionId": uid()}))
        with self.assertRaises(notes.NotesError) as caught:
            self.service.list_notes()
        self.assertEqual(caught.exception.code, "primary_target_changed")

    def test_primary_busy_target_keeps_old_lock(self):
        target = self.copy_primary()
        holder = notes.NotesService(target, self.backup, start_worker=False)
        try:
            repoint_directory(self.primary_entry, target)
            with self.assertRaises(notes.NotesError) as caught:
                self.service.list_notes()
            self.assertEqual(caught.exception.code, "store_locked")
            self.assertEqual(self.service.primary_dir, self.primary)
        finally:
            holder.close()
        self.service.list_notes()
        self.assertEqual(self.service.primary_dir, target)

    def test_hot_switch_waits_for_inflight_operation(self):
        target = self.copy_primary()
        entered, release = threading.Event(), threading.Event()
        def in_flight():
            with self.service._storage_operation():
                entered.set()
                release.wait(10)
                return self.service.primary_dir
        with ThreadPoolExecutor(2) as pool:
            old = pool.submit(in_flight)
            self.assertTrue(entered.wait(5))
            repoint_directory(self.primary_entry, target)
            new = pool.submit(self.service.list_notes)
            try:
                time.sleep(.3)
                self.assertFalse(new.done())
            finally:
                release.set()
            self.assertEqual(old.result(10), self.primary)
            new.result(10)
        self.assertEqual(self.service.primary_dir, target)

    def test_primary_handoff_invalidates_reconcile_job(self):
        self.service._backup_once()
        self.move_backup(copy=True)
        checked = self.job(self.service.reconcile_check())
        repoint_directory(self.primary_entry, self.copy_primary())
        applied = self.job(self.service.reconcile_apply({"checkId": checked["id"], "choices": []}))
        self.assertEqual(applied["error"], "check_stale")

    def test_internal_directory_link_rejected(self):
        other = self.root / "images-elsewhere"
        other.mkdir()
        link_directory(self.backup / "images", other)
        with self.assertRaises(notes.NotesError):
            self.service.upload(uid(), "test.png", image_bytes())
        self.assertEqual(list(other.iterdir()), [])

    def test_internal_primary_link_rejected_before_following_children(self):
        directory = self.primary / "notes"
        directory.rmdir()
        other = self.root / "private-elsewhere"
        other.mkdir()
        link_directory(directory, other)
        with self.assertRaises(notes.NotesError):
            self.service.list_notes()
        self.assertEqual(list(other.iterdir()), [])

    def test_boundary_and_bidirectional_overlap(self):
        for target in (self.primary, self.primary / "inside", self.root, self.state):
            target.mkdir(exist_ok=True)
            repoint_directory(self.backup_entry, target)
            with self.subTest(target=target), self.assertRaises(notes.NotesError):
                self.service._worker("scan")

    def test_corrupt_binding_blocks_writes_without_reset(self):
        self.service.binding_file.write_text("bad")
        with self.assertRaises(notes.NotesError) as caught:
            self.create()
        self.assertEqual(caught.exception.code, "storage_binding_invalid")
        self.service.close()
        with self.assertRaises(notes.NotesError):
            self.open()
        self.assertEqual(self.service.binding_file.read_text(), "bad")

    def test_binding_commit_failure_keeps_changed_target_blocked(self):
        self.service._backup_once()
        target = self.move_backup(copy=True)
        checked = self.job(self.service.reconcile_check())
        with mock.patch("homelab_state.notes_storage.atomic_write_json", side_effect=OSError("disk full")):
            applied = self.job(self.service.reconcile_apply({"checkId": checked["id"], "choices": []}))
        self.assertEqual(applied["error"], "storage_binding_invalid")
        self.assertEqual(self.service._bindings["backupTarget"], str(self.backup))
        with self.assertRaises(notes.NotesError) as caught:
            self.service._worker("image-delete", id=uid())
        self.assertEqual(caught.exception.code, "backup_target_changed")

    def test_primary_overlap_with_previously_bound_backup_is_rejected(self):
        self.service._backup_once()
        repoint_directory(self.primary_entry, self.backup)
        with self.assertRaises(notes.NotesError) as caught:
            self.service.list_notes()
        self.assertEqual(caught.exception.code, "storage_overlap")

    def test_runtime_rebind_validates_changed_backup_content(self):
        self.service._backup_once()
        target = self.move_backup(copy=True)
        checked = self.job(self.service.reconcile_check())
        (target / "settings.json").write_text("{}")
        applied = self.job(self.service.reconcile_apply({"checkId": checked["id"], "choices": []}))
        self.assertEqual(applied["error"], "check_stale")
        self.assertEqual(self.service._bindings["backupTarget"], str(self.backup))

    def test_identical_rebind_rejects_new_primary_content_after_check(self):
        self.service._backup_once()
        self.move_backup(copy=True)
        checked = self.job(self.service.reconcile_check())
        self.assertEqual(checked["result"]["items"], [])
        self.create("changed after the complete snapshot")
        applied = self.job(self.service.reconcile_apply({"checkId": checked["id"], "choices": []}))
        self.assertEqual(applied["error"], "check_stale")
        self.assertEqual(self.service._bindings["backupTarget"], str(self.backup))

    def test_idempotent_upload_obeys_changed_target_gate(self):
        identity = uid()
        self.service.upload(identity, "test.png", image_bytes())
        self.move_backup(copy=True)
        with self.assertRaises(notes.NotesError) as caught:
            self.service.upload(identity, "test.png", image_bytes())
        self.assertEqual(caught.exception.code, "backup_target_changed")

    def test_unc_and_mapped_target_are_not_local(self):
        with self.assertRaises(notes_paths.StoragePathError):
            notes_paths.require_local(Path(r"\\NAS\share\primary"))
        if os.name == "nt":
            import ctypes
            with mock.patch.object(ctypes.windll.kernel32, "GetDriveTypeW", return_value=4):
                with self.assertRaises(notes_paths.StoragePathError):
                    notes_paths.require_local(self.primary)

    def test_probe_timeout_is_bounded_and_unreaped_child_not_replaced(self):
        child = mock.Mock()
        child.poll.return_value = None
        child.communicate.side_effect = notes_paths.subprocess.TimeoutExpired("probe", 5)
        state = {}
        with mock.patch.object(notes_paths.subprocess, "Popen", return_value=child) as popen:
            with self.assertRaises(notes_paths.StoragePathError) as caught:
                notes_paths.run_probe({"op": "probe"}, state)
            self.assertEqual(caught.exception.code, "worker_unreaped")
            with self.assertRaises(notes_paths.StoragePathError):
                notes_paths.run_probe({}, state)
            popen.assert_called_once()
        child.kill.assert_called_once()

    def test_probe_does_not_block_primary_editing(self):
        entered, release = threading.Event(), threading.Event()
        actual = notes.run_probe
        def slow_status(request, state, **kwargs):
            if request["op"] == "probe-pair":
                entered.set()
                release.wait(5)
            return actual(request, state, **kwargs)
        with mock.patch("homelab_state.notes_storage.run_probe", side_effect=slow_status):
            self.service.status()
            self.assertTrue(entered.wait(2))
            try:
                self.create("does not wait for status")
            finally:
                release.set()
                self.service._probe_done.wait(5)

    def test_cycle_unsupported_reparse_and_volume_mount_are_rejected(self):
        original = Path.lstat
        for tag, target, code in ((0xA000000C, str(self.primary_entry), "storage_link_loop"),
                                  (0xA0000003, r"\\?\Volume{00000000-0000-0000-0000-000000000000}", "unsupported_link_type"),
                                  (0xA0000099, str(self.primary), "unsupported_link_type")):
            def metadata(path, **kwargs):
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_reparse_tag=tag) if path == self.primary_entry else original(path, **kwargs)
            with self.subTest(tag=tag), mock.patch.object(Path, "lstat", metadata), mock.patch.object(notes_paths.os, "readlink", return_value=target):
                with self.assertRaises(notes_paths.StoragePathError) as caught:
                    notes_paths.resolve_entry(self.primary_entry)
                self.assertEqual(caught.exception.code, code)

    def test_timeout_status_does_not_disable_local_editing(self):
        self.service._probe_cache = {"primary": {"code": "path_probe_timeout"}, "backup": {"code": "path_probe_timeout"}}
        self.service._probe_checked = time.monotonic()
        self.assertTrue(self.service.status()["primaryAvailable"])
        self.create()
