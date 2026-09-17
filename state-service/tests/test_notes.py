from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock
import uuid

from PIL import Image

from homelab_state import notes
from homelab_state.core import atomic_write_json


def uid():
    return str(uuid.uuid4())


def mutation(expected=None, **fields):
    return {"expectedRevision": expected, "operationId": uid(), **fields}


def picture(fmt="PNG", animated=False):
    stream = io.BytesIO()
    first = Image.new("RGB", (3, 4), "red")
    if animated:
        first.save(stream, format=fmt, save_all=True, append_images=[Image.new("RGB", (3, 4), "blue")], duration=100, loop=0)
    else:
        first.save(stream, format=fmt)
    return stream.getvalue()


class NotesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.primary = root / "primary"
        self.backup = root / "backup"
        self.service = notes.NotesService(self.primary, self.backup, start_worker=False)

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def create(self, body="hello", title="", note_id=None):
        return self.service.save_note(note_id or uid(), mutation(title=title, body=body))["note"]

    def wait_job(self, response):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            job = self.service.get_job(response["job"]["id"])["job"]
            if job["status"] != "running":
                self.assertEqual(job["status"], "complete", job)
                return job
            time.sleep(.02)
        self.fail("job timed out")

    def test_note_create_update_idempotence_and_revision_conflict(self):
        note_id = uid()
        request = mutation(title="work", body="first")
        first = self.service.save_note(note_id, request)["note"]
        repeated = self.service.save_note(note_id, request)["note"]
        self.assertEqual(first, repeated)
        second = self.service.save_note(note_id, mutation(first["revision"], title="work", body="second"))["note"]
        with self.assertRaises(notes.NotesError) as caught:
            self.service.save_note(note_id, mutation(first["revision"], title="work", body="third"))
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(caught.exception.current["revision"], second["revision"])
        self.assertEqual(self.service.get_note(note_id)["note"]["body"], "second")

    def test_operation_cannot_reuse_identifier_with_other_payload(self):
        note_id = uid()
        request = mutation(title="", body="first")
        self.service.save_note(note_id, request)
        with self.assertRaises(notes.NotesError) as caught:
            self.service.save_note(note_id, {**request, "body": "different"})
        self.assertEqual(caught.exception.code, "operation_conflict")

    def test_unchanged_note_performs_no_storage_write(self):
        note = self.create()
        with mock.patch.object(self.service, "_persist") as persist:
            result = self.service.save_note(note["id"], mutation(note["revision"], title="", body="hello"))
        self.assertEqual(result["note"]["revision"], note["revision"])
        persist.assert_not_called()

    def test_delete_has_no_old_content_and_cannot_be_resurrected_by_put(self):
        note = self.create("private text")
        request = mutation(note["revision"])
        deleted = self.service.delete_note(note["id"], request)["note"]
        self.assertNotIn("body", deleted)
        self.assertNotIn("title", deleted)
        self.assertEqual(self.service.list_notes()["notes"], [])
        self.assertEqual(self.service.delete_note(note["id"], request)["note"], deleted)
        with self.assertRaises(notes.NotesError) as caught:
            self.service.get_note(note["id"])
        self.assertEqual(caught.exception.status, 410)
        with self.assertRaises(notes.NotesError):
            self.service.save_note(note["id"], mutation(deleted["revision"], title="", body="resurrect"))
        raw = (self.primary / "notes" / (note["id"] + ".json")).read_text(encoding="utf-8")
        self.assertNotIn("private text", raw)

    def test_order_validation_and_new_notes_first(self):
        first, second = self.create("a"), self.create("b")
        listing = self.service.list_notes()
        self.assertEqual([note["id"] for note in listing["notes"]], [second["id"], first["id"]])
        ordered = self.service.update_order(mutation(listing["order"]["revision"], ids=[first["id"], second["id"]]))
        edited = self.service.save_note(first["id"], mutation(first["revision"], title="", body="new"))
        self.assertTrue(edited)
        self.assertEqual(self.service.list_notes()["order"]["ids"], [first["id"], second["id"]])
        with self.assertRaises(notes.NotesError):
            self.service.update_order(mutation(ordered["order"]["revision"], ids=[first["id"]]))
        with self.assertRaises(notes.NotesError):
            self.service.update_order(mutation(ordered["order"]["revision"], ids=[{}]))

    def test_settings_are_shared_revision_checked_and_strict(self):
        initial = self.service.get_settings()["settings"]
        self.assertEqual(initial["autoSaveSeconds"], 60)
        changed = self.service.update_settings(mutation(initial["revision"], autoSaveSeconds=10, collapseOnBlur=True))["settings"]
        self.assertTrue(changed["collapseOnBlur"])
        for value in (True, 9, 3601, 10.5):
            with self.assertRaises(notes.NotesError):
                self.service.update_settings(mutation(changed["revision"], autoSaveSeconds=value))
        with self.assertRaises(notes.NotesError):
            self.service.update_settings(mutation(initial["revision"], autoSaveEnabled=False))

    def test_limits_allow_unicode_and_empty_titles(self):
        self.create("hi", "标题" * 100)
        with self.assertRaises(notes.NotesError) as caught:
            self.create("hi", "字" * 201)
        self.assertEqual(caught.exception.status, 413)
        with self.assertRaises(notes.NotesError):
            self.create("字" * (notes.MAX_BODY // 3 + 1))

    def test_markdown_reference_parsing_ignores_code_and_html(self):
        image_id = uid()
        path = "/api/notes/images/" + image_id
        source = f"`![x]({path})`\n\n```\n![x]({path})\n```\n\n<img src='{path}'>"
        self.assertEqual(notes.extract_attachments(source), [])
        self.assertEqual(notes.extract_attachments(f"![x]({path})\n[open]({path})"), [image_id])
        self.assertEqual(notes.extract_attachments(f"![x](https://foreign.test{path})"), [])
        self.assertEqual(notes.extract_attachments(f"![x][i]\n\n[i]: {path}"), [image_id])

    def test_single_writer_lock_and_reopen_persistence(self):
        note = self.create("survives")
        with self.assertRaises(notes.NotesError) as caught:
            notes.NotesService(self.primary, self.backup, start_worker=False)
        self.assertEqual(caught.exception.code, "store_locked")
        self.service.close()
        self.service = notes.NotesService(self.primary, self.backup, start_worker=False)
        self.assertEqual(self.service.get_note(note["id"])["note"]["body"], "survives")
        self.assertIn("notes/" + note["id"] + ".json", self.service._sync["pending"])

    def test_corrupt_primary_never_reinitialized(self):
        self.service.close()
        (self.primary / "collection.json").write_text("broken", encoding="utf-8")
        with self.assertRaises((ValueError, notes.NotesError)):
            notes.NotesService(self.primary, self.backup, start_worker=False)
        self.assertEqual((self.primary / "collection.json").read_text(), "broken")

    def test_primary_failure_does_not_falsely_acknowledge_save(self):
        original = notes.atomic_write_json
        def failed(path, value):
            if path.parent.name == "notes":
                raise OSError("disk full")
            return original(path, value)
        with mock.patch.object(notes, "atomic_write_json", side_effect=failed):
            with self.assertRaises(notes.NotesError) as caught:
                self.create("not saved")
        self.assertEqual(caught.exception.code, "primary_unavailable")
        self.assertEqual(self.service._notes, {})
        self.assertTrue(self.service.status()["primaryAvailable"])

    def test_backup_actual_worker_and_no_history(self):
        note = self.create("version one")
        self.service._backup_once()
        path = self.backup / "notes" / (note["id"] + ".json")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["body"], "version one")
        self.service.save_note(note["id"], mutation(note["revision"], title="", body="version two"))
        self.service._backup_once()
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["body"], "version two")
        self.assertEqual(len(list(path.parent.glob("*"))), 1)
        self.assertEqual(self.service.status()["backupPending"], 0)

    def test_backup_failure_does_not_block_primary_writes(self):
        with mock.patch.object(self.service, "_worker", side_effect=notes.NotesError(503, "offline", "offline")):
            self.create("still saved")
            with self.assertRaises(notes.NotesError):
                self.service._backup_once()
            self.create("also saved")
        self.assertEqual(len(self.service.list_notes()["notes"]), 2)

    def test_backup_external_change_is_not_overwritten(self):
        note = self.create("ours")
        self.service._backup_once()
        path = self.backup / "notes" / (note["id"] + ".json")
        external = json.loads(path.read_text(encoding="utf-8"))
        external["body"] = "externally edited"
        atomic_write_json(path, external)
        self.service.save_note(note["id"], mutation(note["revision"], title="", body="new ours"))
        self.service._backup_once()
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["body"], "externally edited")
        self.assertEqual(self.service.status()["backupBlocked"], 1)

    def test_backup_completion_does_not_drop_newer_pending_write(self):
        note = self.create("snapshot")
        real_worker = self.service._worker
        invoked = False
        def worker(op, **kwargs):
            nonlocal invoked
            if op == "backup" and kwargs.get("path", "").startswith("notes/") and not invoked:
                invoked = True
                self.service.save_note(note["id"], mutation(note["revision"], title="", body="newer"))
            return real_worker(op, **kwargs)
        with mock.patch.object(self.service, "_worker", side_effect=worker):
            self.service._backup_once()
        self.assertIn("notes/" + note["id"] + ".json", self.service._sync["pending"])
        self.service._backup_once()
        self.assertEqual(self.service.status()["backupPending"], 0)

    def test_upload_dedupe_original_animation_and_cross_note_cleanup(self):
        image_id = uid()
        data = picture("GIF", animated=True)
        uploaded = self.service.upload(image_id, "animation.gif", data)
        self.assertEqual(uploaded["frames"], 2)
        self.assertEqual(self.service.upload(image_id, "animation.gif", data), uploaded)
        self.assertEqual(self.service.get_image(image_id), (data, "image/gif"))
        first = self.create("![first](" + uploaded["url"] + ")")
        second = self.create("![second](" + uploaded["url"] + ")")
        self.service.delete_note(first["id"], mutation(first["revision"]))
        self.service._backup_once()
        self.assertTrue((self.backup / "images" / image_id).exists())
        self.service.delete_note(second["id"], mutation(second["revision"]))
        self.assertEqual(self.service._sync["uploads"][image_id]["status"], "deleting")
        self.service._backup_once()
        self.assertFalse((self.backup / "images" / image_id).exists())

    def test_staged_upload_lease_and_missing_draft_reference(self):
        image_id = uid()
        uploaded = self.service.upload(image_id, "photo.png", picture())
        self.create("ordinary save does not delete a staged image")
        self.service._backup_once()
        self.assertTrue((self.backup / "images" / image_id).exists())
        self.service._sync["uploads"][image_id]["lastTouch"] = time.time() - 301
        self.service._backup_once()
        self.assertFalse((self.backup / "images" / image_id).exists())
        recovered = self.create("![missing](" + uploaded["url"] + ")")
        self.assertIn(image_id, recovered["attachments"])

    def test_upload_invalid_input_rejected_and_response_loss_recovered(self):
        with self.assertRaises(notes.NotesError) as caught:
            self.service.upload(uid(), "bad.png", b"not an image")
        self.assertEqual(caught.exception.code, "invalid_image")
        image_id = uid()
        self.service.upload(image_id, "image.png", picture())
        self.service._sync["uploads"][image_id]["status"] = "failed"
        self.assertEqual(self.service.get_upload(image_id)["status"], "uploaded")
        with self.assertRaises(notes.NotesError) as caught:
            self.service.upload(image_id, "other.gif", picture("GIF"))
        self.assertEqual(caught.exception.status, 409)

    def test_reconcile_preview_and_explicit_use_backup(self):
        note = self.create("saved backup")
        self.service._backup_once()
        self.service.save_note(note["id"], mutation(note["revision"], title="", body="local unsent"))
        check = self.wait_job(self.service.reconcile_check())
        self.assertEqual(check["result"]["items"][0]["type"], "different")
        applied = self.wait_job(self.service.reconcile_apply({"checkId": check["id"], "choices": [{"id": note["id"], "action": "use-backup"}]}))
        self.assertEqual(applied["result"]["items"][0]["status"], "applied")
        self.assertEqual(self.service.get_note(note["id"])["note"]["body"], "saved backup")

    def test_reconcile_prevents_stale_check_and_does_not_auto_restore_deletion(self):
        note = self.create("alive")
        self.service._backup_once()
        tombstone = self.service.delete_note(note["id"], mutation(note["revision"]))["note"]
        check = self.wait_job(self.service.reconcile_check())
        self.assertEqual(check["result"]["items"][0]["type"], "deleted")
        self.assertEqual(self.service.list_notes()["notes"], [])
        changed = dict(self.service._notes[note["id"]], revision=uid())
        atomic_write_json(self.primary / "notes" / (note["id"] + ".json"), changed)
        applied = self.wait_job(self.service.reconcile_apply({"checkId": check["id"], "choices": [{"id": note["id"], "action": "use-backup"}]}))
        self.assertEqual(applied["result"]["items"][0]["status"], "conflict")
        self.assertTrue(tombstone["deleted"])

    def test_worker_timeout_kills_process_before_return(self):
        process = mock.Mock()
        process.communicate.side_effect = [notes.subprocess.TimeoutExpired("worker", 15), (b"", b"")]
        with mock.patch.object(notes.subprocess, "Popen", return_value=process):
            with self.assertRaises(notes.NotesError) as caught:
                self.service._worker("scan", timeout=15)
        self.assertEqual(caught.exception.code, "worker_timeout")
        process.kill.assert_called_once()
        self.assertEqual(process.communicate.call_count, 2)
        self.assertIsNone(self.service._process)

    def test_unreaped_worker_prevents_spawning_another(self):
        process = mock.Mock()
        process.poll.return_value = None
        process.communicate.side_effect = notes.subprocess.TimeoutExpired("worker", 15)
        with mock.patch.object(notes.subprocess, "Popen", return_value=process) as popen:
            with self.assertRaises(notes.NotesError) as caught:
                self.service._worker("scan", timeout=15)
            self.assertEqual(caught.exception.code, "worker_unreaped")
            with self.assertRaises(notes.NotesError):
                self.service._worker("scan", timeout=15)
            popen.assert_called_once()
        self.service._process = None

    def test_rebuild_primary_explicitly_adopts_backup_and_recovers_managed_images(self):
        image_id = uid()
        self.service.upload(image_id, "photo.png", picture())
        note = self.create("![photo](/api/notes/images/" + image_id + ")")
        old_collection = self.service.collection_id
        self.service._backup_once()
        self.service.close()
        self.service = notes.NotesService(self.primary.parent / "rebuilt", self.backup, start_worker=False)
        check = self.wait_job(self.service.reconcile_check())
        self.assertNotEqual(self.service.collection_id, old_collection)
        restored = self.wait_job(self.service.reconcile_apply({"checkId": check["id"], "choices": [{"id": note["id"], "action": "use-backup"}]}))
        self.assertEqual(restored["result"]["items"][0]["status"], "applied")
        self.assertTrue(restored["result"]["adoptedCollection"])
        self.assertEqual(self.service.get_image(image_id)[0], picture())
        self.assertEqual(self.service._sync["uploads"][image_id]["status"], "committed")
        # Unselected setting/order differences stay blocked rather than silently
        # overwriting either side during the next background backup.
        self.assertIn("settings.json", self.service._sync["blocked"])
        current = self.service.get_note(note["id"])["note"]
        self.service.delete_note(note["id"], mutation(current["revision"]))
        self.service._backup_once()
        self.assertFalse((self.backup / "images" / image_id).exists())

    def test_corrupt_primary_note_is_readonly_and_can_be_restored_explicitly(self):
        note = self.create("backed up text")
        self.service._backup_once()
        path = self.primary / "notes" / (note["id"] + ".json")
        self.service.close()
        path.write_text("broken JSON", encoding="utf-8")
        self.service = notes.NotesService(self.primary, self.backup, start_worker=False)
        self.assertFalse(self.service.status()["primaryAvailable"])
        self.assertEqual(path.read_text(), "broken JSON")
        with self.assertRaises(notes.NotesError):
            self.create("should be read-only")
        check = self.wait_job(self.service.reconcile_check())
        item = check["result"]["items"][0]
        self.assertEqual(item["type"], "corrupt")
        self.assertTrue(item["primaryCorrupt"])
        restored = self.wait_job(self.service.reconcile_apply({"checkId": check["id"], "choices": [{"id": note["id"], "action": "use-backup"}]}))
        self.assertEqual(restored["result"]["items"][0]["status"], "applied")
        self.assertTrue(self.service.status()["primaryAvailable"])
        self.assertEqual(self.service.get_note(note["id"])["note"]["body"], "backed up text")

    def test_corrupt_backup_can_be_explicitly_replaced_with_checked_primary(self):
        note = self.create("good primary")
        self.service._backup_once()
        path = self.backup / "notes" / (note["id"] + ".json")
        path.write_text("broken backup", encoding="utf-8")
        check = self.wait_job(self.service.reconcile_check())
        self.assertTrue(check["result"]["items"][0]["backupCorrupt"])
        restored = self.wait_job(self.service.reconcile_apply({"checkId": check["id"], "choices": [{"id": note["id"], "action": "use-primary"}]}))
        self.assertEqual(restored["result"]["items"][0]["status"], "applied")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["body"], "good primary")

    def test_status_detects_new_disk_corruption_before_recovery(self):
        note = self.create("existing")
        path = self.primary / "notes" / (note["id"] + ".json")
        path.write_text("broken", encoding="utf-8")
        self.assertFalse(self.service.status()["primaryAvailable"])
        self.assertEqual(self.service.status()["primaryCorrupt"], 1)

    def test_permission_error_is_not_a_retryable_upload_failure(self):
        process = mock.Mock(returncode=0)
        process.communicate.return_value = (json.dumps({"ok": False, "kind": "PermissionError", "error": "denied"}).encode(), b"")
        with mock.patch.object(notes.subprocess, "Popen", return_value=process):
            with self.assertRaises(notes.NotesError) as caught:
                self.service._worker("image-write")
        self.assertEqual(caught.exception.status, 403)
        self.assertEqual(caught.exception.code, "storage_permission")

    def test_missing_existing_record_is_not_reset_or_written_over_backup(self):
        note = self.create("restore from backup")
        self.service._backup_once()
        original_settings = (self.backup / "settings.json").read_bytes()
        self.service.close()
        (self.primary / "settings.json").unlink()
        (self.primary / "notes" / (note["id"] + ".json")).unlink()
        self.service = notes.NotesService(self.primary, self.backup, start_worker=False)
        self.assertFalse((self.primary / "settings.json").exists())
        self.assertEqual(self.service.status()["primaryCorrupt"], 2)
        with self.assertRaises(notes.NotesError):
            self.service._backup_once()
        self.assertEqual((self.backup / "settings.json").read_bytes(), original_settings)
        check = self.wait_job(self.service.reconcile_check())
        restored = self.wait_job(self.service.reconcile_apply({"checkId": check["id"], "choices": [{"id": note["id"], "action": "use-backup"}, {"id": "settings", "action": "use-backup"}]}))
        self.assertTrue(all(item["status"] == "applied" for item in restored["result"]["items"]))
        self.assertTrue(self.service.status()["primaryAvailable"])

    def test_missing_sync_file_does_not_silently_reset_attachment_ownership(self):
        self.service.close()
        (self.primary / "sync.json").unlink()
        with self.assertRaises(notes.NotesError):
            notes.NotesService(self.primary, self.backup, start_worker=False)
        self.assertFalse((self.primary / "sync.json").exists())

    def test_attachment_catalog_is_validated_before_background_deletion(self):
        image_id = uid()
        self.service.upload(image_id, "photo.png", picture())
        self.service.close()
        self.service = notes.NotesService(self.primary, self.backup, start_worker=False)
        self.assertEqual(self.service.get_upload(image_id)["status"], "uploaded")
        self.service.close()
        sync = json.loads((self.primary / "sync.json").read_text(encoding="utf-8"))
        sync["uploads"]["../settings.json"] = {**sync["uploads"][image_id], "status": "deleting"}
        atomic_write_json(self.primary / "sync.json", sync)
        with self.assertRaises(notes.NotesError):
            notes.NotesService(self.primary, self.backup, start_worker=False)
        self.assertTrue((self.backup / "images" / image_id).exists())


if __name__ == "__main__":
    unittest.main()
