from __future__ import annotations

import hashlib
import json
import threading
import unittest
from unittest import mock

from homelab_state import backgrounds, bestdori
from tests.helpers import FIXED_TIME, FakeBestdoriClient, TemporaryProject, image_bytes


class BestdoriClientTests(unittest.TestCase):
    def test_catalog_filters_supported_characters_rarities_and_release_server(self) -> None:
        client = bestdori.BestdoriClient(opener=mock.Mock())
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
        client = bestdori.BestdoriClient(opener=mock.Mock())
        card = {"server": "jp", "resourceSetName": "card101"}
        payload = image_bytes()
        with mock.patch.object(
            client,
            "_read",
            side_effect=[bestdori.BestdoriAssetMissing("missing"), payload],
        ) as read:
            downloaded, art = client.download_card(card)
        self.assertEqual(downloaded, payload)
        self.assertEqual(art, "normal")
        self.assertIn("card_after_training.png", read.call_args_list[0].args[0])
        self.assertIn("card_normal.png", read.call_args_list[1].args[0])

    def test_fixed_https_origin_and_response_size_are_enforced(self) -> None:
        client = bestdori.BestdoriClient(opener=mock.Mock())
        for url in (
            "http://bestdori.com/api/cards/all.5.json",
            "https://evil.example/api/cards/all.5.json",
            "https://bestdori.com.evil.example/api/cards/all.5.json",
            "https://bestdori.com:444/api/cards/all.5.json",
        ):
            with self.subTest(url=url), self.assertRaises(bestdori.BestdoriUnavailable):
                client._validate_url(url)

        response = mock.MagicMock()
        response.geturl.return_value = bestdori.BESTDORI_CHARACTERS_URL
        response.headers.get_content_type.return_value = "application/json"
        response.headers.get.return_value = str(bestdori.MAX_BESTDORI_CATALOG_BODY + 1)
        response.__enter__.return_value = response
        client.opener.open.return_value = response
        with self.assertRaisesRegex(bestdori.BestdoriUnavailable, "too large"):
            client._read_json(bestdori.BESTDORI_CHARACTERS_URL)
        self.assertTrue(any(isinstance(handler, bestdori._RejectBestdoriRedirects) for handler in bestdori.BestdoriClient().opener.handlers))


class BestdoriDrawTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = TemporaryProject()
        self.config = self.project.write_config()
        self.store = backgrounds.StateStore(self.config.background_dir, self.config.state_file, lambda: FIXED_TIME)
        self.client = FakeBestdoriClient()
        self.history_path = self.config.state_dir / "BestdoriCardDraws.json"
        self.history = bestdori.BestdoriHistoryStore(self.history_path, lambda: FIXED_TIME)
        self.manager = bestdori.BestdoriDrawManager(
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
            hashlib.sha256((self.project.backgrounds / filename).read_bytes()).hexdigest(),
        )
        with self.assertRaises(bestdori.BestdoriPoolExhausted):
            self.manager.draw("character", {3}, 1)

        second = self.manager.draw("random", {5})
        self.assertEqual(second["card"]["id"], 102)
        with self.assertRaises(bestdori.BestdoriPoolExhausted):
            self.manager.draw("random", {3, 5})

    def test_random_draw_survives_character_name_catalog_failure(self) -> None:
        with mock.patch.object(
            self.client,
            "get_characters",
            side_effect=bestdori.BestdoriUnavailable("character catalog unavailable"),
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
        with self.assertRaises(bestdori.BestdoriHistoryError):
            self.manager.draw("random", {3, 5})
        with self.assertRaises(bestdori.BestdoriHistoryError):
            self.manager.reconcile()
        with self.assertRaises(bestdori.BestdoriHistoryError):
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
        with self.assertRaises(bestdori.BestdoriDrawBusy):
            self.manager.draw("random", {5})
        with self.assertRaises(bestdori.BestdoriDrawBusy):
            self.manager.reconcile()
        with self.assertRaises(bestdori.BestdoriDrawBusy):
            self.manager.reset()
        self.client.download_continue.set()
        thread.join(timeout=2)
        self.assertEqual(errors, [])

    def test_history_write_failure_rolls_back_published_image(self) -> None:
        failing_history = mock.Mock()
        failing_history.used_card_ids.return_value = set()
        failing_history.record.side_effect = bestdori.BestdoriHistoryError("disk full")
        manager = bestdori.BestdoriDrawManager(
            self.store,
            self.history_path,
            client=self.client,
            history=failing_history,
            choice=lambda values: values[0],
        )
        before = {path.name for path in self.project.backgrounds.iterdir() if path.is_file()}
        with self.assertRaises(bestdori.BestdoriHistoryError):
            manager.draw("random", {3})
        self.assertEqual(
            {path.name for path in self.project.backgrounds.iterdir() if path.is_file()},
            before,
        )
        self.assertEqual(list(self.store.thumbnail_dir.iterdir()), [])
