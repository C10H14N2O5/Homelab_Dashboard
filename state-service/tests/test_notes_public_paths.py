from __future__ import annotations

import os
import unittest
import uuid
from unittest import mock

from homelab_state import config, notes
from homelab_state.core import ConfigurationError
from tests.helpers import TemporaryProject, image_bytes


class NotesPublicPathsTests(unittest.TestCase):
    def setUp(self):
        self.project = TemporaryProject()
        self.addCleanup(self.project.close)
        self.assets = self.project.root / "nas" / "homepage" / "backgrounds"
        self.assets.parent.mkdir(parents=True)
        self.project.backgrounds.rename(self.assets)
        self.link_directory(self.project.backgrounds, self.assets)
        self.backup = self.assets.parent / "notebook"
        self.public = self.project.backgrounds.parent.resolve()

    def link_directory(self, link, target):
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            if os.name != "nt":
                raise
            # Junctions exercise real Windows path resolution without symlink privileges.
            import _winapi
            _winapi.CreateJunction(str(target), str(link))

    def load(self, **overrides):
        return self.project.write_config(version=4, notes={
            "enabled": True,
            "primaryDir": str(self.project.root / "primary"),
            "backupDir": str(self.backup),
        }, **overrides)

    def test_sibling_backup_upload_and_read_through_worker(self):
        cfg = self.load()
        self.assertEqual(cfg.public_root, self.public)
        self.assertEqual(cfg.background_dir, self.assets.resolve())
        with mock.patch.object(config, "application_directory", return_value=self.assets.parent / "service"):
            config.validate_application_location(cfg)
        service = notes.NotesService(cfg.notes_primary_dir, cfg.notes_backup_dir,
                                     start_worker=False, forbidden_roots=cfg.public_directories)
        self.addCleanup(service.close)
        image_id = str(uuid.uuid4())
        data = image_bytes()
        service.upload(image_id, "photo.png", data)
        self.assertEqual(service.get_image(image_id)[0], data)
        self.assertTrue((self.backup / "collection.json").is_file())

    def test_both_actual_web_root_and_background_target_are_protected(self):
        cfg = self.load()
        for public in cfg.public_directories:
            with self.subTest(public=public):
                self.backup = public / "notebook"
                with self.assertRaisesRegex(ConfigurationError, "public Web root"):
                    self.load()
                self.backup = self.assets.parent / "notebook"
                with self.assertRaisesRegex(ConfigurationError, "stateDir must be outside"):
                    self.load(stateDir=str(public / "state"))
                with mock.patch.object(config, "application_directory", return_value=public / "service"):
                    with self.assertRaisesRegex(ConfigurationError, "public Web root"):
                        config.validate_application_location(cfg)
                service = notes.NotesService(self.project.root / "worker-primary", public / "notebook",
                                             start_worker=False, forbidden_roots=cfg.public_directories)
                try:
                    with self.assertRaisesRegex(notes.NotesError, "public Web Root"):
                        service._worker("scan")
                    self.assertFalse((public / "notebook").exists())
                finally:
                    service.close()

    def test_backup_link_requires_direct_target(self):
        cfg = self.load()
        self.backup.mkdir()
        link = self.project.root / "notebook_nas"
        self.link_directory(link, self.backup)
        service = notes.NotesService(cfg.notes_primary_dir, link, start_worker=False,
                                     forbidden_roots=cfg.public_directories)
        self.addCleanup(service.close)
        with self.assertRaisesRegex(notes.NotesError, "symbolic links"):
            service._worker("scan")
