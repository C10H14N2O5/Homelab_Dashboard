from __future__ import annotations

import json
import sys
import unittest
from unittest import mock

from homelab_state import config, launcher
from homelab_state.core import ConfigurationError
from tests.helpers import TEST_ORIGIN, TemporaryProject


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = TemporaryProject()

    def tearDown(self) -> None:
        self.project.close()

    def test_interactive_defaults(self) -> None:
        self.assertEqual(config.DEFAULT_BACKGROUND_DIRECTORY, r"D:\Zoraxy\www\html\backgrounds")
        self.assertEqual(config.DEFAULT_BING_INTERNATIONAL_PROXY, "127.0.0.1:7890")
        self.assertEqual(launcher.MONITOR_REFRESH_SECONDS, 60.0)

    def test_prompt_value_strips_redirected_utf8_bom(self) -> None:
        with mock.patch("builtins.input", return_value="\ufeffD:\\wallpapers  "):
            self.assertEqual(launcher.prompt_value("背景目录", "default"), r"D:\wallpapers")

    def test_wizard_preserves_existing_background_default(self) -> None:
        existing = self.project.write_config()
        prompts: list[tuple[str, str]] = []

        def accept_default(label: str, default: str) -> str:
            prompts.append((label, default))
            return default

        with mock.patch.object(launcher, "prompt_value", side_effect=accept_default), mock.patch.object(
            launcher, "prompt_yes_no", return_value=False
        ), mock.patch.object(sys, "frozen", False, create=True), mock.patch.object(
            launcher, "validate_application_location"
        ) as validate_location, mock.patch("builtins.print"):
            configured = launcher.run_configuration_wizard(self.project.config_path)

        self.assertEqual(prompts[0], ("背景目录", r"web\html\backgrounds"))
        self.assertIn(
            ("Bing 国际版 HTTP 代理", "127.0.0.1:7890"),
            prompts,
        )
        self.assertEqual(configured.background_dir, existing.background_dir)
        validate_location.assert_called_once_with(configured)

    def test_wizard_migrates_existing_v2_config_and_preserves_values(self) -> None:
        existing = self.project.write_config()
        raw = json.loads(self.project.config_path.read_text(encoding="utf-8"))
        raw["version"] = 2
        del raw["bingInternationalProxy"]
        self.project.config_path.write_text(
            json.dumps(raw, ensure_ascii=False),
            encoding="utf-8",
        )
        prompts: list[tuple[str, str]] = []

        def accept_default(label: str, default: str) -> str:
            prompts.append((label, default))
            return default

        with mock.patch.object(launcher, "prompt_value", side_effect=accept_default), mock.patch.object(
            launcher, "prompt_yes_no", return_value=False
        ), mock.patch.object(sys, "frozen", False, create=True), mock.patch.object(
            launcher, "validate_application_location"
        ), mock.patch("builtins.print"):
            migrated = launcher.run_configuration_wizard(self.project.config_path)

        saved = json.loads(self.project.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["version"], 3)
        self.assertEqual(saved["controlToken"], existing.control_token)
        self.assertEqual(saved["bingInternationalProxy"], "127.0.0.1:7890")
        self.assertEqual(migrated.background_dir, existing.background_dir)
        self.assertIn(
            ("Bing 国际版 HTTP 代理", "127.0.0.1:7890"),
            prompts,
        )

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
            "127.0.0.1:7890",
            "127.0.0.1:443",
            "home.example.test",
            "5",
            AbortWizard(),
        ]

        with mock.patch.object(launcher, "prompt_value", side_effect=answers), mock.patch.object(
            launcher,
            "atomic_write_json",
        ) as write_config, mock.patch("builtins.print"):
            with self.assertRaises(AbortWizard):
                launcher.run_configuration_wizard(public_config)

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

        with mock.patch.object(launcher, "prompt_value", side_effect=accept_default), mock.patch.object(
            launcher,
            "validate_application_location",
            side_effect=AbortWizard(),
        ), mock.patch.object(launcher, "atomic_write_json") as write_config, mock.patch("builtins.print"):
            with self.assertRaises(AbortWizard):
                launcher.run_configuration_wizard(self.project.config_path)

        write_config.assert_not_called()
        self.assertEqual(self.project.config_path.read_bytes(), original)

    def test_load_config_resolves_relative_paths(self) -> None:
        loaded = self.project.write_config()
        self.assertEqual(loaded.background_dir, self.project.backgrounds.resolve())
        self.assertEqual(loaded.state_dir, self.project.state_dir.resolve())
        self.assertEqual(loaded.listen, "127.0.0.1:8787")
        self.assertEqual(loaded.allowed_origins, frozenset({TEST_ORIGIN}))
        self.assertEqual(loaded.bing_international_proxy, "127.0.0.1:7890")
        self.assertEqual(
            loaded.bing_international_proxy_url,
            "http://127.0.0.1:7890",
        )
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
            {"bingInternationalProxy": "192.0.2.1:7890"},
            {"bingInternationalProxy": "localhost:7890"},
            {"bingInternationalProxy": "http://127.0.0.1:7890"},
            {"bingInternationalProxy": "user:pass@127.0.0.1:7890"},
            {"bingInternationalProxy": "127.0.0.1:7890/path"},
            {"bingInternationalProxy": "127.0.0.1"},
            {"bingInternationalProxy": "127.0.0.1:0"},
            {"bingInternationalProxy": "127.0.0.1:65536"},
            {"probeDomainSuffix": "*.example.test"},
            {"probeTimeoutSeconds": 6},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(ConfigurationError):
                    self.project.write_config(**overrides)

    def test_old_config_versions_are_rejected(self) -> None:
        for version in (1, 2):
            with self.subTest(version=version), self.assertRaisesRegex(
                ConfigurationError,
                "unsupported configuration version",
            ):
                self.project.write_config(version=version)

    def test_config_requires_exact_v3_fields(self) -> None:
        for mutate in (
            lambda value: value.pop("bingInternationalProxy"),
            lambda value: value.update({"unexpected": True}),
        ):
            self.project.write_config()
            raw = json.loads(self.project.config_path.read_text(encoding="utf-8"))
            mutate(raw)
            self.project.config_path.write_text(
                json.dumps(raw, ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaises(ConfigurationError):
                config.load_config(self.project.config_path)

    def test_ipv6_loopback_proxy_is_canonicalized(self) -> None:
        loaded = self.project.write_config(bingInternationalProxy="[::1]:7890")
        self.assertEqual(loaded.bing_international_proxy, "[::1]:7890")
        self.assertEqual(
            loaded.bing_international_proxy_url,
            "http://[::1]:7890",
        )

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
