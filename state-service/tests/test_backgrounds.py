from __future__ import annotations

import io
import json
import os
import shutil
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from homelab_state import backgrounds, core
from tests.helpers import FIXED_TIME, TemporaryProject, image_bytes


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = TemporaryProject()
        self.config = self.project.write_config()
        self.store = backgrounds.StateStore(self.config.background_dir, self.config.state_file, lambda: FIXED_TIME)

    def tearDown(self) -> None:
        self.project.close()

    def write_real_backgrounds(self) -> None:
        formats = {
            "bg.jpg": "JPEG",
            "test.jpg": "JPEG",
            "portrait.png": "PNG",
            "alternate.webp": "WEBP",
        }
        for name, image_format in formats.items():
            (self.project.backgrounds / name).write_bytes(image_bytes(image_format))

    def thumbnail_path(self, manifest: dict[str, object], background: str) -> Path:
        records = manifest["backgrounds"]
        record = next(item for item in records if item["name"] == background)
        thumbnail = record["thumbnail"]
        self.assertIsInstance(thumbnail, str)
        return self.project.backgrounds / thumbnail.split("?", 1)[0]

    def test_initial_state_and_independent_updates_persist(self) -> None:
        self.assertEqual(
            self.store.get(),
            {
                "version": 1,
                "landscape": "bg.jpg",
                "portrait": "bg.jpg",
                "updatedAt": FIXED_TIME,
            },
        )
        self.store.update("landscape", "test.jpg")
        result = self.store.update("portrait", "portrait.png")
        self.assertEqual(result["landscape"], "test.jpg")
        self.assertEqual(result["portrait"], "portrait.png")
        reloaded = backgrounds.StateStore(self.config.background_dir, self.config.state_file, lambda: FIXED_TIME)
        self.assertEqual(reloaded.get(), result)

    def test_validation(self) -> None:
        for name, error in (
            ("../test.jpg", backgrounds.InvalidBackground),
            ("test.svg", backgrounds.InvalidBackground),
            ("missing.jpg", backgrounds.BackgroundMissing),
        ):
            with self.subTest(name=name), self.assertRaises(error):
                self.store.update("landscape", name)
        with self.assertRaises(ValueError):
            self.store.update("desktop", "test.jpg")

    def test_deleted_selection_falls_back_and_repairs_state(self) -> None:
        self.store.update("landscape", "test.jpg")
        (self.project.backgrounds / "test.jpg").unlink()
        repaired = self.store.get()
        self.assertEqual(repaired["landscape"], "bg.jpg")
        persisted = json.loads(self.config.state_file.read_text(encoding="utf-8"))
        self.assertEqual(persisted, repaired)

    def test_concurrent_updates_leave_valid_complete_state(self) -> None:
        errors: list[Exception] = []

        def update(index: int) -> None:
            try:
                mode = "landscape" if index % 2 == 0 else "portrait"
                background = ("bg.jpg", "test.jpg", "portrait.png", "alternate.webp")[index % 4]
                self.store.update(mode, background)
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=update, args=(index,)) for index in range(40)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        persisted = json.loads(self.config.state_file.read_text(encoding="utf-8"))
        self.assertEqual(set(persisted), {"version", "landscape", "portrait", "updatedAt"})
        self.assertFalse(any(path.name.startswith(".state.json.") for path in self.config.state_dir.iterdir()))

    def test_upload_validates_image_and_uses_collision_safe_names(self) -> None:
        self.write_real_backgrounds()
        payload = image_bytes()
        first = self.store.upload_background("holiday.png", io.BytesIO(payload), len(payload))
        second = self.store.upload_background("holiday.png", io.BytesIO(payload), len(payload))

        self.assertEqual(first, {"background": "holiday.png", "size": len(payload), "width": 3, "height": 2})
        self.assertEqual(second["background"], "holiday (2).png")
        self.assertEqual((self.project.backgrounds / first["background"]).read_bytes(), payload)
        self.assertEqual((self.project.backgrounds / second["background"]).read_bytes(), payload)
        self.assertFalse(any(path.name.startswith(".upload-") for path in self.project.backgrounds.iterdir()))
        self.assertEqual(self.store.get()["landscape"], "bg.jpg")

        manifest = self.store.list_backgrounds()
        holiday = next(item for item in manifest["backgrounds"] if item["name"] == "holiday.png")
        self.assertIsInstance(holiday["thumbnail"], str)
        with backgrounds.Image.open(self.thumbnail_path(manifest, "holiday.png")) as thumbnail:
            self.assertEqual(thumbnail.format, "WEBP")
            self.assertEqual(thumbnail.size, (480, 270))

    def test_thumbnail_manifest_generates_reuses_and_orients_supported_images(self) -> None:
        self.write_real_backgrounds()
        (self.project.backgrounds / "phone.avif").write_bytes(image_bytes("AVIF"))

        oriented = backgrounds.Image.new("RGB", (40, 80), (180, 40, 60))
        exif = backgrounds.Image.Exif()
        exif[274] = 6
        oriented.save(self.project.backgrounds / "oriented.jpg", format="JPEG", exif=exif)
        oriented.close()

        with mock.patch.object(
            backgrounds.ImageOps,
            "exif_transpose",
            wraps=backgrounds.ImageOps.exif_transpose,
        ) as transpose, mock.patch.object(
            backgrounds.ImageOps,
            "fit",
            wraps=backgrounds.ImageOps.fit,
        ) as fit, mock.patch.object(backgrounds.os, "fsync", wraps=backgrounds.os.fsync) as fsync, mock.patch.object(
            backgrounds.os,
            "replace",
            wraps=backgrounds.os.replace,
        ) as replace:
            first = self.store.list_backgrounds()

        self.assertEqual(first["version"], 1)
        self.assertEqual(
            first["thumbnails"],
            {"generated": 6, "reused": 0, "failed": 0, "deferred": 0},
        )
        self.assertEqual(first["total"], 6)
        self.assertFalse(first["truncated"])
        self.assertEqual(first["backgrounds"][0]["name"], "bg.jpg")
        self.assertGreaterEqual(transpose.call_count, 6)
        self.assertGreaterEqual(fit.call_count, 6)
        for call in fit.call_args_list:
            self.assertEqual(call.args[1], (480, 270))
            self.assertEqual(call.kwargs["centering"], (0.5, 0.5))
        self.assertGreaterEqual(fsync.call_count, 6)
        for call in replace.call_args_list:
            temporary, destination = map(Path, call.args[:2])
            self.assertEqual(temporary.parent, destination.parent)
            self.assertEqual(temporary.suffix, ".tmp")
            self.assertEqual(destination.suffix, ".webp")
        for record in first["backgrounds"]:
            self.assertRegex(
                record["thumbnail"],
                r"^thumbnails/thumbnail-[0-9a-f]{64}-[0-9a-f]{64}\.webp\?v=\d+-\d+$",
            )
            with backgrounds.Image.open(self.thumbnail_path(first, record["name"])) as thumbnail:
                self.assertEqual((thumbnail.format, thumbnail.size), ("WEBP", (480, 270)))

        second = self.store.list_backgrounds()
        self.assertEqual(
            second["thumbnails"],
            {"generated": 0, "reused": 6, "failed": 0, "deferred": 0},
        )
        self.assertEqual(second["backgrounds"], first["backgrounds"])

    def test_thumbnail_manifest_invalidates_repairs_and_cleans_only_internal_orphans(self) -> None:
        self.write_real_backgrounds()
        first = self.store.list_backgrounds()
        original_cache = self.thumbnail_path(first, "bg.jpg")

        source = self.project.backgrounds / "bg.jpg"
        source.write_bytes(image_bytes("JPEG") + b"\n")
        source_stat = source.stat()
        os.utime(
            source,
            ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns + 1_000_000_000),
        )
        changed = self.store.list_backgrounds()
        changed_cache = self.thumbnail_path(changed, "bg.jpg")
        self.assertNotEqual(changed_cache, original_cache)
        self.assertFalse(original_cache.exists())

        changed_cache.write_bytes(b"damaged")
        repaired = self.store.list_backgrounds()
        self.assertGreaterEqual(repaired["thumbnails"]["generated"], 1)
        with backgrounds.Image.open(changed_cache) as thumbnail:
            self.assertEqual((thumbnail.format, thumbnail.size), ("WEBP", (480, 270)))

        orphan = self.store.thumbnail_dir / f"thumbnail-{'a' * 64}-{'b' * 64}.webp"
        unrelated = self.store.thumbnail_dir / "keep-me.txt"
        nested = self.store.thumbnail_dir / "keep-directory"
        orphan.write_bytes(b"orphan")
        unrelated.write_text("operator file", encoding="utf-8")
        nested.mkdir()
        self.store.list_backgrounds()
        self.assertFalse(orphan.exists())
        self.assertTrue(unrelated.is_file())
        self.assertTrue(nested.is_dir())

    def test_thumbnail_manifest_contains_single_image_failures_and_rejects_unsafe_cache_path(self) -> None:
        self.write_real_backgrounds()
        (self.project.backgrounds / "test.jpg").write_bytes(b"damaged")
        with self.assertLogs(core.SERVICE_NAME, level="WARNING"):
            partial = self.store.list_backgrounds()
        failed = next(item for item in partial["backgrounds"] if item["name"] == "test.jpg")
        self.assertIsNone(failed["thumbnail"])
        self.assertEqual(
            partial["thumbnails"],
            {"generated": 3, "reused": 0, "failed": 1, "deferred": 0},
        )

        shutil.rmtree(self.store.thumbnail_dir)
        outside = self.project.root / "outside-cache"
        outside.mkdir()
        try:
            os.symlink(outside, self.store.thumbnail_dir, target_is_directory=True)
        except OSError:
            self.store.thumbnail_dir.write_text("not a directory", encoding="utf-8")
        with self.assertLogs(core.SERVICE_NAME, level="WARNING"):
            unsafe = self.store.list_backgrounds()
        self.assertTrue(all(item["thumbnail"] is None for item in unsafe["backgrounds"]))
        self.assertEqual(unsafe["thumbnails"]["failed"], 4)
        self.assertEqual(list(outside.iterdir()), [])

        if self.store.thumbnail_dir.is_symlink() or self.store.thumbnail_dir.is_file():
            self.store.thumbnail_dir.unlink()
        else:
            shutil.rmtree(self.store.thumbnail_dir)
        self.store.thumbnail_dir.mkdir()
        with mock.patch.object(
            type(self.store.thumbnail_dir),
            "is_junction",
            return_value=True,
        ), self.assertLogs(core.SERVICE_NAME, level="WARNING"):
            junction = self.store.list_backgrounds()
        self.assertTrue(all(item["thumbnail"] is None for item in junction["backgrounds"]))
        self.assertEqual(junction["thumbnails"]["failed"], 4)

    def test_thumbnail_work_does_not_hold_the_state_lock(self) -> None:
        self.write_real_backgrounds()
        manifest_entered = threading.Event()
        release_manifest = threading.Event()
        mutation_started = threading.Event()
        mutation_finished = threading.Event()
        errors: list[BaseException] = []
        original_thumbnail = self.store._thumbnail_for_background_locked

        def blocking_thumbnail(
            background: str,
            *,
            allow_generate: bool = True,
        ) -> tuple[str | None, str]:
            if background == "bg.jpg":
                manifest_entered.set()
                if not release_manifest.wait(5):
                    raise TimeoutError("test did not release the thumbnail manifest")
            return original_thumbnail(background, allow_generate=allow_generate)

        def list_manifest() -> None:
            try:
                self.store.list_backgrounds()
            except BaseException as error:
                errors.append(error)

        def upload_during_manifest() -> None:
            mutation_started.set()
            try:
                payload = image_bytes()
                self.store.upload_background("queued.png", io.BytesIO(payload), len(payload))
            except BaseException as error:
                errors.append(error)
            finally:
                mutation_finished.set()

        with mock.patch.object(
            self.store,
            "_thumbnail_for_background_locked",
            side_effect=blocking_thumbnail,
        ):
            manifest_thread = threading.Thread(target=list_manifest)
            manifest_thread.start()
            self.assertTrue(manifest_entered.wait(5))

            self.assertEqual(self.store.get()["landscape"], "bg.jpg")
            updated = self.store.update("landscape", "test.jpg")
            self.assertEqual(updated["landscape"], "test.jpg")

            mutation_thread = threading.Thread(target=upload_during_manifest)
            mutation_thread.start()
            self.assertTrue(mutation_started.wait(5))
            deadline = time.monotonic() + 5
            while (
                not (self.project.backgrounds / "queued.png").is_file()
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            self.assertTrue((self.project.backgrounds / "queued.png").is_file())
            self.assertFalse(mutation_finished.wait(0.1))
            release_manifest.set()
            manifest_thread.join(10)
            mutation_thread.join(10)

        self.assertFalse(manifest_thread.is_alive())
        self.assertFalse(mutation_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue((self.project.backgrounds / "queued.png").is_file())

    def test_thumbnail_generation_is_bounded_and_deferred_work_resumes(self) -> None:
        for path in self.project.backgrounds.iterdir():
            path.unlink()
        payload = image_bytes("JPEG")
        for index in range(17):
            name = "bg.jpg" if index == 0 else f"background-{index:02d}.jpg"
            (self.project.backgrounds / name).write_bytes(payload)

        first = self.store.list_backgrounds()
        self.assertEqual(
            first["thumbnails"],
            {"generated": 16, "reused": 0, "failed": 0, "deferred": 1},
        )
        self.assertEqual(len(first["backgrounds"]), 17)
        self.assertEqual(
            sum(item["thumbnail"] is None for item in first["backgrounds"]),
            1,
        )

        second = self.store.list_backgrounds()
        self.assertEqual(
            second["thumbnails"],
            {"generated": 1, "reused": 16, "failed": 0, "deferred": 0},
        )

    def test_manifest_caps_results_and_preserves_selected_backgrounds(self) -> None:
        for index in range(509):
            (self.project.backgrounds / f"extra-{index:04d}.jpg").write_bytes(b"image")
        selected_landscape = "extra-0507.jpg"
        selected_portrait = "extra-0508.jpg"
        self.store.update("landscape", selected_landscape)
        self.store.update("portrait", selected_portrait)
        self.store.thumbnail_dir.mkdir()
        orphan = self.store.thumbnail_dir / f"thumbnail-{'a' * 64}-{'b' * 64}.webp"
        orphan.write_bytes(b"orphan")

        with mock.patch.object(
            self.store,
            "_thumbnail_for_background_locked",
            return_value=("thumbnails/existing.webp?v=1-1", "reused"),
        ):
            manifest = self.store.list_backgrounds()

        names = {item["name"] for item in manifest["backgrounds"]}
        self.assertEqual(len(manifest["backgrounds"]), backgrounds.MAX_BACKGROUND_FILES)
        self.assertEqual(manifest["total"], 513)
        self.assertTrue(manifest["truncated"])
        self.assertIn("bg.jpg", names)
        self.assertIn(selected_landscape, names)
        self.assertIn(selected_portrait, names)
        self.assertTrue(orphan.exists())

    def test_directory_scan_limit_fails_closed_and_skips_orphan_cleanup(self) -> None:
        for index in range(backgrounds.MAX_BACKGROUND_DIRECTORY_ENTRIES):
            (self.project.backgrounds / f"scan-{index:04d}.jpg").write_bytes(b"image")
        self.store.thumbnail_dir.mkdir()
        orphan = self.store.thumbnail_dir / f"thumbnail-{'c' * 64}-{'d' * 64}.webp"
        orphan.write_bytes(b"orphan")

        with mock.patch.object(
            self.store,
            "_thumbnail_for_background_locked",
            return_value=("thumbnails/existing.webp?v=1-1", "reused"),
        ):
            manifest = self.store.list_backgrounds()

        self.assertIsNone(manifest["total"])
        self.assertTrue(manifest["truncated"])
        self.assertEqual(len(manifest["backgrounds"]), backgrounds.MAX_BACKGROUND_FILES)
        self.assertTrue(orphan.exists())

    def test_concurrent_uploads_cannot_exceed_background_quota(self) -> None:
        for index in range(backgrounds.MAX_BACKGROUND_FILES - 4 - 1):
            (self.project.backgrounds / f"quota-{index:04d}.jpg").write_bytes(b"image")
        payload = image_bytes()
        results: list[dict[str, object]] = []
        errors: list[BaseException] = []

        def upload(name: str) -> None:
            try:
                results.append(self.store.upload_background(name, io.BytesIO(payload), len(payload)))
            except BaseException as error:
                errors.append(error)

        threads = [
            threading.Thread(target=upload, args=("last-a.png",)),
            threading.Thread(target=upload, args=("last-b.png",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], backgrounds.BackgroundQuotaExceeded)

    def test_thumbnail_failure_does_not_rollback_upload_and_removal_cleans_cache(self) -> None:
        payload = image_bytes()
        with mock.patch.object(
            self.store,
            "_thumbnail_for_background_locked",
            side_effect=OSError("cache unavailable"),
        ), self.assertLogs(core.SERVICE_NAME, level="WARNING"):
            uploaded = self.store.upload_background("survives.png", io.BytesIO(payload), len(payload))
        self.assertTrue((self.project.backgrounds / uploaded["background"]).is_file())

        self.write_real_backgrounds()
        manifest = self.store.list_backgrounds()
        cache = self.thumbnail_path(manifest, "test.jpg")
        with mock.patch.object(backgrounds, "send2trash", side_effect=OSError("trash unavailable")):
            with self.assertRaises(backgrounds.TrashUnavailable) as issued:
                self.store.remove_background("test.jpg")
        self.assertTrue(cache.is_file())
        self.store.remove_background("test.jpg", issued.exception.confirmation_token)
        self.assertFalse(cache.exists())

        restored = self.project.backgrounds / "test.jpg"
        restored.write_bytes(image_bytes("JPEG"))
        restored_manifest = self.store.list_backgrounds()
        restored_cache = self.thumbnail_path(restored_manifest, "test.jpg")
        self.assertTrue(restored_cache.is_file())

        def simulate_trash(path: str) -> None:
            Path(path).unlink()

        with mock.patch.object(backgrounds, "send2trash", side_effect=simulate_trash):
            disposition, _ = self.store.remove_background("test.jpg")
        self.assertEqual(disposition, "trash")
        self.assertFalse(restored_cache.exists())

    def test_upload_rejects_invalid_size_name_format_and_incomplete_body(self) -> None:
        payload = image_bytes()
        cases = (
            ("../holiday.png", io.BytesIO(payload), len(payload), backgrounds.InvalidBackground),
            ("holiday.png", io.BytesIO(payload), backgrounds.MAX_BACKGROUND_UPLOAD_BODY + 1, backgrounds.UploadTooLarge),
            ("holiday.jpg", io.BytesIO(payload), len(payload), backgrounds.InvalidImage),
            ("holiday.png", io.BytesIO(b"not an image"), 12, backgrounds.InvalidImage),
            ("holiday.png", io.BytesIO(payload[:-1]), len(payload), backgrounds.InvalidImage),
        )
        for name, stream, length, expected in cases:
            with self.subTest(name=name, expected=expected), self.assertRaises(expected):
                self.store.upload_background(name, stream, length)
        self.assertFalse(any(path.name.startswith(".upload-") for path in self.project.backgrounds.iterdir()))

    def test_upload_enforces_exact_100_megapixel_limit(self) -> None:
        class FakeImage:
            format = "PNG"

            def __init__(self, size: tuple[int, int]) -> None:
                self.size = size

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def verify(self) -> None:
                pass

            def load(self) -> None:
                pass

        at_limit = FakeImage((10_000, 10_000))

        def open_at_limit(path: Path):
            backgrounds.warnings.warn("Pillow threshold", backgrounds.Image.DecompressionBombWarning)
            return at_limit

        with mock.patch.object(backgrounds.Image, "open", side_effect=open_at_limit):
            self.assertEqual(self.store._validate_uploaded_image(Path("ignored"), ".png"), (10_000, 10_000))

        above_limit = FakeImage((10_001, 10_000))
        with mock.patch.object(backgrounds.Image, "open", return_value=above_limit):
            with self.assertRaisesRegex(backgrounds.InvalidImage, "dimensions"):
                self.store._validate_uploaded_image(Path("ignored"), ".png")

    def test_remove_uses_trash_and_repairs_every_referenced_mode(self) -> None:
        self.store.update("landscape", "test.jpg")
        self.store.update("portrait", "test.jpg")

        def simulate_trash(path: str) -> None:
            Path(path).unlink()

        with mock.patch.object(backgrounds, "send2trash", side_effect=simulate_trash) as send_to_trash:
            disposition, preferences = self.store.remove_background("test.jpg")

        self.assertEqual(disposition, "trash")
        self.assertEqual(preferences["landscape"], "bg.jpg")
        self.assertEqual(preferences["portrait"], "bg.jpg")
        self.assertFalse((self.project.backgrounds / "test.jpg").exists())
        send_to_trash.assert_called_once_with(str(self.project.backgrounds / "test.jpg"))
        self.assertEqual(json.loads(self.config.state_file.read_text(encoding="utf-8")), preferences)
        with self.assertRaises(backgrounds.ProtectedBackground):
            self.store.remove_background("bg.jpg")

    def test_remove_requires_bound_single_use_unexpired_token_for_permanent_delete(self) -> None:
        now = [100.0]
        store = backgrounds.StateStore(
            self.config.background_dir,
            self.config.state_file,
            lambda: FIXED_TIME,
            monotonic=lambda: now[0],
        )
        store.update("landscape", "test.jpg")

        with mock.patch.object(backgrounds, "send2trash", side_effect=OSError("trash unavailable")):
            with self.assertRaises(backgrounds.TrashUnavailable) as issued:
                store.remove_background("test.jpg")
        token = issued.exception.confirmation_token

        with self.assertRaises(backgrounds.InvalidConfirmationToken):
            store.remove_background("alternate.webp", token)
        self.assertTrue((self.project.backgrounds / "alternate.webp").exists())
        with self.assertRaises(backgrounds.InvalidConfirmationToken):
            store.remove_background("test.jpg", token)

        with mock.patch.object(backgrounds, "send2trash", side_effect=OSError("trash unavailable")):
            with self.assertRaises(backgrounds.TrashUnavailable) as issued:
                store.remove_background("test.jpg")
        token = issued.exception.confirmation_token
        disposition, preferences = store.remove_background("test.jpg", token)
        self.assertEqual(disposition, "permanent")
        self.assertEqual(preferences["landscape"], "bg.jpg")
        self.assertFalse((self.project.backgrounds / "test.jpg").exists())

        expired_payload = image_bytes("JPEG")
        (self.project.backgrounds / "expiring.jpg").write_bytes(expired_payload)
        with mock.patch.object(backgrounds, "send2trash", side_effect=OSError("trash unavailable")):
            with self.assertRaises(backgrounds.TrashUnavailable) as issued:
                store.remove_background("expiring.jpg")
        now[0] += backgrounds.REMOVAL_TOKEN_TTL_SECONDS + 1
        with self.assertRaises(backgrounds.InvalidConfirmationToken):
            store.remove_background("expiring.jpg", issued.exception.confirmation_token)
        self.assertTrue((self.project.backgrounds / "expiring.jpg").exists())

    def test_remove_token_store_is_bounded(self) -> None:
        with mock.patch.object(backgrounds, "send2trash", side_effect=OSError("trash unavailable")):
            for _ in range(backgrounds.MAX_REMOVAL_TOKENS + 5):
                with self.assertRaises(backgrounds.TrashUnavailable):
                    self.store.remove_background("test.jpg")
        self.assertEqual(len(self.store._removal_tokens), backgrounds.MAX_REMOVAL_TOKENS)
