from __future__ import annotations

import sys
import unittest
from unittest import mock

import main
from homelab_state import config
from homelab_state.core import ConfigurationError
from tests.helpers import TEST_ORIGIN, TemporaryProject


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = TemporaryProject()

    def tearDown(self) -> None:
        self.project.close()

    def test_interactive_defaults(self) -> None:
        self.assertEqual(config.DEFAULT_BACKGROUND_DIRECTORY, r"D:\Zoraxy\www\html\backgrounds")
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
        ), mock.patch.object(sys, "frozen", False, create=True), mock.patch.object(
            main, "validate_application_location"
        ) as validate_location, mock.patch("builtins.print"):
            configured = main.run_configuration_wizard(self.project.config_path)

        self.assertEqual(prompts[0], ("背景目录", r"web\html\backgrounds"))
        self.assertEqual(configured.background_dir, existing.background_dir)
        validate_location.assert_called_once_with(configured)

    def test_wizard_validates_paths_before_writing_control_token(self) -> None:
        class AbortWizard(BaseException):
            pass

        public_config = self.project.backgrounds.parent / "unsafe-config.json"
        unused_state_dir = self.project.root / "unused-state"
        answers: list[object] = [
            str(self.project.backgrounds),
            TEST_ORIGIN,
            "127.0.0.1:8787",
            str(unused_state_dir),
            "127.0.0.1:443",
            "home.example.test",
            "5",
            AbortWizard(),
        ]

        with mock.patch.object(main, "prompt_value", side_effect=answers), mock.patch.object(
            main,
            "atomic_write_json",
        ) as write_config, mock.patch("builtins.print"):
            with self.assertRaises(AbortWizard):
                main.run_configuration_wizard(public_config)

        write_config.assert_not_called()
        self.assertFalse(public_config.exists())
        self.assertFalse(unused_state_dir.exists())

    def test_wizard_validation_failure_preserves_existing_config(self) -> None:
        class AbortWizard(BaseException):
            pass

        self.project.write_config()
        original = self.project.config_path.read_bytes()

        def accept_default(_label: str, default: str) -> str:
            return default

        with mock.patch.object(main, "prompt_value", side_effect=accept_default), mock.patch.object(
            main,
            "validate_application_location",
            side_effect=AbortWizard(),
        ), mock.patch.object(main, "atomic_write_json") as write_config, mock.patch("builtins.print"):
            with self.assertRaises(AbortWizard):
                main.run_configuration_wizard(self.project.config_path)

        write_config.assert_not_called()
        self.assertEqual(self.project.config_path.read_bytes(), original)

    def test_load_config_resolves_relative_paths(self) -> None:
        loaded = self.project.write_config()
        self.assertEqual(loaded.background_dir, self.project.backgrounds.resolve())
        self.assertEqual(loaded.state_dir, self.project.state_dir.resolve())
        self.assertEqual(loaded.listen, "127.0.0.1:8787")
        self.assertEqual(loaded.allowed_origins, frozenset({TEST_ORIGIN}))
        self.assertEqual(loaded.probe_connect_address, "127.0.0.1:443")
        self.assertEqual(loaded.probe_domain_suffix, "home.example.test")
        self.assertEqual(loaded.probe_timeout_seconds, 5.0)

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
                with self.assertRaises(ConfigurationError):
                    self.project.write_config(**overrides)

    def test_config_v1_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "unsupported configuration version"):
            self.project.write_config(version=1)

    def test_rejects_missing_default_background(self) -> None:
        (self.project.backgrounds / "bg.jpg").unlink()
        with self.assertRaises(ConfigurationError):
            self.project.write_config()

    def test_application_directory_must_be_outside_public_web_root(self) -> None:
        loaded = self.project.write_config()
        with mock.patch.object(config, "application_directory", return_value=self.project.root):
            config.validate_application_location(loaded)

        for application_dir in (self.project.backgrounds.parent, self.project.backgrounds):
            with self.subTest(application_dir=application_dir), mock.patch.object(
                config, "application_directory", return_value=application_dir
            ):
                with self.assertRaisesRegex(ConfigurationError, "application directory"):
                    config.validate_application_location(loaded)
