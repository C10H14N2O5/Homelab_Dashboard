from __future__ import annotations

import hashlib
import json
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from homelab_state import favicon
from tests.helpers import FIXED_TIME


SIMPLE_SVG = '<svg viewBox="0 0 24 24"><path d="M1 1h22v22H1z"/></svg>'
SECOND_SVG = '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/></svg>'


class FaviconStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.state_file = Path(self.temporary.name) / favicon.FAVICON_STATE_FILE_NAME
        self.store = favicon.FaviconStore(self.state_file, lambda: FIXED_TIME)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_missing_state_is_explicit_and_does_not_create_a_file(self) -> None:
        self.assertEqual(
            self.store.get(),
            {
                "version": 1,
                "configured": False,
                "updatedAt": None,
                "contentHash": None,
            },
        )
        self.assertFalse(self.state_file.exists())
        with self.assertRaises(favicon.FaviconNotFound):
            self.store.get_asset("0" * 64)

    def test_upload_is_sanitized_hashed_and_persists_across_restart(self) -> None:
        result = self.store.update({"version": 1, "iconSvg": SIMPLE_SVG})
        self.assertTrue(result["configured"])
        self.assertEqual(result["updatedAt"], FIXED_TIME)
        self.assertRegex(result["contentHash"], r"^[0-9a-f]{64}$")
        persisted = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertIn('xmlns="http://www.w3.org/2000/svg"', persisted["iconSvg"])
        self.assertEqual(
            result["contentHash"],
            hashlib.sha256(persisted["iconSvg"].encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            self.store.get_asset(result["contentHash"]),
            (persisted["iconSvg"], result["contentHash"]),
        )

        restarted = favicon.FaviconStore(self.state_file, lambda: FIXED_TIME)
        self.assertEqual(restarted.get(), result)
        self.assertEqual(restarted.get_asset(result["contentHash"])[0], persisted["iconSvg"])

    def test_replacement_invalidates_old_hash_and_removal_persists_tombstone(self) -> None:
        first = self.store.update({"version": 1, "iconSvg": SIMPLE_SVG})
        second = self.store.update({"version": 1, "iconSvg": SECOND_SVG})
        self.assertNotEqual(first["contentHash"], second["contentHash"])
        with self.assertRaises(favicon.FaviconNotFound):
            self.store.get_asset(first["contentHash"])
        self.assertEqual(self.store.get_asset(second["contentHash"])[1], second["contentHash"])

        removed = self.store.update({"version": 1, "iconSvg": None})
        self.assertEqual(
            removed,
            {
                "version": 1,
                "configured": False,
                "updatedAt": FIXED_TIME,
                "contentHash": None,
            },
        )
        persisted = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertIsNone(persisted["iconSvg"])
        restarted = favicon.FaviconStore(self.state_file, lambda: FIXED_TIME)
        self.assertEqual(restarted.get(), removed)
        with self.assertRaises(favicon.FaviconNotFound):
            restarted.get_asset(second["contentHash"])

    def test_last_write_wins_without_revision_contract(self) -> None:
        self.store.update({"version": 1, "iconSvg": SIMPLE_SVG})
        latest = self.store.update({"version": 1, "iconSvg": SECOND_SVG})
        self.assertEqual(self.store.get(), latest)
        self.assertNotIn("revision", latest)

    def test_request_shape_and_svg_validation_are_strict(self) -> None:
        invalid_requests = (
            None,
            [],
            {"version": True, "iconSvg": SIMPLE_SVG},
            {"version": 2, "iconSvg": SIMPLE_SVG},
            {"version": 1},
            {"version": 1, "iconSvg": SIMPLE_SVG, "extra": True},
            {"version": 1, "iconSvg": 7},
            {"version": 1, "iconSvg": '<svg onload="alert(1)"><path d="M0 0"/></svg>'},
        )
        for request in invalid_requests:
            with self.subTest(request=request):
                with self.assertRaises(favicon.InvalidFavicon):
                    self.store.update(request)
                self.assertFalse(self.state_file.exists())

    def test_atomic_write_failure_preserves_previous_file_and_memory(self) -> None:
        before = self.store.update({"version": 1, "iconSvg": SIMPLE_SVG})
        file_before = self.state_file.read_bytes()
        with mock.patch.object(favicon, "atomic_write_json", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.store.update({"version": 1, "iconSvg": SECOND_SVG})
        self.assertEqual(self.store.get(), before)
        self.assertEqual(self.state_file.read_bytes(), file_before)

    def test_corrupt_incompatible_and_oversized_files_fail_closed(self) -> None:
        cases = (
            b"",
            b"[]",
            b'{"version":2,"updatedAt":"2026-07-22T12:00:00Z","iconSvg":null}',
            b'{"version":1,"version":1,"updatedAt":"2026-07-22T12:00:00Z","iconSvg":null}',
            b'{"version":1,"updatedAt":"not-a-time","iconSvg":null}',
            b'{"version":1,"updatedAt":"2026-07-22T12:00:00Z","iconSvg":"<svg><script/></svg>"}',
            b" " * (favicon.MAX_FAVICON_STATE_FILE_BYTES + 1),
        )
        for index, content in enumerate(cases):
            with self.subTest(index=index):
                path = Path(self.temporary.name) / f"damaged-{index}.json"
                path.write_bytes(content)
                store = favicon.FaviconStore(path, lambda: FIXED_TIME)
                with self.assertRaises(favicon.FaviconStateUnavailable):
                    store.get()
                with self.assertRaises(favicon.FaviconStateUnavailable):
                    store.update({"version": 1, "iconSvg": SIMPLE_SVG})
                self.assertEqual(path.read_bytes(), content)

    def test_non_regular_state_path_fails_closed(self) -> None:
        directory_path = Path(self.temporary.name) / "state-directory"
        directory_path.mkdir()
        directory_store = favicon.FaviconStore(directory_path, lambda: FIXED_TIME)
        with self.assertRaises(favicon.FaviconStateUnavailable):
            directory_store.get()

        link_stat = SimpleNamespace(st_mode=stat.S_IFLNK, st_size=0)
        with mock.patch.object(favicon.os, "lstat", return_value=link_stat):
            link_store = favicon.FaviconStore(
                Path(self.temporary.name) / "state-link.json",
                lambda: FIXED_TIME,
            )
        with self.assertRaises(favicon.FaviconStateUnavailable):
            link_store.get()


if __name__ == "__main__":
    unittest.main()
