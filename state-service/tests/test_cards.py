from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from homelab_state import cards
from tests.helpers import FIXED_TIME


DOMAIN_SUFFIX = "home.example.test"
SIMPLE_SVG = '<svg viewBox="0 0 24 24"><path d="M1 1h22v22H1z"/></svg>'
GRADIENT_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
<defs><linearGradient id="tone" x1="0" x2="1"><stop offset="0" stop-color="#08B962"/>
<stop offset="1" stop-color="rgb(1, 2, 3)"/></linearGradient></defs>
<path id="shape" fill="url(#tone)" d="M1 1h22v22H1z"/></svg>"""


def service_card(
    identifier: str = "synology-nas",
    *,
    icon_svg: str = GRADIENT_SVG,
    url: str = "https://nas.home.example.test/",
    probe_url: str | None = None,
) -> dict[str, object]:
    return {
        "id": identifier,
        "iconSvg": icon_svg,
        "name": "Synology NAS",
        "description": "数据存储中心",
        "url": url,
        "probeUrl": probe_url,
    }


def quick_link(identifier: str = "github", *, icon_svg: str = SIMPLE_SVG) -> dict[str, object]:
    return {
        "id": identifier,
        "iconSvg": icon_svg,
        "name": "GitHub",
        "url": "https://github.com/",
    }


def initialize_request() -> dict[str, object]:
    return {
        "version": 1,
        "services": [service_card()],
        "quickLinks": [quick_link()],
    }


class CardsStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.state_file = Path(self.temporary.name) / cards.CARDS_STATE_FILE_NAME
        self.store = cards.CardsStore(self.state_file, DOMAIN_SUFFIX, lambda: FIXED_TIME)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_missing_state_is_explicit_and_does_not_create_a_file(self) -> None:
        self.assertEqual(
            self.store.get(),
            {"version": 1, "initialized": False, "channels": None},
        )
        self.assertFalse(self.state_file.exists())
        with self.assertRaises(cards.CardsNotInitialized):
            self.store.update(
                {"version": 1, "channel": "services", "expectedRevision": 1, "cards": []}
            )

    def test_initialize_both_channels_atomically_and_restart(self) -> None:
        result = self.store.initialize(initialize_request())
        self.assertTrue(result["initialized"])
        self.assertEqual(result["channels"]["services"]["revision"], 1)
        self.assertEqual(result["channels"]["quickLinks"]["revision"], 1)
        self.assertEqual(result["channels"]["services"]["updatedAt"], FIXED_TIME)
        canonical_svg = result["channels"]["services"]["cards"][0]["iconSvg"]
        self.assertIn("card-", canonical_svg)
        self.assertNotIn('id="tone"', canonical_svg)
        self.assertTrue(self.state_file.is_file())

        restarted = cards.CardsStore(self.state_file, DOMAIN_SUFFIX, lambda: FIXED_TIME)
        self.assertEqual(restarted.get(), result)
        with self.assertRaises(cards.CardsAlreadyInitialized):
            restarted.initialize(initialize_request())

    def test_update_is_per_channel_and_preserves_explicit_empty_arrays(self) -> None:
        initial = self.store.initialize(initialize_request())
        updated = self.store.update(
            {
                "version": 1,
                "channel": "services",
                "expectedRevision": 1,
                "cards": [],
            }
        )
        self.assertEqual(updated["channels"]["services"]["revision"], 2)
        self.assertEqual(updated["channels"]["services"]["cards"], [])
        self.assertEqual(updated["channels"]["quickLinks"], initial["channels"]["quickLinks"])

        with self.assertRaises(cards.CardsRevisionConflict) as conflict:
            self.store.update(
                {
                    "version": 1,
                    "channel": "services",
                    "expectedRevision": 1,
                    "cards": [],
                }
            )
        self.assertEqual(conflict.exception.current_revision, 2)

    def test_concurrent_initialize_has_one_winner(self) -> None:
        gate = threading.Barrier(3)
        results: list[str] = []

        def initialize() -> None:
            gate.wait()
            try:
                self.store.initialize(initialize_request())
            except cards.CardsAlreadyInitialized:
                results.append("conflict")
            else:
                results.append("created")

        threads = [threading.Thread(target=initialize) for _ in range(2)]
        for thread in threads:
            thread.start()
        gate.wait()
        for thread in threads:
            thread.join(timeout=2)
        self.assertEqual(sorted(results), ["conflict", "created"])
        self.assertEqual(self.store.get()["channels"]["services"]["revision"], 1)

    def test_concurrent_same_channel_update_has_one_winner(self) -> None:
        self.store.initialize(initialize_request())
        gate = threading.Barrier(3)
        results: list[str] = []

        def update() -> None:
            gate.wait()
            try:
                self.store.update(
                    {
                        "version": 1,
                        "channel": "services",
                        "expectedRevision": 1,
                        "cards": [],
                    }
                )
            except cards.CardsRevisionConflict:
                results.append("conflict")
            else:
                results.append("updated")

        threads = [threading.Thread(target=update) for _ in range(2)]
        for thread in threads:
            thread.start()
        gate.wait()
        for thread in threads:
            thread.join(timeout=2)
        self.assertEqual(sorted(results), ["conflict", "updated"])
        self.assertEqual(self.store.get()["channels"]["services"]["revision"], 2)

    def test_concurrent_different_channel_updates_both_persist(self) -> None:
        self.store.initialize(initialize_request())
        gate = threading.Barrier(3)
        failures: list[Exception] = []

        def update(channel: str) -> None:
            gate.wait()
            try:
                self.store.update(
                    {
                        "version": 1,
                        "channel": channel,
                        "expectedRevision": 1,
                        "cards": [],
                    }
                )
            except Exception as error:
                failures.append(error)

        threads = [
            threading.Thread(target=update, args=("services",)),
            threading.Thread(target=update, args=("quickLinks",)),
        ]
        for thread in threads:
            thread.start()
        gate.wait()
        for thread in threads:
            thread.join(timeout=2)
        self.assertEqual(failures, [])
        state = self.store.get()
        self.assertEqual(state["channels"]["services"]["revision"], 2)
        self.assertEqual(state["channels"]["quickLinks"]["revision"], 2)
        self.assertEqual(state["channels"]["services"]["cards"], [])
        self.assertEqual(state["channels"]["quickLinks"]["cards"], [])

    def test_atomic_write_failure_preserves_file_memory_and_revision(self) -> None:
        before = self.store.initialize(initialize_request())
        file_before = self.state_file.read_bytes()
        with mock.patch.object(cards, "atomic_write_json", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.store.update(
                    {
                        "version": 1,
                        "channel": "services",
                        "expectedRevision": 1,
                        "cards": [],
                    }
                )
        self.assertEqual(self.store.get(), before)
        self.assertEqual(self.state_file.read_bytes(), file_before)

    def test_initialize_write_failure_remains_uninitialized(self) -> None:
        with mock.patch.object(cards, "atomic_write_json", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.store.initialize(initialize_request())
        self.assertEqual(
            self.store.get(),
            {"version": 1, "initialized": False, "channels": None},
        )
        self.assertFalse(self.state_file.exists())

    def test_corrupt_and_incompatible_files_fail_closed(self) -> None:
        cases = (
            b"",
            b'{"version":2,"initialized":true,"channels":{}}',
            b'{"version":1,"version":1,"initialized":true,"channels":{}}',
            b"[]",
        )
        for index, content in enumerate(cases):
            with self.subTest(index=index):
                path = Path(self.temporary.name) / f"damaged-{index}.json"
                path.write_bytes(content)
                store = cards.CardsStore(path, DOMAIN_SUFFIX, lambda: FIXED_TIME)
                with self.assertRaises(cards.CardsStateUnavailable):
                    store.get()
                with self.assertRaises(cards.CardsStateUnavailable):
                    store.initialize(initialize_request())
                with self.assertRaises(cards.CardsStateUnavailable):
                    store.update(
                        {
                            "version": 1,
                            "channel": "services",
                            "expectedRevision": 1,
                            "cards": [],
                        }
                    )
                self.assertEqual(path.read_bytes(), content)

    def test_request_shapes_versions_revisions_and_card_limits_are_strict(self) -> None:
        invalid_initializers = (
            {"version": True, "services": [], "quickLinks": []},
            {"version": 1, "services": [], "quickLinks": [], "extra": True},
            {"version": 1, "services": [service_card()] * 33, "quickLinks": []},
            {"version": 1, "services": [service_card(), service_card()], "quickLinks": []},
        )
        for request in invalid_initializers:
            with self.subTest(request=request):
                with self.assertRaises(cards.InvalidCards):
                    self.store.initialize(request)
                self.assertFalse(self.state_file.exists())

        self.store.initialize(initialize_request())
        invalid_updates = (
            {"version": 1, "channel": "services", "expectedRevision": True, "cards": []},
            {"version": 1, "channel": "other", "expectedRevision": 1, "cards": []},
            {"version": 1, "channel": "services", "expectedRevision": 1, "cards": [], "x": 1},
        )
        for request in invalid_updates:
            with self.subTest(request=request):
                with self.assertRaises(cards.InvalidCards):
                    self.store.update(request)

    def test_card_fields_urls_and_probe_policy(self) -> None:
        valid_external_navigation = service_card(
            url="https://external.example/launch#section",
            probe_url="https://nas.home.example.test/health",
        )
        validated = cards.validate_cards([valid_external_navigation], "services", DOMAIN_SUFFIX)
        self.assertEqual(validated[0]["url"], valid_external_navigation["url"])
        self.assertEqual(validated[0]["probeUrl"], valid_external_navigation["probeUrl"])
        self.assertEqual(cards.validate_cards([quick_link()], "quickLinks", DOMAIN_SUFFIX)[0]["url"], "https://github.com/")

        invalid_cards = (
            {**service_card(), "description": ""},
            {**service_card(), "name": " padded "},
            {**service_card(), "url": "http://nas.home.example.test/"},
            {**service_card(), "url": "https://user:pass@nas.home.example.test/"},
            {**service_card(), "url": "https://external.example/"},
            {**service_card(), "probeUrl": ""},
            {**service_card(), "probeUrl": "https://nested.nas.home.example.test/"},
            {**service_card(), "unexpected": True},
            {**service_card(identifier="bad id")},
        )
        for card in invalid_cards:
            with self.subTest(card=card):
                with self.assertRaises(cards.InvalidCards):
                    cards.validate_cards([card], "services", DOMAIN_SUFFIX)


class SvgSanitizerTests(unittest.TestCase):
    def test_canonicalization_rewrites_ids_and_is_idempotent(self) -> None:
        canonical = cards.sanitize_svg(GRADIENT_SVG, "gemini")
        self.assertIn('xmlns="http://www.w3.org/2000/svg"', canonical)
        self.assertIn("card-", canonical)
        self.assertNotIn('id="tone"', canonical)
        self.assertNotIn("url(#tone)", canonical)
        self.assertEqual(cards.sanitize_svg(canonical, "gemini"), canonical)

    def test_xml_declaration_and_safe_local_gradient_href_are_supported(self) -> None:
        source = """<?xml version="1.0"?>
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">
          <defs><linearGradient id="base"><stop offset="0" stop-color="red"/></linearGradient>
          <linearGradient id="derived" href="#base"/></defs>
          <path fill="url(#derived)" d="M0 0h10v10z"/>
        </svg>"""
        canonical = cards.sanitize_svg(source, "safe")
        self.assertIn("href=", canonical)
        self.assertNotIn("#base", canonical)

    def test_claude_like_title_and_style_attributes_are_removed(self) -> None:
        source = """<svg fill="currentColor" fill-rule="evenodd" height="1em"
        style="flex:none;line-height:1" viewBox="0 0 24 24" width="1em"
        xmlns="http://www.w3.org/2000/svg"><title>Claude</title>
        <g style="color:red"><path style="fill:url(https://evil.example/icon.svg)"
        d="M4.7 15.9L9.4 13.3z"/></g></svg>"""
        canonical = cards.sanitize_svg(source, "claude")
        self.assertNotIn("<title", canonical)
        self.assertNotIn("style=", canonical)
        self.assertNotIn("Claude", canonical)
        self.assertNotIn("evil.example", canonical)
        self.assertIn('fill="currentColor"', canonical)
        self.assertIn("<path", canonical)
        self.assertEqual(cards.sanitize_svg(canonical, "claude"), canonical)

        boundary_title = "界" * 85 + "x"
        self.assertEqual(len(boundary_title.encode("utf-8")), cards.MAX_SVG_TITLE_BYTES)
        boundary = cards.sanitize_svg(
            f'<svg><title>{boundary_title}</title><path d="M0 0"/></svg>',
            "title-boundary",
        )
        self.assertNotIn("<title", boundary)

    def test_invalid_title_shapes_are_rejected(self) -> None:
        invalid_titles = (
            '<svg><title>One</title><title>Two</title><path d="M0 0"/></svg>',
            '<svg><g><title>Nested</title></g><path d="M0 0"/></svg>',
            '<svg><title lang="en">Label</title><path d="M0 0"/></svg>',
            '<svg><title><path d="M0 0"/></title><path d="M0 0"/></svg>',
            '<svg><title></title><path d="M0 0"/></svg>',
            '<svg><title>   </title><path d="M0 0"/></svg>',
            '<svg><title>' + ("界" * 86) + '</title><path d="M0 0"/></svg>',
            '<svg><title>unsafe\x01control</title><path d="M0 0"/></svg>',
        )
        for source in invalid_titles:
            with self.subTest(source=source):
                with self.assertRaises(cards.InvalidCards):
                    cards.sanitize_svg(source, "invalid-title")

    def test_title_counts_toward_the_svg_element_limit(self) -> None:
        source = (
            "<svg><title>Element budget</title>"
            + "<path d='M0 0'/>" * 255
            + "</svg>"
        )
        with self.assertRaises(cards.InvalidCards):
            cards.sanitize_svg(source, "title-budget")

    def test_dangerous_or_unsupported_svg_is_rejected(self) -> None:
        dangerous = (
            '<!DOCTYPE svg><svg><path d="M0 0"/></svg>',
            '<!DOCTYPE svg [<!ENTITY x "x">]><svg><path d="M0 0"/></svg>',
            '<svg xmlns="http://evil.example/svg"><path d="M0 0"/></svg>',
            '<svg><script>alert(1)</script><path d="M0 0"/></svg>',
            '<svg><style>path{fill:red}</style><path d="M0 0"/></svg>',
            '<svg><path class="painted" d="M0 0"/></svg>',
            '<svg xmlns:unsafe="https://evil.example"><path unsafe:style="fill:red" d="M0 0"/></svg>',
            '<svg><foreignObject/><path d="M0 0"/></svg>',
            '<svg><image href="data:image/png;base64,AA=="/><path d="M0 0"/></svg>',
            '<svg><a href="https://evil.example"><path d="M0 0"/></a></svg>',
            '<svg><defs><path id="x" d="M0 0"/></defs><use href="#x"/></svg>',
            '<svg><animate attributeName="x"/><path d="M0 0"/></svg>',
            '<svg onload="alert(1)"><path d="M0 0"/></svg>',
            '<svg xml:base="https://evil.example"><path d="M0 0"/></svg>',
            '<svg><defs><linearGradient id="x" href="https://evil.example/x"/></defs><path fill="url(#x)" d="M0 0"/></svg>',
            '<svg><path fill="url(data:image/svg+xml,evil)" d="M0 0"/></svg>',
            '<svg><path fill="javascript:alert(1)" d="M0 0"/></svg>',
            '<svg><defs><linearGradient id="x"/><linearGradient id="x"/></defs><path d="M0 0"/></svg>',
            '<svg><path fill="url(#missing)" d="M0 0"/></svg>',
            '<html><path d="M0 0"/></html>',
            '<svg><text>unsafe</text><path d="M0 0"/></svg>',
        )
        for source in dangerous:
            with self.subTest(source=source):
                with self.assertRaises(cards.InvalidCards):
                    cards.sanitize_svg(source, "unsafe")

    def test_size_element_and_depth_limits_are_enforced(self) -> None:
        with self.assertRaises(cards.InvalidCards):
            cards.sanitize_svg("<svg>" + "<path d='M0 0'/>" * 256 + "</svg>", "many")
        nested = "<svg>" + "<g>" * 16 + "<path d='M0 0'/>" + "</g>" * 16 + "</svg>"
        with self.assertRaises(cards.InvalidCards):
            cards.sanitize_svg(nested, "deep")
        with self.assertRaises(cards.InvalidCards):
            cards.sanitize_svg("<svg><path d='" + "M0 0 " * 6000 + "'/></svg>", "large")
        with self.assertRaises(cards.InvalidCards):
            cards.sanitize_svg("<svg><path d='" + "M0 0 " * 14000 + "'/></svg>", "raw-large")


if __name__ == "__main__":
    unittest.main()
