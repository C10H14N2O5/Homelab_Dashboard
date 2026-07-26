from __future__ import annotations

import argparse
import io
import json
import os
import socket
import ssl
import sys
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parent))
import main  # noqa: E402


FIXED_TIME = "2026-07-22T12:00:00Z"
TEST_ORIGIN = "https://home.example.test"
TEST_CERTIFICATE_DER = "MIIDAzCCAeugAwIBAgIIULMcMQ0qRsIwDQYJKoZIhvcNAQELBQAwJDEiMCAGA1UEAxMZc2VydmljZS5ob21lLmV4YW1wbGUudGVzdDAeFw0yNjA3MjExNTQxNTJaFw0zNjA3MjIxNTQxNTJaMCQxIjAgBgNVBAMTGXNlcnZpY2UuaG9tZS5leGFtcGxlLnRlc3QwggEiMA0GCSqGSIb3DQEBAQUAA4IBDwAwggEKAoIBAQDSaxoBcAH00t96vPpMoOJ0Hya8zTPzWHbXox7lljL7FZ4TzLx0GmsG6LozdQ0Ghy3q175crgQbdSMvq/5mVp4M5Lid3bOj7ZQETQXrFgUjNp/tRmuGeLrLVw7lCNkD3UWTQ9JcZQLaJF1GTtrChW/kJL7FsK2LeZHDHN9p+NhXOCG4l1YKQqR88AtWLmz7Na4I0bkppD6IU9gK/G1usEyELueCbzrMLpV3jW9WBnobSGNp0cC3qpIxDNJiHOUJd2Qd14wt6go33YDoHhGXBuiCxipc9Hc/ckFhr+xMqsINhr5kg+Qq7wgkh+eue++/B21wCl+bsh8hUoSNV5bvXAx9AgMBAAGjOTA3MCQGA1UdEQQdMBuCGXNlcnZpY2UuaG9tZS5leGFtcGxlLnRlc3QwDwYDVR0TAQH/BAUwAwEB/zANBgkqhkiG9w0BAQsFAAOCAQEAJXd+c+xWObYBWDUZgETrapwNzSKHx6cp9mDyKM2jgG3D40i3jqP7PVIpqJUKs/2MrDTOQGsMnjiToVyuQJGq3nM7tVHjdkFqzeiA6zjlA0WSLiltv9WEmqWZIGiDMtkYJVW0uBeWl1vpNach3xQELguWMZiuxTVJ5P911RfkPsVroRPK1kpgDkoVS6MlzRGwOiULg/73usbN5FuaLQdZ511/oqZ2RmMNSNrnCoSBkIi0SqZIXgBRnlUZ+Pr5hUeH4SUThTMz/qdgPe2kBJLBgeJpvkJ8hCGE2vJmLTMqLpZIjf8FTyjSzdf+LRtBc6kEBTHQ32pIieOPLBZ8LXLaLw=="
TEST_PRIVATE_KEY_DER = "MIIEvwIBADANBgkqhkiG9w0BAQEFAASCBKkwggSlAgEAAoIBAQDSaxoBcAH00t96vPpMoOJ0Hya8zTPzWHbXox7lljL7FZ4TzLx0GmsG6LozdQ0Ghy3q175crgQbdSMvq/5mVp4M5Lid3bOj7ZQETQXrFgUjNp/tRmuGeLrLVw7lCNkD3UWTQ9JcZQLaJF1GTtrChW/kJL7FsK2LeZHDHN9p+NhXOCG4l1YKQqR88AtWLmz7Na4I0bkppD6IU9gK/G1usEyELueCbzrMLpV3jW9WBnobSGNp0cC3qpIxDNJiHOUJd2Qd14wt6go33YDoHhGXBuiCxipc9Hc/ckFhr+xMqsINhr5kg+Qq7wgkh+eue++/B21wCl+bsh8hUoSNV5bvXAx9AgMBAAECggEBAKLxKRw/3mP1//UN7k30sCf4D4lY5ofk03K88KodG7Fv1g/DFZRa+NJK2hYK6GFLwPUAyHsAjlGs7eh2qfQZ0x5s58Hn8rpYuaxy8MhRLv1LyaB2TcG+L/hSnEgkB9mEOeKGPJor5n+CdXCCk8jaiAEFq21vGZTOOV/8Nc3cU7uFZkkFcMwLStX4TRJnL7pBkBiaw384N080h7mgdRZnif3szzCt/HCFxUkfT0t/xfC9VcM+6IvkBxkc5VEyH1mGSNQzcFG0EYmZjsrWUYmBO9+DmKrv2O9hbrSuA3VKkQZTypJXvalsNBFprMhyBHtAGevY0NiRVVpYD5iHDwgRxskCgYEA83c/Ja8dOufMvMkyBAe3ffm10301wX/Sopb/nD3i7AZxvDmvFh4mTHBOfz6TWC9LZaQ2hMN1DrwS+EuhC02LvNMUWNNEtRBZlX5F8KSJjl4mIhZIRI1G3Hw7Q5peEfCnWVck19hgHxJcQFG+1K9ib5ucT6B5PYpehdTyCh1UsdsCgYEA3UBOhl1TKP0Qzx3/zJHJqMd5ntInko4mb7RKtUqGc5oQWsjCf5YUr2no+F7NL9JJNWsG5DVwgc6F5pEqMz4WHWr21PBE2TJQk4yarT28tkyeic1DU7MtMIFaL4PPk6XsUFRVWlenWQbh8egmZX+/MiuKuIkt0HiHVIqpU/cPZocCgYEAsbBrVv6KT86Tn4kbxtM2+WtBaBwvGo1G1sFrxsVoE6jUY3KaNYNFom4o7DdoZGkIFyOVYqogieN72NmhrNUo0mQfqoBu2dMX/sJNG0eK2WwfWqt2jQmrGQpWWo2UcHV6Pfsa+amC5cqJp6vBogpqxlgE6hyNv/t35yc2i2lCwhcCgYEAyQrpD6D3stQZlQo+aQXER3BSrCVsPfibJn+eLxP0DlFdMREh40tdqnNDdYS3UA/14+tHzNBeViVyeKtJvFxbPgn3FlDpTq8wlpJ57pedxczjdnYlCoZStrOjXm+YjCKGFAw9rqTddMETSHxZf3tQIed82ugT0E3lMsX4zUo/HTsCgYBv6HjiGFqy47F6i2KnCE8UD2fQ3QvPKct4gYac9lpZ4A7CbyrIxfHYIQMFpLalHJhxZWtIIJSq8P/+/MTnIOcsjo/30+nRu1Zc7Oq2bywBDBau3JgNfJC7JF1G9nu6PbPzORGfh/mcwz/Qemh2UffsjUYpGuF+hbR27Ezq+HSABA=="


def pem(label: str, der_base64: str) -> str:
    lines = [der_base64[index : index + 64] for index in range(0, len(der_base64), 64)]
    return f"-----BEGIN {label}-----\n" + "\n".join(lines) + f"\n-----END {label}-----\n"


TEST_CERTIFICATE_PEM = pem("CERTIFICATE", TEST_CERTIFICATE_DER)
TEST_PRIVATE_KEY_PEM = pem("PRIVATE KEY", TEST_PRIVATE_KEY_DER)


def image_bytes(image_format: str = "PNG") -> bytes:
    output = io.BytesIO()
    main.Image.new("RGB", (3, 2), (35, 110, 180)).save(output, format=image_format)
    return output.getvalue()


class FakeBestdoriClient:
    def __init__(self) -> None:
        self.characters = [
            {"id": 1, "name": "户山香澄"},
            {"id": 2, "name": "花园多惠"},
        ]
        self.cards = [
            {
                "id": 101,
                "characterId": 1,
                "rarity": 3,
                "resourceSetName": "card101",
                "title": "星之鼓动",
                "server": "cn",
            },
            {
                "id": 102,
                "characterId": 2,
                "rarity": 5,
                "resourceSetName": "card102",
                "title": "闪耀舞台",
                "server": "jp",
            },
        ]
        self.download_started: threading.Event | None = None
        self.download_continue: threading.Event | None = None

    def get_characters(self) -> list[dict[str, object]]:
        return [dict(character) for character in self.characters]

    def get_cards(self) -> list[dict[str, object]]:
        return [dict(card) for card in self.cards]

    def download_card(self, card: dict[str, object]) -> tuple[bytes, str]:
        if self.download_started is not None:
            self.download_started.set()
        if self.download_continue is not None:
            self.download_continue.wait(timeout=2)
        return image_bytes(), "after_training"


class TemporaryProject:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.backgrounds = self.root / "web" / "html" / "backgrounds"
        self.backgrounds.mkdir(parents=True)
        for name in ("bg.jpg", "test.jpg", "portrait.png", "alternate.webp"):
            (self.backgrounds / name).write_bytes(b"image")
        self.state_dir = self.root / "HomeLabWallpaperState-data"
        self.config_path = self.root / main.DEFAULT_CONFIG_NAME

    def close(self) -> None:
        self.temporary.cleanup()

    def write_config(self, **overrides: object) -> main.AppConfig:
        value = {
            "version": 2,
            "backgroundDir": r"web\html\backgrounds",
            "allowedOrigins": [TEST_ORIGIN],
            "listen": "127.0.0.1:8787",
            "stateDir": main.DEFAULT_DATA_DIRECTORY,
            "controlToken": "x" * 43,
            "probeConnectAddress": "127.0.0.1:443",
            "probeDomainSuffix": "home.example.test",
            "probeTimeoutSeconds": 5,
        }
        value.update(overrides)
        main.atomic_write_json(self.config_path, value)
        return main.load_config(self.config_path)


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = TemporaryProject()

    def tearDown(self) -> None:
        self.project.close()

    def test_interactive_defaults(self) -> None:
        self.assertEqual(main.DEFAULT_BACKGROUND_DIRECTORY, r"D:\Zoraxy\www\html\backgrounds")
        self.assertEqual(main.MONITOR_REFRESH_SECONDS, 60.0)

    def test_prompt_value_strips_redirected_utf8_bom(self) -> None:
        with mock.patch("builtins.input", return_value="\ufeffD:\\wallpapers  "):
            self.assertEqual(main.prompt_value("背景目录", "default"), r"D:\wallpapers")

    def test_wizard_preserves_existing_background_default(self) -> None:
        existing = self.project.write_config()
        prompts: list[tuple[str, str]] = []

        def accept_default(label: str, default: str) -> str:
            prompts.append((label, default))
            return default

        with mock.patch.object(main, "prompt_value", side_effect=accept_default), mock.patch.object(
            main, "prompt_yes_no", return_value=False
        ), mock.patch.object(main.sys, "frozen", False, create=True), mock.patch.object(
            main, "validate_application_location"
        ) as validate_location, mock.patch("builtins.print"):
            configured = main.run_configuration_wizard(self.project.config_path)

        self.assertEqual(prompts[0], ("背景目录", r"web\html\backgrounds"))
        self.assertEqual(configured.background_dir, existing.background_dir)
        validate_location.assert_called_once_with(configured)

    def test_load_config_resolves_relative_paths(self) -> None:
        config = self.project.write_config()
        self.assertEqual(config.background_dir, self.project.backgrounds.resolve())
        self.assertEqual(config.state_dir, self.project.state_dir.resolve())
        self.assertEqual(config.listen, "127.0.0.1:8787")
        self.assertEqual(config.allowed_origins, frozenset({TEST_ORIGIN}))
        self.assertEqual(config.probe_connect_address, "127.0.0.1:443")
        self.assertEqual(config.probe_domain_suffix, "home.example.test")
        self.assertEqual(config.probe_timeout_seconds, 5.0)

    def test_rejects_unsafe_listen_and_public_state(self) -> None:
        for overrides in (
            {"listen": "0.0.0.0:8787"},
            {"stateDir": r"web\html\runtime"},
            {"allowedOrigins": ["https://example.test/path"]},
            {"probeConnectAddress": "192.0.2.1:443"},
            {"probeConnectAddress": "localhost:443"},
            {"probeDomainSuffix": "*.example.test"},
            {"probeTimeoutSeconds": 6},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(main.ConfigurationError):
                    self.project.write_config(**overrides)

    def test_config_v1_is_rejected(self) -> None:
        with self.assertRaisesRegex(main.ConfigurationError, "unsupported configuration version"):
            self.project.write_config(version=1)

    def test_rejects_missing_default_background(self) -> None:
        (self.project.backgrounds / "bg.jpg").unlink()
        with self.assertRaises(main.ConfigurationError):
            self.project.write_config()

    def test_application_directory_must_be_outside_public_web_root(self) -> None:
        config = self.project.write_config()
        with mock.patch.object(main, "application_directory", return_value=self.project.root):
            main.validate_application_location(config)

        for application_dir in (self.project.backgrounds.parent, self.project.backgrounds):
            with self.subTest(application_dir=application_dir), mock.patch.object(
                main, "application_directory", return_value=application_dir
            ):
                with self.assertRaisesRegex(main.ConfigurationError, "application directory"):
                    main.validate_application_location(config)


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = TemporaryProject()
        self.config = self.project.write_config()
        self.store = main.StateStore(self.config.background_dir, self.config.state_file, lambda: FIXED_TIME)

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
        reloaded = main.StateStore(self.config.background_dir, self.config.state_file, lambda: FIXED_TIME)
        self.assertEqual(reloaded.get(), result)

    def test_validation(self) -> None:
        for name, error in (
            ("../test.jpg", main.InvalidBackground),
            ("test.svg", main.InvalidBackground),
            ("missing.jpg", main.BackgroundMissing),
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
        with main.Image.open(self.thumbnail_path(manifest, "holiday.png")) as thumbnail:
            self.assertEqual(thumbnail.format, "WEBP")
            self.assertEqual(thumbnail.size, (480, 270))

    def test_thumbnail_manifest_generates_reuses_and_orients_supported_images(self) -> None:
        self.write_real_backgrounds()
        (self.project.backgrounds / "phone.avif").write_bytes(image_bytes("AVIF"))

        oriented = main.Image.new("RGB", (40, 80), (180, 40, 60))
        exif = main.Image.Exif()
        exif[274] = 6
        oriented.save(self.project.backgrounds / "oriented.jpg", format="JPEG", exif=exif)
        oriented.close()

        with mock.patch.object(
            main.ImageOps,
            "exif_transpose",
            wraps=main.ImageOps.exif_transpose,
        ) as transpose, mock.patch.object(
            main.ImageOps,
            "fit",
            wraps=main.ImageOps.fit,
        ) as fit, mock.patch.object(main.os, "fsync", wraps=main.os.fsync) as fsync, mock.patch.object(
            main.os,
            "replace",
            wraps=main.os.replace,
        ) as replace:
            first = self.store.list_backgrounds()

        self.assertEqual(first["version"], 1)
        self.assertEqual(first["thumbnails"], {"generated": 6, "reused": 0, "failed": 0})
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
            with main.Image.open(self.thumbnail_path(first, record["name"])) as thumbnail:
                self.assertEqual((thumbnail.format, thumbnail.size), ("WEBP", (480, 270)))

        second = self.store.list_backgrounds()
        self.assertEqual(second["thumbnails"], {"generated": 0, "reused": 6, "failed": 0})
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
        with main.Image.open(changed_cache) as thumbnail:
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
        with self.assertLogs(main.SERVICE_NAME, level="WARNING"):
            partial = self.store.list_backgrounds()
        failed = next(item for item in partial["backgrounds"] if item["name"] == "test.jpg")
        self.assertIsNone(failed["thumbnail"])
        self.assertEqual(partial["thumbnails"], {"generated": 3, "reused": 0, "failed": 1})

        main.shutil.rmtree(self.store.thumbnail_dir)
        outside = self.project.root / "outside-cache"
        outside.mkdir()
        try:
            os.symlink(outside, self.store.thumbnail_dir, target_is_directory=True)
        except OSError:
            self.store.thumbnail_dir.write_text("not a directory", encoding="utf-8")
        with self.assertLogs(main.SERVICE_NAME, level="WARNING"):
            unsafe = self.store.list_backgrounds()
        self.assertTrue(all(item["thumbnail"] is None for item in unsafe["backgrounds"]))
        self.assertEqual(unsafe["thumbnails"]["failed"], 4)
        self.assertEqual(list(outside.iterdir()), [])

        if self.store.thumbnail_dir.is_symlink() or self.store.thumbnail_dir.is_file():
            self.store.thumbnail_dir.unlink()
        else:
            main.shutil.rmtree(self.store.thumbnail_dir)
        self.store.thumbnail_dir.mkdir()
        with mock.patch.object(
            type(self.store.thumbnail_dir),
            "is_junction",
            return_value=True,
        ), self.assertLogs(main.SERVICE_NAME, level="WARNING"):
            junction = self.store.list_backgrounds()
        self.assertTrue(all(item["thumbnail"] is None for item in junction["backgrounds"]))
        self.assertEqual(junction["thumbnails"]["failed"], 4)

    def test_thumbnail_manifest_serializes_with_background_mutations(self) -> None:
        self.write_real_backgrounds()
        manifest_entered = threading.Event()
        release_manifest = threading.Event()
        mutation_started = threading.Event()
        mutation_finished = threading.Event()
        errors: list[BaseException] = []
        original_thumbnail = self.store._thumbnail_for_background_locked

        def blocking_thumbnail(background: str) -> tuple[str, str]:
            if background == "bg.jpg":
                manifest_entered.set()
                if not release_manifest.wait(5):
                    raise TimeoutError("test did not release the thumbnail manifest")
            return original_thumbnail(background)

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
            mutation_thread = threading.Thread(target=upload_during_manifest)
            mutation_thread.start()
            self.assertTrue(mutation_started.wait(5))
            self.assertFalse(mutation_finished.wait(0.1))
            release_manifest.set()
            manifest_thread.join(10)
            mutation_thread.join(10)

        self.assertFalse(manifest_thread.is_alive())
        self.assertFalse(mutation_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue((self.project.backgrounds / "queued.png").is_file())

    def test_thumbnail_failure_does_not_rollback_upload_and_removal_cleans_cache(self) -> None:
        payload = image_bytes()
        with mock.patch.object(
            self.store,
            "_thumbnail_for_background_locked",
            side_effect=OSError("cache unavailable"),
        ), self.assertLogs(main.SERVICE_NAME, level="WARNING"):
            uploaded = self.store.upload_background("survives.png", io.BytesIO(payload), len(payload))
        self.assertTrue((self.project.backgrounds / uploaded["background"]).is_file())

        self.write_real_backgrounds()
        manifest = self.store.list_backgrounds()
        cache = self.thumbnail_path(manifest, "test.jpg")
        with mock.patch.object(main, "send2trash", side_effect=OSError("trash unavailable")):
            with self.assertRaises(main.TrashUnavailable) as issued:
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

        with mock.patch.object(main, "send2trash", side_effect=simulate_trash):
            disposition, _ = self.store.remove_background("test.jpg")
        self.assertEqual(disposition, "trash")
        self.assertFalse(restored_cache.exists())

    def test_upload_rejects_invalid_size_name_format_and_incomplete_body(self) -> None:
        payload = image_bytes()
        cases = (
            ("../holiday.png", io.BytesIO(payload), len(payload), main.InvalidBackground),
            ("holiday.png", io.BytesIO(payload), main.MAX_BACKGROUND_UPLOAD_BODY + 1, main.UploadTooLarge),
            ("holiday.jpg", io.BytesIO(payload), len(payload), main.InvalidImage),
            ("holiday.png", io.BytesIO(b"not an image"), 12, main.InvalidImage),
            ("holiday.png", io.BytesIO(payload[:-1]), len(payload), main.InvalidImage),
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
            main.warnings.warn("Pillow threshold", main.Image.DecompressionBombWarning)
            return at_limit

        with mock.patch.object(main.Image, "open", side_effect=open_at_limit):
            self.assertEqual(self.store._validate_uploaded_image(Path("ignored"), ".png"), (10_000, 10_000))

        above_limit = FakeImage((10_001, 10_000))
        with mock.patch.object(main.Image, "open", return_value=above_limit):
            with self.assertRaisesRegex(main.InvalidImage, "dimensions"):
                self.store._validate_uploaded_image(Path("ignored"), ".png")

    def test_remove_uses_trash_and_repairs_every_referenced_mode(self) -> None:
        self.store.update("landscape", "test.jpg")
        self.store.update("portrait", "test.jpg")

        def simulate_trash(path: str) -> None:
            Path(path).unlink()

        with mock.patch.object(main, "send2trash", side_effect=simulate_trash) as send_to_trash:
            disposition, preferences = self.store.remove_background("test.jpg")

        self.assertEqual(disposition, "trash")
        self.assertEqual(preferences["landscape"], "bg.jpg")
        self.assertEqual(preferences["portrait"], "bg.jpg")
        self.assertFalse((self.project.backgrounds / "test.jpg").exists())
        send_to_trash.assert_called_once_with(str(self.project.backgrounds / "test.jpg"))
        self.assertEqual(json.loads(self.config.state_file.read_text(encoding="utf-8")), preferences)
        with self.assertRaises(main.ProtectedBackground):
            self.store.remove_background("bg.jpg")

    def test_remove_requires_bound_single_use_unexpired_token_for_permanent_delete(self) -> None:
        now = [100.0]
        store = main.StateStore(
            self.config.background_dir,
            self.config.state_file,
            lambda: FIXED_TIME,
            monotonic=lambda: now[0],
        )
        store.update("landscape", "test.jpg")

        with mock.patch.object(main, "send2trash", side_effect=OSError("trash unavailable")):
            with self.assertRaises(main.TrashUnavailable) as issued:
                store.remove_background("test.jpg")
        token = issued.exception.confirmation_token

        with self.assertRaises(main.InvalidConfirmationToken):
            store.remove_background("alternate.webp", token)
        self.assertTrue((self.project.backgrounds / "alternate.webp").exists())
        with self.assertRaises(main.InvalidConfirmationToken):
            store.remove_background("test.jpg", token)

        with mock.patch.object(main, "send2trash", side_effect=OSError("trash unavailable")):
            with self.assertRaises(main.TrashUnavailable) as issued:
                store.remove_background("test.jpg")
        token = issued.exception.confirmation_token
        disposition, preferences = store.remove_background("test.jpg", token)
        self.assertEqual(disposition, "permanent")
        self.assertEqual(preferences["landscape"], "bg.jpg")
        self.assertFalse((self.project.backgrounds / "test.jpg").exists())

        expired_payload = image_bytes("JPEG")
        (self.project.backgrounds / "expiring.jpg").write_bytes(expired_payload)
        with mock.patch.object(main, "send2trash", side_effect=OSError("trash unavailable")):
            with self.assertRaises(main.TrashUnavailable) as issued:
                store.remove_background("expiring.jpg")
        now[0] += main.REMOVAL_TOKEN_TTL_SECONDS + 1
        with self.assertRaises(main.InvalidConfirmationToken):
            store.remove_background("expiring.jpg", issued.exception.confirmation_token)
        self.assertTrue((self.project.backgrounds / "expiring.jpg").exists())

    def test_remove_token_store_is_bounded(self) -> None:
        with mock.patch.object(main, "send2trash", side_effect=OSError("trash unavailable")):
            for _ in range(main.MAX_REMOVAL_TOKENS + 5):
                with self.assertRaises(main.TrashUnavailable):
                    self.store.remove_background("test.jpg")
        self.assertEqual(len(self.store._removal_tokens), main.MAX_REMOVAL_TOKENS)


class BestdoriClientTests(unittest.TestCase):
    def test_catalog_filters_supported_characters_rarities_and_release_server(self) -> None:
        client = main.BestdoriClient(opener=mock.Mock())
        characters = {
            str(character_id): {"characterName": [f"JP {character_id}", "", "", f"中文 {character_id}"]}
            for character_id in range(1, 41)
        }
        cards = {
            "101": {
                "characterId": 1,
                "rarity": 3,
                "resourceSetName": "card101",
                "prefix": ["日文", "", "", "中文标题"],
                "releasedAt": [None, None, None, 123, None],
            },
            "102": {
                "characterId": 41,
                "rarity": 5,
                "resourceSetName": "new-band",
                "prefix": ["unsupported"],
                "releasedAt": [123],
            },
            "103": {
                "characterId": 1,
                "rarity": 2,
                "resourceSetName": "low-rarity",
                "prefix": ["unsupported"],
                "releasedAt": [123],
            },
            "104": {
                "characterId": 1,
                "rarity": 5,
                "resourceSetName": "../unsafe",
                "prefix": ["unsafe"],
                "releasedAt": [123],
            },
            "105": {
                "characterId": 2,
                "rarity": 4,
                "resourceSetName": "unreleased",
                "prefix": ["unreleased"],
                "releasedAt": [None, None, None, None, None],
            },
        }
        with mock.patch.object(client, "_read_json", side_effect=[characters, cards]):
            parsed_characters = client.get_characters()
            parsed_cards = client.get_cards()
        self.assertEqual(len(parsed_characters), 40)
        self.assertEqual(parsed_characters[0], {"id": 1, "name": "中文 1"})
        self.assertEqual(
            parsed_cards,
            [{
                "id": 101,
                "characterId": 1,
                "rarity": 3,
                "resourceSetName": "card101",
                "title": "中文标题",
                "server": "cn",
            }],
        )

    def test_card_download_falls_back_from_after_training_404_to_normal(self) -> None:
        client = main.BestdoriClient(opener=mock.Mock())
        card = {"server": "jp", "resourceSetName": "card101"}
        payload = image_bytes()
        with mock.patch.object(
            client,
            "_read",
            side_effect=[main.BestdoriAssetMissing("missing"), payload],
        ) as read:
            downloaded, art = client.download_card(card)
        self.assertEqual(downloaded, payload)
        self.assertEqual(art, "normal")
        self.assertIn("card_after_training.png", read.call_args_list[0].args[0])
        self.assertIn("card_normal.png", read.call_args_list[1].args[0])

    def test_fixed_https_origin_and_response_size_are_enforced(self) -> None:
        client = main.BestdoriClient(opener=mock.Mock())
        for url in (
            "http://bestdori.com/api/cards/all.5.json",
            "https://evil.example/api/cards/all.5.json",
            "https://bestdori.com.evil.example/api/cards/all.5.json",
            "https://bestdori.com:444/api/cards/all.5.json",
        ):
            with self.subTest(url=url), self.assertRaises(main.BestdoriUnavailable):
                client._validate_url(url)

        response = mock.MagicMock()
        response.geturl.return_value = main.BESTDORI_CHARACTERS_URL
        response.headers.get_content_type.return_value = "application/json"
        response.headers.get.return_value = str(main.MAX_BESTDORI_CATALOG_BODY + 1)
        response.__enter__.return_value = response
        client.opener.open.return_value = response
        with self.assertRaisesRegex(main.BestdoriUnavailable, "too large"):
            client._read_json(main.BESTDORI_CHARACTERS_URL)
        self.assertTrue(any(isinstance(handler, main._RejectBestdoriRedirects) for handler in main.BestdoriClient().opener.handlers))


class BestdoriDrawTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = TemporaryProject()
        self.config = self.project.write_config()
        self.store = main.StateStore(self.config.background_dir, self.config.state_file, lambda: FIXED_TIME)
        self.client = FakeBestdoriClient()
        self.history_path = self.config.state_dir / "BestdoriCardDraws.json"
        self.history = main.BestdoriHistoryStore(self.history_path, lambda: FIXED_TIME)
        self.manager = main.BestdoriDrawManager(
            self.store,
            self.history_path,
            client=self.client,
            history=self.history,
            choice=lambda values: values[0],
        )

    def tearDown(self) -> None:
        self.project.close()

    def test_character_draw_publishes_records_and_permanently_deduplicates(self) -> None:
        result = self.manager.draw("character", {3, 5}, 1)
        self.assertEqual(result["card"]["id"], 101)
        self.assertEqual(result["card"]["characterName"], "户山香澄")
        self.assertEqual(result["card"]["art"], "after_training")
        filename = result["background"]["background"]
        self.assertIn("户山香澄_三星_星之鼓动_101_after_training.png", filename)
        self.assertTrue((self.project.backgrounds / filename).is_file())
        history = json.loads(self.history_path.read_text(encoding="utf-8"))
        self.assertEqual(history["version"], 1)
        self.assertEqual(history["records"]["101"]["filename"], filename)
        self.assertEqual(
            history["records"]["101"]["sha256"],
            main.hashlib.sha256((self.project.backgrounds / filename).read_bytes()).hexdigest(),
        )
        with self.assertRaises(main.BestdoriPoolExhausted):
            self.manager.draw("character", {3}, 1)

        second = self.manager.draw("random", {5})
        self.assertEqual(second["card"]["id"], 102)
        with self.assertRaises(main.BestdoriPoolExhausted):
            self.manager.draw("random", {3, 5})

    def test_random_draw_survives_character_name_catalog_failure(self) -> None:
        with mock.patch.object(
            self.client,
            "get_characters",
            side_effect=main.BestdoriUnavailable("character catalog unavailable"),
        ):
            result = self.manager.draw("random", {3})

        self.assertEqual(result["card"]["characterName"], "角色 1")
        filename = result["background"]["background"]
        self.assertIn("角色 1_三星_星之鼓动_101_after_training.png", filename)
        history = json.loads(self.history_path.read_text(encoding="utf-8"))
        self.assertEqual(history["records"]["101"]["card"]["characterName"], "角色 1")

    def test_reconcile_removes_missing_and_hash_mismatched_records_without_deleting_images(self) -> None:
        first = self.manager.draw("character", {3}, 1)
        second = self.manager.draw("character", {5}, 2)
        first_path = self.project.backgrounds / first["background"]["background"]
        second_path = self.project.backgrounds / second["background"]["background"]
        first_path.write_bytes(b"changed")
        second_path.unlink()

        result = self.manager.reconcile()
        self.assertEqual(result, {"removed": 2, "remaining": 0})
        self.assertEqual(first_path.read_bytes(), b"changed")
        self.assertFalse(second_path.exists())

    def test_reset_clears_records_but_keeps_downloaded_background(self) -> None:
        drawn = self.manager.draw("random", {3})
        path = self.project.backgrounds / drawn["background"]["background"]
        self.assertEqual(self.manager.reset(), {"removed": 1, "remaining": 0})
        self.assertTrue(path.is_file())
        self.assertEqual(json.loads(self.history_path.read_text(encoding="utf-8"))["records"], {})

    def test_corrupt_history_fails_closed_without_affecting_preferences(self) -> None:
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        self.history_path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(main.BestdoriHistoryError):
            self.manager.draw("random", {3, 5})
        with self.assertRaises(main.BestdoriHistoryError):
            self.manager.reconcile()
        with self.assertRaises(main.BestdoriHistoryError):
            self.manager.reset()
        self.assertEqual(self.store.get()["landscape"], "bg.jpg")

    def test_operation_lock_rejects_concurrent_draw_and_maintenance(self) -> None:
        self.client.download_started = threading.Event()
        self.client.download_continue = threading.Event()
        errors: list[Exception] = []

        def draw() -> None:
            try:
                self.manager.draw("random", {3})
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=draw)
        thread.start()
        self.assertTrue(self.client.download_started.wait(timeout=1))
        with self.assertRaises(main.BestdoriDrawBusy):
            self.manager.draw("random", {5})
        with self.assertRaises(main.BestdoriDrawBusy):
            self.manager.reconcile()
        with self.assertRaises(main.BestdoriDrawBusy):
            self.manager.reset()
        self.client.download_continue.set()
        thread.join(timeout=2)
        self.assertEqual(errors, [])

    def test_history_write_failure_rolls_back_published_image(self) -> None:
        failing_history = mock.Mock()
        failing_history.used_card_ids.return_value = set()
        failing_history.record.side_effect = main.BestdoriHistoryError("disk full")
        manager = main.BestdoriDrawManager(
            self.store,
            self.history_path,
            client=self.client,
            history=failing_history,
            choice=lambda values: values[0],
        )
        before = {path.name for path in self.project.backgrounds.iterdir() if path.is_file()}
        with self.assertRaises(main.BestdoriHistoryError):
            manager.draw("random", {3})
        self.assertEqual(
            {path.name for path in self.project.backgrounds.iterdir() if path.is_file()},
            before,
        )
        self.assertEqual(list(self.store.thumbnail_dir.iterdir()), [])


class FakeResponse:
    def __init__(self, status: int, location: str | None = None) -> None:
        self.status = status
        self.location = location
        self.closed = False

    def getheader(self, name: str) -> str | None:
        return self.location if name == "Location" else None

    def close(self) -> None:
        self.closed = True


class ProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = TemporaryProject()
        self.config = self.project.write_config()
        self.clients: list[main.ProbeClient] = []

    def tearDown(self) -> None:
        for client in self.clients:
            client.close()
        self.project.close()

    def make_client(self, connection_factory) -> main.ProbeClient:
        client = main.ProbeClient(self.config, connection_factory=connection_factory)
        self.clients.append(client)
        return client

    def test_target_validation_rejects_unsafe_shapes(self) -> None:
        valid = (
            "https://service.home.example.test/",
            "https://service.home.example.test:443/path?token=not-logged",
        )
        invalid = (
            "http://service.home.example.test/",
            "https://service.home.example.test:444/",
            "https://user:pass@service.home.example.test/",
            "https://service.home.example.test/#fragment",
            "https://service.home.example.test/#",
            "https://127.0.0.1/",
            "https://home.example.test/",
            "https://deep.service.home.example.test/",
            "https://service.example.test/",
        )
        for target in valid:
            with self.subTest(target=target):
                self.assertIsNotNone(main.validate_probe_target(target, self.config.probe_domain_suffix))
        for target in invalid:
            with self.subTest(target=target):
                self.assertIsNone(main.validate_probe_target(target, self.config.probe_domain_suffix))

    def test_pinned_connection_uses_loopback_and_target_sni(self) -> None:
        raw_socket = mock.Mock()
        wrapped_socket = mock.Mock()
        context = mock.Mock()
        context.wrap_socket.return_value = wrapped_socket
        connection = main.PinnedHTTPSConnection(
            "service.home.example.test",
            "127.0.0.1",
            443,
            2.0,
            context,
        )
        with mock.patch.object(main.socket, "create_connection", return_value=raw_socket) as create_connection:
            connection.connect()
        create_connection.assert_called_once_with(("127.0.0.1", 443), 2.0, None)
        context.wrap_socket.assert_called_once_with(raw_socket, server_hostname="service.home.example.test")
        self.assertIs(connection.sock, wrapped_socket)

    def test_real_tls_probe_connects_to_loopback_with_target_host_and_sni(self) -> None:
        certificate = self.project.root / "test-certificate.pem"
        private_key = self.project.root / "test-private-key.pem"
        certificate.write_text(TEST_CERTIFICATE_PEM, encoding="ascii")
        private_key.write_text(TEST_PRIVATE_KEY_PEM, encoding="ascii")
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        observed: dict[str, str] = {}
        errors: list[Exception] = []
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.load_cert_chain(certificate, private_key)

        def record_sni(_socket, server_name, _context) -> None:
            observed["sni"] = server_name

        server_context.set_servername_callback(record_sni)

        def serve_once() -> None:
            try:
                connection, _address = listener.accept()
                with server_context.wrap_socket(connection, server_side=True) as secured:
                    request = b""
                    while b"\r\n\r\n" not in request:
                        request += secured.recv(4096)
                    observed["request"] = request.decode("ascii")
                    secured.sendall(b"HTTP/1.1 521 Web Server Is Down\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=serve_once, daemon=True)
        thread.start()
        config = self.project.write_config(probeConnectAddress=f"127.0.0.1:{port}")
        client_context = ssl.create_default_context(cadata=TEST_CERTIFICATE_PEM)
        client = main.ProbeClient(config, ssl_context=client_context)
        self.clients.append(client)
        try:
            result = client.probe_one("https://service.home.example.test/health")
        finally:
            listener.close()
            thread.join(timeout=2)

        self.assertEqual(errors, [])
        self.assertEqual(result.status, 521)
        self.assertIsNone(result.error)
        self.assertEqual(observed["sni"], "service.home.example.test")
        self.assertIn("GET /health HTTP/1.1\r\n", observed["request"])
        self.assertIn("Host: service.home.example.test\r\n", observed["request"])

    def test_probe_preserves_host_and_returns_http_status(self) -> None:
        observed: dict[str, object] = {}
        response = FakeResponse(521)

        class Connection:
            def __init__(self, target_host, connect_host, connect_port, timeout, context) -> None:
                observed["constructor"] = (target_host, connect_host, connect_port, timeout)

            def request(self, method, path, headers) -> None:
                observed["request"] = (method, path, headers)

            def getresponse(self):
                return response

            def close(self) -> None:
                observed["closed"] = True

        client = self.make_client(Connection)
        result = client.probe_one("https://service.home.example.test/path?safe=1")
        self.assertEqual(result.status, 521)
        self.assertIsNone(result.error)
        self.assertEqual(observed["constructor"][:3], ("service.home.example.test", "127.0.0.1", 443))
        method, path, headers = observed["request"]
        self.assertEqual((method, path), ("GET", "/path?safe=1"))
        self.assertEqual(headers["Host"], "service.home.example.test")
        self.assertTrue(response.closed)
        self.assertTrue(observed["closed"])

    def test_redirect_rejection_preserves_last_status(self) -> None:
        response = FakeResponse(302, "https://outside.example.test/")
        connection = mock.Mock()
        connection.getresponse.return_value = response
        client = self.make_client(lambda *_args: connection)
        result = client.probe_one("https://service.home.example.test/")
        self.assertEqual(result.status, 302)
        self.assertEqual(result.error, "redirect_rejected")

    def test_probe_error_codes_are_stable(self) -> None:
        cases = (
            (socket.timeout(), "timeout"),
            (ssl.SSLError("tls"), "tls_error"),
            (ConnectionRefusedError(), "connection_error"),
            (main.http.client.BadStatusLine("bad"), "request_error"),
        )
        for exception, expected in cases:
            with self.subTest(expected=expected):
                connection = mock.Mock()
                connection.request.side_effect = exception
                client = self.make_client(lambda *_args, connection=connection: connection)
                result = client.probe_one("https://service.home.example.test/")
                self.assertIsNone(result.status)
                self.assertEqual(result.error, expected)

    def test_batch_preserves_input_order_and_uses_shared_worker_limit(self) -> None:
        client = self.make_client(mock.Mock())
        self.assertEqual(client.executor._max_workers, 8)
        targets = [f"https://service{index}.home.example.test/" for index in range(12)]

        def delayed_probe(target: str, _started: float, _deadline: float) -> main.ProbeResult:
            index = targets.index(target)
            threading.Event().wait((len(targets) - index) / 1000)
            return main.ProbeResult(target, 200 + index, index, None)

        with mock.patch.object(client, "probe_one", side_effect=delayed_probe):
            results = client.probe_many(targets)
        self.assertEqual([result.target for result in results], targets)
        self.assertEqual([result.status for result in results], list(range(200, 212)))

    def test_queued_probe_honors_shared_batch_deadline(self) -> None:
        factory = mock.Mock()
        client = self.make_client(factory)
        with mock.patch.object(client, "clock", return_value=10.0):
            result = client.probe_one("https://service.home.example.test/", started=0.0, deadline=5.0)
        self.assertEqual(result.error, "timeout")
        factory.assert_not_called()


class LoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.log_file = Path(self.temporary.name) / "service.log"

    def tearDown(self) -> None:
        logger = main.logging.getLogger(main.SERVICE_NAME)
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()
        self.temporary.cleanup()

    def flush_handlers(self, logger: main.logging.Logger) -> None:
        for handler in logger.handlers:
            handler.flush()

    def test_logging_uses_one_small_rotating_backup(self) -> None:
        logger = main.configure_logging(self.log_file, include_console=False)
        handler = logger.handlers[0]
        self.assertIsInstance(handler, main.RotatingFileHandler)
        self.assertEqual(handler.maxBytes, 48 * 1024)
        self.assertEqual(handler.backupCount, 1)

        for index in range(20):
            logger.info("entry %d %s", index, "x" * 8192)
        self.flush_handlers(logger)

        backup = Path(f"{self.log_file}.1")
        self.assertTrue(self.log_file.is_file())
        self.assertTrue(backup.is_file())
        self.assertLessEqual(self.log_file.stat().st_size, main.LOG_MAX_BYTES)
        self.assertLessEqual(backup.stat().st_size, main.LOG_MAX_BYTES)
        self.assertEqual({path.name for path in self.log_file.parent.iterdir()}, {"service.log", "service.log.1"})

    def test_http_access_is_suppressed_but_lifecycle_is_logged(self) -> None:
        logger = main.configure_logging(self.log_file, include_console=False)
        request_handler = main.StateRequestHandler.__new__(main.StateRequestHandler)
        request_handler.server = mock.Mock(logger=logger)
        with mock.patch.object(request_handler, "address_string", return_value="127.0.0.1"):
            request_handler.log_message('"%s" %s', "GET /healthz HTTP/1.1", "200")
        self.flush_handlers(logger)
        self.assertEqual(self.log_file.read_text(encoding="utf-8"), "")

        logger.info("wallpaper state service listening")
        self.flush_handlers(logger)
        self.assertIn("wallpaper state service listening", self.log_file.read_text(encoding="utf-8"))

    def test_oversized_legacy_logs_are_discarded(self) -> None:
        backup = Path(f"{self.log_file}.1")
        self.log_file.write_bytes(b"x" * (main.LOG_MAX_BYTES + 1))
        backup.write_bytes(b"y" * (main.LOG_MAX_BYTES + 1))

        main.configure_logging(self.log_file, include_console=False)

        self.assertEqual(self.log_file.stat().st_size, 0)
        self.assertFalse(backup.exists())


class FakeProbeClient:
    def __init__(self) -> None:
        self.executor = object()
        self.targets: list[str] = []

    def probe_many(self, targets: list[str]) -> list[main.ProbeResult]:
        self.targets = list(targets)
        return [main.ProbeResult(target, 200 if index == 0 else None, 10 + index, None if index == 0 else "timeout")
                for index, target in enumerate(targets)]


class FakeBestdoriManager:
    def __init__(self) -> None:
        self.draw_request: tuple[str, set[int], int | None] | None = None
        self.history_action: str | None = None

    def characters(self) -> list[dict[str, object]]:
        return [{"id": 1, "name": "户山香澄"}, {"id": 40, "name": "奥泽美咲"}]

    def draw(self, mode: str, rarities: set[int], character_id: int | None) -> dict[str, object]:
        self.draw_request = (mode, set(rarities), character_id)
        return {
            "background": {
                "background": "户山香澄_五星_测试卡面_101_after_training.png",
                "size": 100,
                "width": 1000,
                "height": 1334,
            },
            "card": {
                "id": 101,
                "characterId": 1,
                "characterName": "户山香澄",
                "rarity": 5,
                "title": "测试卡面",
                "art": "after_training",
            },
        }

    def reconcile(self) -> dict[str, int]:
        self.history_action = "reconcile"
        return {"removed": 1, "remaining": 2}

    def reset(self) -> dict[str, int]:
        self.history_action = "reset"
        return {"removed": 3, "remaining": 0}


class APITests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = TemporaryProject()
        self.config = self.project.write_config(listen="127.0.0.1:1")
        self.store = main.StateStore(self.config.background_dir, self.config.state_file, lambda: FIXED_TIME)
        logger = main.logging.getLogger(f"test-{id(self)}")
        logger.handlers.clear()
        logger.addHandler(main.logging.NullHandler())
        self.server = main.StateHTTPServer.__new__(main.StateHTTPServer)
        self.server.config = self.config
        self.server.store = self.store
        self.server.logger = logger
        self.server.probe_client = FakeProbeClient()
        self.server.bestdori_manager = FakeBestdoriManager()
        main.ThreadingHTTPServer.__init__(self.server, ("127.0.0.1", 0), main.StateRequestHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.server.server_address

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.project.close()

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, object]]:
        connection = HTTPConnection(self.host, self.port, timeout=2)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        payload = json.loads(response.read()) if response.length != 0 else {}
        status = response.status
        connection.close()
        return status, payload

    def patch(self, value: dict[str, object], headers: dict[str, str] | None = None) -> tuple[int, dict[str, object]]:
        request_headers = {
            "Origin": TEST_ORIGIN,
            "X-Requested-With": "homelab-dashboard",
            "Content-Type": "application/json",
        }
        if headers:
            request_headers.update(headers)
        return self.request("PATCH", main.API_PATH, json.dumps(value).encode(), request_headers)

    def service_status(self, value: object, headers: dict[str, str] | None = None) -> tuple[int, dict[str, object]]:
        request_headers = {
            "Origin": TEST_ORIGIN,
            "X-Requested-With": "homelab-dashboard",
            "Content-Type": "application/json",
        }
        if headers:
            request_headers.update(headers)
        return self.request("POST", main.SERVICE_STATUS_PATH, json.dumps(value).encode(), request_headers)

    def upload(
        self,
        name: str,
        body: bytes,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, object]]:
        request_headers = {
            "Origin": TEST_ORIGIN,
            "X-Requested-With": "homelab-dashboard",
            "Content-Type": "application/octet-stream",
        }
        if headers:
            request_headers.update(headers)
        return self.request("POST", f"{main.BACKGROUND_UPLOAD_PATH}?name={name}", body, request_headers)

    def remove(
        self,
        value: object,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, object]]:
        request_headers = {
            "Origin": TEST_ORIGIN,
            "X-Requested-With": "homelab-dashboard",
            "Content-Type": "application/json",
        }
        if headers:
            request_headers.update(headers)
        return self.request("POST", main.BACKGROUND_REMOVE_PATH, json.dumps(value).encode(), request_headers)

    def bestdori_post(
        self,
        path: str,
        value: object,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, object]]:
        request_headers = {
            "Origin": TEST_ORIGIN,
            "X-Requested-With": "homelab-dashboard",
            "Content-Type": "application/json",
        }
        if headers:
            request_headers.update(headers)
        return self.request("POST", path, json.dumps(value).encode(), request_headers)

    def test_health_and_get_contract(self) -> None:
        status, health = self.request("GET", main.HEALTH_PATH)
        self.assertEqual(status, 200)
        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["service"], main.SERVICE_NAME)
        self.assertEqual(health["version"], main.SERVICE_VERSION)
        status, preferences = self.request("GET", main.API_PATH)
        self.assertEqual(status, 200)
        self.assertEqual(preferences["landscape"], "bg.jpg")

    def test_background_manifest_contract_partial_failure_and_methods(self) -> None:
        (self.project.backgrounds / "bg.jpg").write_bytes(image_bytes("JPEG"))
        with self.assertLogs(main.SERVICE_NAME, level="WARNING"):
            status, manifest = self.request("GET", main.BACKGROUND_FILES_PATH)
        self.assertEqual(status, 200)
        self.assertEqual(set(manifest), {"version", "backgrounds", "thumbnails"})
        self.assertEqual(manifest["version"], 1)
        self.assertEqual(manifest["backgrounds"][0]["name"], "bg.jpg")
        self.assertIsInstance(manifest["backgrounds"][0]["thumbnail"], str)
        self.assertEqual(
            manifest["thumbnails"],
            {"generated": 1, "reused": 0, "failed": 3},
        )
        failed = [item for item in manifest["backgrounds"] if item["thumbnail"] is None]
        self.assertEqual(len(failed), 3)

        with self.assertLogs(main.SERVICE_NAME, level="WARNING"):
            status, reused = self.request("GET", main.BACKGROUND_FILES_PATH)
        self.assertEqual(status, 200)
        self.assertEqual(reused["thumbnails"], {"generated": 0, "reused": 1, "failed": 3})
        for method in ("POST", "PATCH", "DELETE", "HEAD", "OPTIONS"):
            with self.subTest(method=method):
                status, _ = self.request(method, main.BACKGROUND_FILES_PATH)
                self.assertEqual(status, 405)

    def test_patch_matches_frontend_contract(self) -> None:
        status, preferences = self.patch({"mode": "landscape", "background": "test.jpg"})
        self.assertEqual(status, 200)
        self.assertEqual(preferences["landscape"], "test.jpg")

    def test_patch_validation(self) -> None:
        cases = (
            ({"mode": "landscape", "background": "test.jpg"}, {"Origin": "https://evil.test"}, 403),
            ({"mode": "landscape", "background": "test.jpg"}, {"X-Requested-With": "wrong"}, 403),
            ({"mode": "desktop", "background": "test.jpg"}, None, 400),
            ({"mode": ["landscape"], "background": "test.jpg"}, None, 400),
            ({"mode": "landscape", "background": ["test.jpg"]}, None, 400),
            ({"mode": "landscape", "background": "../test.jpg"}, None, 400),
            ({"mode": "landscape", "background": "missing.jpg"}, None, 404),
            ({"mode": "landscape", "background": "test.jpg", "extra": True}, None, 400),
        )
        for value, headers, expected in cases:
            with self.subTest(value=value, headers=headers):
                status, _ = self.patch(value, headers)
                self.assertEqual(status, expected)

    def test_background_upload_contract_and_collision_naming(self) -> None:
        payload = image_bytes()
        status, uploaded = self.upload("from-browser.png", payload)
        self.assertEqual(status, 201)
        self.assertEqual(
            uploaded,
            {
                "background": "from-browser.png",
                "size": len(payload),
                "width": 3,
                "height": 2,
            },
        )
        self.assertEqual((self.project.backgrounds / "from-browser.png").read_bytes(), payload)

        status, duplicate = self.upload("from-browser.png", payload)
        self.assertEqual(status, 201)
        self.assertEqual(duplicate["background"], "from-browser (2).png")
        self.assertEqual(self.store.get()["landscape"], "bg.jpg")

    def test_background_upload_request_validation(self) -> None:
        payload = image_bytes()
        cases = (
            ("from-browser.png", payload, {"Origin": "https://evil.test"}, 403),
            ("from-browser.png", payload, {"X-Requested-With": "wrong"}, 403),
            ("from-browser.png", payload, {"Content-Type": "image/png"}, 415),
            ("../from-browser.png", payload, None, 400),
            ("from-browser.jpg", payload, None, 400),
            ("from-browser.png", b"not an image", None, 400),
            ("from-browser.png&extra=value", payload, None, 400),
        )
        for name, body, headers, expected in cases:
            with self.subTest(name=name, headers=headers):
                status, _ = self.upload(name, body, headers)
                self.assertEqual(status, expected)

        connection = HTTPConnection(self.host, self.port, timeout=2)
        connection.putrequest("POST", f"{main.BACKGROUND_UPLOAD_PATH}?name=too-large.png")
        connection.putheader("Origin", TEST_ORIGIN)
        connection.putheader("X-Requested-With", "homelab-dashboard")
        connection.putheader("Content-Type", "application/octet-stream")
        connection.putheader("Content-Length", str(main.MAX_BACKGROUND_UPLOAD_BODY + 1))
        connection.endheaders()
        response = connection.getresponse()
        self.assertEqual(response.status, 413)
        response.read()
        connection.close()

        status, _ = self.request("GET", main.BACKGROUND_UPLOAD_PATH)
        self.assertEqual(status, 405)

    def test_background_remove_trash_and_permanent_fallback_contract(self) -> None:
        self.store.update("landscape", "test.jpg")
        self.store.update("portrait", "test.jpg")

        with mock.patch.object(main, "send2trash", side_effect=OSError("trash unavailable")):
            status, conflict = self.remove({"background": "test.jpg"})
        self.assertEqual(status, 409)
        self.assertEqual(conflict["error"], "trash_unavailable")
        self.assertEqual(conflict["expiresIn"], main.REMOVAL_TOKEN_TTL_SECONDS)
        self.assertIsInstance(conflict["confirmationToken"], str)
        self.assertTrue((self.project.backgrounds / "test.jpg").exists())

        status, removed = self.remove(
            {
                "background": "test.jpg",
                "confirmationToken": conflict["confirmationToken"],
            }
        )
        self.assertEqual(status, 200)
        self.assertEqual(removed["background"], "test.jpg")
        self.assertEqual(removed["disposition"], "permanent")
        self.assertEqual(removed["preferences"]["landscape"], "bg.jpg")
        self.assertEqual(removed["preferences"]["portrait"], "bg.jpg")
        self.assertFalse((self.project.backgrounds / "test.jpg").exists())

    def test_background_remove_validation_and_trash_success(self) -> None:
        cases = (
            ({"background": "bg.jpg"}, None, 400),
            ({"background": "../test.jpg"}, None, 400),
            ({"background": "missing.jpg"}, None, 404),
            ({"background": "test.jpg", "confirmationToken": "invalid"}, None, 403),
            ({"background": "test.jpg", "extra": True}, None, 400),
            ({"background": ["test.jpg"]}, None, 400),
            ({"background": "test.jpg"}, {"Origin": "https://evil.test"}, 403),
            ({"background": "test.jpg"}, {"Content-Type": "text/plain"}, 415),
        )
        for value, headers, expected in cases:
            with self.subTest(value=value, headers=headers):
                status, _ = self.remove(value, headers)
                self.assertEqual(status, expected)

        self.store.update("landscape", "alternate.webp")

        def simulate_trash(path: str) -> None:
            Path(path).unlink()

        with mock.patch.object(main, "send2trash", side_effect=simulate_trash):
            status, removed = self.remove({"background": "alternate.webp"})
        self.assertEqual(status, 200)
        self.assertEqual(removed["disposition"], "trash")
        self.assertEqual(removed["preferences"]["landscape"], "bg.jpg")

        status, _ = self.request("GET", main.BACKGROUND_REMOVE_PATH)
        self.assertEqual(status, 405)

    def test_bestdori_characters_draw_and_history_contracts(self) -> None:
        status, characters = self.request("GET", main.BESTDORI_CHARACTERS_PATH)
        self.assertEqual(status, 200)
        self.assertEqual(characters["characters"][0], {"id": 1, "name": "户山香澄"})

        status, drawn = self.bestdori_post(
            main.BESTDORI_DRAW_PATH,
            {"mode": "character", "rarities": [3, 5], "characterId": 1},
        )
        self.assertEqual(status, 201)
        self.assertEqual(drawn["background"]["background"], "户山香澄_五星_测试卡面_101_after_training.png")
        self.assertEqual(drawn["card"]["id"], 101)
        self.assertEqual(self.server.bestdori_manager.draw_request, ("character", {3, 5}, 1))

        status, reconciled = self.bestdori_post(main.BESTDORI_RECONCILE_PATH, {})
        self.assertEqual(status, 200)
        self.assertEqual(reconciled, {"removed": 1, "remaining": 2})
        status, reset = self.bestdori_post(main.BESTDORI_RESET_PATH, {})
        self.assertEqual(status, 200)
        self.assertEqual(reset, {"removed": 3, "remaining": 0})

    def test_bestdori_request_validation_and_security(self) -> None:
        valid_random = {"mode": "random", "rarities": [4, 5]}
        cases = (
            (valid_random, {"Origin": "https://evil.test"}, 403),
            (valid_random, {"X-Requested-With": "wrong"}, 403),
            (valid_random, {"Content-Type": "text/plain"}, 415),
            ({"mode": "random", "rarities": []}, None, 400),
            ({"mode": "random", "rarities": [3, 3]}, None, 400),
            ({"mode": "random", "rarities": [2]}, None, 400),
            ({"mode": "random", "rarities": [True]}, None, 400),
            ({"mode": "random", "rarities": [3], "characterId": 1}, None, 400),
            ({"mode": "character", "rarities": [3]}, None, 400),
            ({"mode": "character", "rarities": [3], "characterId": 41}, None, 400),
            ({"mode": "character", "rarities": [3], "characterId": True}, None, 400),
        )
        for value, headers, expected in cases:
            with self.subTest(value=value, headers=headers):
                status, _ = self.bestdori_post(main.BESTDORI_DRAW_PATH, value, headers)
                self.assertEqual(status, expected)

        for path in (main.BESTDORI_RECONCILE_PATH, main.BESTDORI_RESET_PATH):
            with self.subTest(path=path):
                status, _ = self.bestdori_post(path, {"unexpected": True})
                self.assertEqual(status, 400)
                status, _ = self.request("GET", path)
                self.assertEqual(status, 405)
        status, _ = self.request("POST", main.BESTDORI_DRAW_PATH, b"{}", {})
        self.assertEqual(status, 403)
        status, _ = self.request("POST", main.BESTDORI_CHARACTERS_PATH)
        self.assertEqual(status, 405)

    def test_all_dashboard_json_endpoints_reject_null_without_hanging(self) -> None:
        dashboard_headers = {
            "Origin": TEST_ORIGIN,
            "X-Requested-With": "homelab-dashboard",
            "Content-Type": "application/json",
        }
        endpoints = (
            ("PATCH", main.API_PATH),
            ("POST", main.BACKGROUND_REMOVE_PATH),
            ("POST", main.SERVICE_STATUS_PATH),
            ("POST", main.BESTDORI_DRAW_PATH),
            ("POST", main.BESTDORI_RECONCILE_PATH),
            ("POST", main.BESTDORI_RESET_PATH),
        )
        for method, path in endpoints:
            with self.subTest(method=method, path=path):
                status, payload = self.request(method, path, b"null", dashboard_headers)
                self.assertEqual(status, 400)
                self.assertIn("null", payload["error"])

    def test_bestdori_failures_map_to_stable_http_errors(self) -> None:
        for error, expected_status, expected_message in (
            (main.BestdoriDrawBusy("busy"), 409, "bestdori_operation_in_progress"),
            (main.BestdoriPoolExhausted("empty"), 409, "bestdori_card_pool_exhausted"),
            (main.BestdoriHistoryError("broken"), 503, "bestdori_history_unavailable"),
            (main.BestdoriUnavailable("offline"), 502, "bestdori_unavailable"),
            (main.InvalidImage("bad"), 502, "bestdori_invalid_image"),
        ):
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(self.server.bestdori_manager, "draw", side_effect=error):
                    status, payload = self.bestdori_post(
                        main.BESTDORI_DRAW_PATH,
                        {"mode": "random", "rarities": [3]},
                    )
                self.assertEqual(status, expected_status)
                self.assertEqual(payload["error"], expected_message)

    def test_service_status_contract_and_partial_failure(self) -> None:
        targets = ["https://one.home.example.test/", "https://two.home.example.test/"]
        status, payload = self.service_status({"targets": targets})
        self.assertEqual(status, 200)
        self.assertEqual(self.server.probe_client.targets, targets)
        self.assertEqual(
            payload["results"],
            [
                {"target": targets[0], "status": 200, "latencyMs": 10, "error": None},
                {"target": targets[1], "status": None, "latencyMs": 11, "error": "timeout"},
            ],
        )

    def test_service_status_request_validation(self) -> None:
        for count in (1, 11, 32):
            with self.subTest(count=count):
                targets = [f"https://service{index}.home.example.test/" for index in range(count)]
                status, payload = self.service_status({"targets": targets})
                self.assertEqual(status, 200)
                self.assertEqual(len(payload["results"]), count)

        cases = (
            ({}, None, 400),
            ({"targets": []}, None, 400),
            ({"targets": [1]}, None, 400),
            ({"targets": ["https://one.home.example.test/"] * 33}, None, 400),
            ({"targets": ["https://one.home.example.test/"]}, {"Origin": "https://evil.test"}, 403),
            ({"targets": ["https://one.home.example.test/"]}, {"X-Requested-With": "wrong"}, 403),
            ({"targets": ["https://one.home.example.test/"]}, {"Content-Type": "text/plain"}, 415),
        )
        for value, headers, expected in cases:
            with self.subTest(value=value, headers=headers):
                status, _ = self.service_status(value, headers)
                self.assertEqual(status, expected)

        status, _ = self.request("GET", main.SERVICE_STATUS_PATH)
        self.assertEqual(status, 405)

        dashboard_headers = {
            "Origin": TEST_ORIGIN,
            "X-Requested-With": "homelab-dashboard",
            "Content-Type": "application/json",
        }
        status, _ = self.request(
            "POST",
            main.SERVICE_STATUS_PATH,
            b'{"targets":[],"targets":[]}',
            dashboard_headers,
        )
        self.assertEqual(status, 400)
        status, _ = self.request(
            "POST",
            main.SERVICE_STATUS_PATH,
            b"x" * (main.MAX_SERVICE_STATUS_BODY + 1),
            dashboard_headers,
        )
        self.assertEqual(status, 413)

    def test_control_requires_token_and_shuts_down(self) -> None:
        status, _ = self.request("POST", main.CONTROL_PATH, headers={"X-Control-Token": "wrong"})
        self.assertEqual(status, 403)
        status, payload = self.request(
            "POST", main.CONTROL_PATH, headers={"X-Control-Token": self.config.control_token}
        )
        self.assertEqual(status, 202)
        self.assertEqual(payload["status"], "stopping")


class LauncherTests(unittest.TestCase):
    def test_console_encoding_is_forced_to_utf8_on_windows(self) -> None:
        kernel32 = mock.Mock()
        windll = mock.Mock(kernel32=kernel32)
        streams = [mock.Mock() for _ in range(3)]
        with mock.patch.object(main.os, "name", "nt"), mock.patch.object(main.ctypes, "windll", windll), mock.patch.object(
            main.sys, "stdin", streams[0]
        ), mock.patch.object(main.sys, "stdout", streams[1]), mock.patch.object(main.sys, "stderr", streams[2]):
            main.configure_console_encoding()

        kernel32.SetConsoleCP.assert_called_once_with(65001)
        kernel32.SetConsoleOutputCP.assert_called_once_with(65001)
        for stream in streams:
            stream.reconfigure.assert_called_once_with(encoding="utf-8", errors="replace")

    def test_ensure_backend_reuses_healthy_service(self) -> None:
        config = mock.Mock()
        expected = {
            "status": "ok",
            "service": main.SERVICE_NAME,
            "version": main.SERVICE_VERSION,
            "pid": 123,
        }
        with mock.patch.object(main, "health", return_value=expected), mock.patch.object(main, "spawn_backend") as spawn:
            self.assertEqual(main.ensure_backend(config), expected)
            spawn.assert_not_called()

    def test_health_requires_exact_service_version(self) -> None:
        config = mock.Mock(local_base_url="http://127.0.0.1:8787")
        matching = {
            "status": "ok",
            "service": main.SERVICE_NAME,
            "version": main.SERVICE_VERSION,
            "pid": 123,
        }
        with mock.patch.object(main, "read_json_url", return_value=matching):
            self.assertEqual(main.health(config), matching)

        for version in (None, str(main.SERVICE_VERSION), main.SERVICE_VERSION - 1, main.SERVICE_VERSION + 1):
            incompatible = dict(matching)
            if version is None:
                incompatible.pop("version")
            else:
                incompatible["version"] = version
            with self.subTest(version=version), mock.patch.object(
                main, "read_json_url", return_value=incompatible
            ):
                self.assertIsNone(main.health(config))

    def test_spawn_rejects_incompatible_listener_without_starting_process(self) -> None:
        config = mock.Mock(
            local_base_url="http://127.0.0.1:8787",
            listen="127.0.0.1:8787",
        )
        incompatible = {
            "status": "ok",
            "service": main.SERVICE_NAME,
            "version": main.SERVICE_VERSION - 1,
        }
        with mock.patch.object(main, "read_json_url", return_value=incompatible), mock.patch.object(
            main, "port_has_http_listener", return_value=True
        ), mock.patch.object(main.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(RuntimeError, "incompatible"):
                main.spawn_backend(config)
            popen.assert_not_called()

    def test_spawn_rejects_foreign_listener(self) -> None:
        config = mock.Mock()
        config.listen = "127.0.0.1:8787"
        with mock.patch.object(main, "health", return_value=None), mock.patch.object(
            main, "port_has_http_listener", return_value=True
        ):
            with self.assertRaisesRegex(RuntimeError, "occupied"):
                main.spawn_backend(config)

    def test_executable_command_supports_source_mode(self) -> None:
        with mock.patch.object(main.sys, "frozen", False, create=True):
            command = main.executable_command("serve", "--config", "config.json")
        self.assertEqual(command[0], sys.executable)
        self.assertTrue(command[1].endswith("main.py"))
        self.assertEqual(command[-3:], ["serve", "--config", "config.json"])

    def test_main_validates_application_location_before_serve(self) -> None:
        project = TemporaryProject()
        try:
            config = project.write_config()
            arguments = argparse.Namespace(command="serve", config=project.config_path)
            with mock.patch.object(main, "parse_arguments", return_value=arguments), mock.patch.object(
                main, "load_config", return_value=config
            ), mock.patch.object(main, "validate_application_location") as validate_location, mock.patch.object(
                main, "serve", return_value=0
            ) as serve:
                self.assertEqual(main.main([]), 0)
            validate_location.assert_called_once_with(config)
            serve.assert_called_once_with(config)
        finally:
            project.close()

    def test_monitor_refresh_and_quit_keys_remain_immediate(self) -> None:
        keyboard = mock.Mock()
        keyboard.kbhit.return_value = True
        keyboard.getwch.side_effect = ["r", "q"]
        config = mock.Mock()
        config.listen = "127.0.0.1:8787"
        config.background_dir = Path("backgrounds")
        config.state_dir = Path("state")
        config.log_file = Path("service.log")
        status = {"status": "ok", "pid": 123}

        with mock.patch.dict(sys.modules, {"msvcrt": keyboard}), mock.patch.object(
            main, "health", return_value=status
        ) as health, mock.patch.object(main, "read_preferences", return_value=None), mock.patch.object(
            main, "clear_console"
        ), mock.patch.object(main, "tail_lines", return_value=[]), mock.patch.object(
            main.time, "sleep"
        ) as sleep, mock.patch("builtins.print"):
            self.assertEqual(main.monitor(config), 0)

        self.assertEqual(health.call_count, 2)
        self.assertEqual(keyboard.getwch.call_count, 2)
        sleep.assert_not_called()

    def test_monitor_shutdown_confirmation_requires_exact_uppercase_y(self) -> None:
        for value, expected in (
            ("Y", True),
            ("y", False),
            ("Y ", False),
            ("yes", False),
            ("", False),
        ):
            with self.subTest(value=value), mock.patch("builtins.input", return_value=value), mock.patch.object(
                main, "clear_console"
            ), mock.patch("builtins.print"):
                self.assertIs(main.confirm_monitor_shutdown(), expected)

        for error in (EOFError(), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__), mock.patch(
                "builtins.input", side_effect=error
            ), mock.patch.object(main, "clear_console"), mock.patch("builtins.print"):
                self.assertFalse(main.confirm_monitor_shutdown())

    def test_monitor_big_exit_stops_api_or_exits_when_already_stopped(self) -> None:
        for stopped in (True, False):
            keyboard = mock.Mock()
            keyboard.kbhit.return_value = True
            keyboard.getwch.return_value = "x"
            config = mock.Mock(
                listen="127.0.0.1:8787",
                background_dir=Path("backgrounds"),
                state_dir=Path("state"),
                log_file=Path("service.log"),
                probe_connect_address="127.0.0.1:443",
                probe_domain_suffix="home.example.test",
            )
            status = {"status": "ok", "pid": 123}

            with self.subTest(stopped=stopped), mock.patch.dict(
                sys.modules, {"msvcrt": keyboard}
            ), mock.patch.object(main, "health", return_value=status), mock.patch.object(
                main, "read_preferences", return_value=None
            ), mock.patch.object(main, "clear_console"), mock.patch.object(
                main, "tail_lines", return_value=[]
            ), mock.patch.object(main, "confirm_monitor_shutdown", return_value=True), mock.patch.object(
                main, "request_stop", return_value=stopped
            ) as request_stop, mock.patch.object(
                main.time, "sleep"
            ) as sleep, mock.patch(
                "builtins.print"
            ) as output:
                self.assertEqual(main.monitor(config), 0)

            request_stop.assert_called_once_with(config)
            sleep.assert_not_called()
            message = "API 已停止，Monitor 已关闭。" if stopped else "API 当前未运行，Monitor 已关闭。"
            self.assertTrue(any(message in str(call) for call in output.call_args_list))

    def test_monitor_big_exit_cancel_returns_to_monitor_and_can_retry(self) -> None:
        keyboard = mock.Mock()
        keyboard.kbhit.return_value = True
        keyboard.getwch.side_effect = ["x", "x"]
        config = mock.Mock(
            listen="127.0.0.1:8787",
            background_dir=Path("backgrounds"),
            state_dir=Path("state"),
            log_file=Path("service.log"),
            probe_connect_address="127.0.0.1:443",
            probe_domain_suffix="home.example.test",
        )
        status = {"status": "ok", "pid": 123}

        with mock.patch.dict(sys.modules, {"msvcrt": keyboard}), mock.patch.object(
            main, "health", return_value=status
        ) as health, mock.patch.object(main, "read_preferences", return_value=None), mock.patch.object(
            main, "clear_console"
        ), mock.patch.object(main, "tail_lines", return_value=[]), mock.patch.object(
            main, "confirm_monitor_shutdown", side_effect=[False, True]
        ), mock.patch.object(main, "request_stop") as request_stop, mock.patch.object(
            main.time, "sleep"
        ) as sleep, mock.patch(
            "builtins.print"
        ) as output:
            self.assertEqual(main.monitor(config), 0)

        request_stop.assert_called_once_with(config)
        self.assertEqual(health.call_count, 2)
        sleep.assert_not_called()
        self.assertTrue(any("已取消大退，API 继续运行" in str(call) for call in output.call_args_list))

    def test_monitor_big_exit_failure_returns_to_monitor_and_can_retry(self) -> None:
        keyboard = mock.Mock()
        keyboard.kbhit.return_value = True
        keyboard.getwch.side_effect = ["X", "x"]
        config = mock.Mock(
            listen="127.0.0.1:8787",
            background_dir=Path("backgrounds"),
            state_dir=Path("state"),
            log_file=Path("service.log"),
            probe_connect_address="127.0.0.1:443",
            probe_domain_suffix="home.example.test",
        )
        status = {"status": "ok", "pid": 123}

        with mock.patch.dict(sys.modules, {"msvcrt": keyboard}), mock.patch.object(
            main, "health", return_value=status
        ), mock.patch.object(main, "read_preferences", return_value=None), mock.patch.object(
            main, "clear_console"
        ), mock.patch.object(main, "tail_lines", return_value=[]), mock.patch.object(
            main, "confirm_monitor_shutdown", return_value=True
        ), mock.patch.object(
            main,
            "request_stop",
            side_effect=[RuntimeError("backend did not stop within the timeout"), True],
        ) as request_stop, mock.patch.object(
            main.time, "sleep"
        ) as sleep, mock.patch(
            "builtins.print"
        ) as output:
            self.assertEqual(main.monitor(config), 0)

        self.assertEqual(request_stop.call_args_list, [mock.call(config), mock.call(config)])
        sleep.assert_not_called()
        self.assertTrue(
            any(
                "大退失败：backend did not stop within the timeout" in str(call)
                for call in output.call_args_list
            )
        )


if __name__ == "__main__":
    unittest.main()
