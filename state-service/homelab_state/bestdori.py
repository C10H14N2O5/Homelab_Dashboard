from __future__ import annotations

import hashlib
import io
import json
import re
import secrets
import ssl
import threading
import time
from datetime import datetime
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from homelab_state.backgrounds import (
    INVALID_BACKGROUND_NAME_PATTERN,
    MAX_BACKGROUND_UPLOAD_BODY,
    InvalidBackground,
    StateStore,
    validate_background_name,
)
from homelab_state.core import (
    SERVICE_NAME,
    SERVICE_VERSION,
    atomic_write_json,
    file_sha256,
    utc_timestamp,
)


BESTDORI_HISTORY_VERSION = 1
MAX_BESTDORI_CATALOG_BODY = 24 * 1024 * 1024
BESTDORI_BASE_URL = "https://bestdori.com"
BESTDORI_CHARACTERS_URL = f"{BESTDORI_BASE_URL}/api/characters/all.2.json"
BESTDORI_CARDS_URL = f"{BESTDORI_BASE_URL}/api/cards/all.5.json"
BESTDORI_TIMEOUT_SECONDS = 30.0
BESTDORI_CACHE_SECONDS = 300.0
BESTDORI_CHARACTER_IDS = frozenset(range(1, 41))
BESTDORI_RARITIES = frozenset({3, 4, 5})
BESTDORI_SERVERS = ("jp", "en", "tw", "cn", "kr")


class BestdoriUnavailable(RuntimeError):
    pass


class BestdoriAssetMissing(BestdoriUnavailable):
    pass


class BestdoriHistoryError(RuntimeError):
    pass


class BestdoriPoolExhausted(RuntimeError):
    pass


class BestdoriDrawBusy(RuntimeError):
    pass


class _RejectBestdoriRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        raise HTTPError(request.full_url, code, "Bestdori redirects are not allowed", headers, file_pointer)


class BestdoriClient:
    def __init__(
        self,
        *,
        timeout: float = BESTDORI_TIMEOUT_SECONDS,
        cache_seconds: float = BESTDORI_CACHE_SECONDS,
        monotonic=time.monotonic,
        opener=None,
    ) -> None:
        self.timeout = timeout
        self.cache_seconds = cache_seconds
        self.monotonic = monotonic
        self.opener = opener or build_opener(
            _RejectBestdoriRedirects(),
            HTTPSHandler(context=ssl.create_default_context()),
        )
        self._cache_lock = threading.Lock()
        self._characters_cache: tuple[float, list[dict[str, Any]]] | None = None
        self._cards_cache: tuple[float, list[dict[str, Any]]] | None = None

    @staticmethod
    def _validate_url(url: str) -> None:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "bestdori.com"
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 443}
            or parsed.fragment
        ):
            raise BestdoriUnavailable("invalid Bestdori URL")

    def _read(self, url: str, maximum_body: int, accepted_types: frozenset[str]) -> bytes:
        self._validate_url(url)
        request = Request(
            url,
            headers={
                "Accept": ", ".join(sorted(accepted_types)),
                "User-Agent": f"{SERVICE_NAME}/{SERVICE_VERSION}",
            },
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                self._validate_url(response.geturl())
                content_type = response.headers.get_content_type().lower()
                if content_type not in accepted_types:
                    raise BestdoriUnavailable("Bestdori returned an unexpected content type")
                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        declared_length = int(content_length)
                    except ValueError:
                        raise BestdoriUnavailable("Bestdori returned an invalid response length") from None
                    if declared_length < 0 or declared_length > maximum_body:
                        raise BestdoriUnavailable("Bestdori response is too large")
                payload = response.read(maximum_body + 1)
        except HTTPError as error:
            if error.code == HTTPStatus.NOT_FOUND:
                raise BestdoriAssetMissing("Bestdori card image is unavailable") from None
            raise BestdoriUnavailable(f"Bestdori request failed with HTTP {error.code}") from None
        except BestdoriUnavailable:
            raise
        except (OSError, URLError, ssl.SSLError, TimeoutError):
            raise BestdoriUnavailable("unable to reach Bestdori") from None
        if len(payload) > maximum_body:
            raise BestdoriUnavailable("Bestdori response is too large")
        return payload

    def _read_json(self, url: str) -> dict[str, Any]:
        payload = self._read(
            url,
            MAX_BESTDORI_CATALOG_BODY,
            frozenset({"application/json", "text/json"}),
        )
        try:
            value = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise BestdoriUnavailable("Bestdori returned invalid JSON") from None
        if not isinstance(value, dict):
            raise BestdoriUnavailable("Bestdori returned an invalid catalog")
        return value

    @staticmethod
    def _localized_text(value: Any, fallback: str) -> str:
        selected = ""
        if isinstance(value, list):
            for index in (3, 2, 0, 1, 4):
                if index < len(value) and isinstance(value[index], str) and value[index].strip():
                    selected = value[index].strip()
                    break
        elif isinstance(value, str) and value.strip():
            selected = value.strip()
        return (selected or fallback)[:160]

    def get_characters(self) -> list[dict[str, Any]]:
        now = self.monotonic()
        with self._cache_lock:
            if self._characters_cache is not None and self._characters_cache[0] > now:
                return [dict(character) for character in self._characters_cache[1]]
        raw = self._read_json(BESTDORI_CHARACTERS_URL)
        characters: list[dict[str, Any]] = []
        for character_id in sorted(BESTDORI_CHARACTER_IDS):
            value = raw.get(str(character_id))
            if not isinstance(value, dict):
                raise BestdoriUnavailable("Bestdori character catalog is incomplete")
            name = self._localized_text(
                value.get("characterName", value.get("name")),
                f"角色 {character_id}",
            )
            characters.append({"id": character_id, "name": name})
        with self._cache_lock:
            self._characters_cache = (now + self.cache_seconds, characters)
        return [dict(character) for character in characters]

    @staticmethod
    def _card_server(released_at: Any) -> str | None:
        if not isinstance(released_at, list):
            return None
        for index, server in enumerate(BESTDORI_SERVERS):
            if index < len(released_at) and released_at[index] is not None:
                return server
        return None

    def get_cards(self) -> list[dict[str, Any]]:
        now = self.monotonic()
        with self._cache_lock:
            if self._cards_cache is not None and self._cards_cache[0] > now:
                return [dict(card) for card in self._cards_cache[1]]
        raw = self._read_json(BESTDORI_CARDS_URL)
        cards: list[dict[str, Any]] = []
        for card_id_text, value in raw.items():
            if not isinstance(card_id_text, str) or not card_id_text.isdigit() or not isinstance(value, dict):
                continue
            card_id = int(card_id_text)
            character_id = value.get("characterId")
            rarity = value.get("rarity")
            resource_set = value.get("resourceSetName")
            server = self._card_server(value.get("releasedAt"))
            if (
                isinstance(character_id, bool)
                or not isinstance(character_id, int)
                or character_id not in BESTDORI_CHARACTER_IDS
                or isinstance(rarity, bool)
                or not isinstance(rarity, int)
                or rarity not in BESTDORI_RARITIES
                or not isinstance(resource_set, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]+", resource_set)
                or server is None
            ):
                continue
            cards.append(
                {
                    "id": card_id,
                    "characterId": character_id,
                    "rarity": rarity,
                    "resourceSetName": resource_set,
                    "title": self._localized_text(value.get("prefix"), f"卡面 {card_id}"),
                    "server": server,
                }
            )
        if not cards:
            raise BestdoriUnavailable("Bestdori card catalog contains no supported cards")
        with self._cache_lock:
            self._cards_cache = (now + self.cache_seconds, cards)
        return [dict(card) for card in cards]

    def _asset_url(self, card: dict[str, Any], art: str) -> str:
        if art not in {"after_training", "normal"}:
            raise BestdoriUnavailable("invalid Bestdori card art type")
        server = card.get("server")
        resource_set = card.get("resourceSetName")
        if server not in BESTDORI_SERVERS or not isinstance(resource_set, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]+", resource_set
        ):
            raise BestdoriUnavailable("invalid Bestdori card metadata")
        return (
            f"{BESTDORI_BASE_URL}/assets/{server}/characters/resourceset/"
            f"{resource_set}_rip/card_{art}.png"
        )

    def download_card(self, card: dict[str, Any]) -> tuple[bytes, str]:
        for art in ("after_training", "normal"):
            try:
                payload = self._read(
                    self._asset_url(card, art),
                    MAX_BACKGROUND_UPLOAD_BODY,
                    frozenset({"image/png"}),
                )
                return payload, art
            except BestdoriAssetMissing:
                continue
        raise BestdoriAssetMissing("Bestdori card image is unavailable")


class BestdoriHistoryStore:
    def __init__(self, path: Path, clock=utc_timestamp) -> None:
        self.path = path.resolve()
        self.clock = clock
        self._lock = threading.Lock()

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {"version": BESTDORI_HISTORY_VERSION, "records": {}}

    @staticmethod
    def _validate_record(card_id_text: str, record: Any) -> dict[str, Any]:
        if not card_id_text.isdigit() or not isinstance(record, dict):
            raise BestdoriHistoryError("Bestdori draw history is invalid")
        if set(record) != {"filename", "sha256", "drawnAt", "card"}:
            raise BestdoriHistoryError("Bestdori draw history is invalid")
        card = record.get("card")
        expected_card_fields = {"id", "characterId", "characterName", "rarity", "title", "art"}
        if (
            not isinstance(record.get("filename"), str)
            or not isinstance(record.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"])
            or not isinstance(record.get("drawnAt"), str)
            or not isinstance(card, dict)
            or set(card) != expected_card_fields
            or isinstance(card.get("id"), bool)
            or not isinstance(card.get("id"), int)
            or str(card["id"]) != card_id_text
            or isinstance(card.get("characterId"), bool)
            or not isinstance(card.get("characterId"), int)
            or card["characterId"] not in BESTDORI_CHARACTER_IDS
            or not isinstance(card.get("characterName"), str)
            or isinstance(card.get("rarity"), bool)
            or not isinstance(card.get("rarity"), int)
            or card["rarity"] not in BESTDORI_RARITIES
            or not isinstance(card.get("title"), str)
            or card.get("art") not in {"after_training", "normal"}
        ):
            raise BestdoriHistoryError("Bestdori draw history is invalid")
        try:
            validate_background_name(record["filename"])
            datetime.fromisoformat(record["drawnAt"].replace("Z", "+00:00"))
        except (InvalidBackground, ValueError):
            raise BestdoriHistoryError("Bestdori draw history is invalid") from None
        return {
            "filename": record["filename"],
            "sha256": record["sha256"],
            "drawnAt": record["drawnAt"],
            "card": dict(card),
        }

    def _read_locked(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return self._empty()
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise BestdoriHistoryError("Bestdori draw history is unavailable") from None
        if not isinstance(value, dict) or set(value) != {"version", "records"}:
            raise BestdoriHistoryError("Bestdori draw history is invalid")
        if value["version"] != BESTDORI_HISTORY_VERSION or not isinstance(value["records"], dict):
            raise BestdoriHistoryError("Bestdori draw history is invalid")
        records = {
            card_id: self._validate_record(card_id, record)
            for card_id, record in value["records"].items()
            if isinstance(card_id, str)
        }
        if len(records) != len(value["records"]):
            raise BestdoriHistoryError("Bestdori draw history is invalid")
        return {"version": BESTDORI_HISTORY_VERSION, "records": records}

    def used_card_ids(self) -> set[int]:
        with self._lock:
            return {int(card_id) for card_id in self._read_locked()["records"]}

    def record(self, filename: str, sha256: str, card: dict[str, Any]) -> None:
        with self._lock:
            value = self._read_locked()
            card_id = str(card["id"])
            if card_id in value["records"]:
                raise BestdoriPoolExhausted("card has already been drawn")
            value["records"][card_id] = {
                "filename": filename,
                "sha256": sha256,
                "drawnAt": self.clock(),
                "card": dict(card),
            }
            try:
                atomic_write_json(self.path, value)
            except OSError:
                raise BestdoriHistoryError("unable to save Bestdori draw history") from None

    @staticmethod
    def _file_sha256(path: Path) -> str:
        return file_sha256(path)

    def reconcile(self, background_dir: Path) -> dict[str, int]:
        with self._lock:
            value = self._read_locked()
            removed = 0
            for card_id, record in list(value["records"].items()):
                path = background_dir / record["filename"]
                try:
                    valid = path.is_file() and not path.is_symlink() and self._file_sha256(path) == record["sha256"]
                except OSError:
                    raise BestdoriHistoryError("unable to inspect a recorded background") from None
                if not valid:
                    del value["records"][card_id]
                    removed += 1
            if removed:
                try:
                    atomic_write_json(self.path, value)
                except OSError:
                    raise BestdoriHistoryError("unable to save Bestdori draw history") from None
            return {"removed": removed, "remaining": len(value["records"])}

    def reset(self) -> dict[str, int]:
        with self._lock:
            value = self._read_locked()
            removed = len(value["records"])
            try:
                atomic_write_json(self.path, self._empty())
            except OSError:
                raise BestdoriHistoryError("unable to save Bestdori draw history") from None
            return {"removed": removed, "remaining": 0}


class BestdoriDrawManager:
    def __init__(
        self,
        store: StateStore,
        history_path: Path,
        *,
        client: BestdoriClient | None = None,
        history: BestdoriHistoryStore | None = None,
        choice=secrets.choice,
    ) -> None:
        self.store = store
        self.client = client or BestdoriClient()
        self.history = history or BestdoriHistoryStore(history_path)
        self.choice = choice
        self._operation_lock = threading.Lock()

    def characters(self) -> list[dict[str, Any]]:
        return self.client.get_characters()

    @staticmethod
    def _safe_filename_component(value: str, fallback: str) -> str:
        cleaned = INVALID_BACKGROUND_NAME_PATTERN.sub("_", value)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._")
        return cleaned or fallback

    def _background_name(self, card: dict[str, Any], art: str) -> str:
        rarity_names = {3: "三星", 4: "四星", 5: "五星"}
        character = self._safe_filename_component(card["characterName"], f"角色{card['characterId']}")
        title = self._safe_filename_component(card["title"], "无标题")
        suffix = f"_{card['id']}_{art}.png"
        prefix = f"{character}_{rarity_names[card['rarity']]}_"
        title = title[: max(1, 220 - len(prefix) - len(suffix))]
        return validate_background_name(f"{prefix}{title}{suffix}")

    def draw(
        self,
        mode: str,
        rarities: set[int],
        character_id: int | None = None,
    ) -> dict[str, Any]:
        if (
            mode not in {"character", "random"}
            or not rarities
            or not rarities <= BESTDORI_RARITIES
            or (
                mode == "character"
                and (
                    isinstance(character_id, bool)
                    or not isinstance(character_id, int)
                    or character_id not in BESTDORI_CHARACTER_IDS
                )
            )
            or (mode == "random" and character_id is not None)
        ):
            raise ValueError("invalid Bestdori draw filters")
        if not self._operation_lock.acquire(blocking=False):
            raise BestdoriDrawBusy("another Bestdori operation is in progress")
        try:
            used_ids = self.history.used_card_ids()
            cards = self.client.get_cards()
            try:
                character_names = {
                    character["id"]: character["name"]
                    for character in self.client.get_characters()
                }
            except BestdoriUnavailable:
                # The card catalog already contains the stable character ID.
                # Keep random draws usable when only the display-name catalog is unavailable.
                character_names = {}
            eligible = [
                card
                for card in cards
                if card["id"] not in used_ids
                and card["rarity"] in rarities
                and (mode == "random" or card["characterId"] == character_id)
            ]
            if not eligible:
                raise BestdoriPoolExhausted("no undrawn cards match the selected filters")
            selected = dict(self.choice(eligible))
            selected["characterName"] = character_names.get(
                selected["characterId"],
                f"角色 {selected['characterId']}",
            )
            image, art = self.client.download_card(selected)
            card_metadata = {
                "id": selected["id"],
                "characterId": selected["characterId"],
                "characterName": selected["characterName"],
                "rarity": selected["rarity"],
                "title": selected["title"],
                "art": art,
            }
            filename = self._background_name(card_metadata, art)
            background = self.store.upload_background(filename, io.BytesIO(image), len(image))
            digest = hashlib.sha256(image).hexdigest()
            try:
                self.history.record(background["background"], digest, card_metadata)
            except Exception:
                self.store.rollback_uploaded_background(
                    background["background"],
                    digest,
                )
                raise
            return {"background": background, "card": card_metadata}
        finally:
            self._operation_lock.release()

    def reconcile(self) -> dict[str, int]:
        if not self._operation_lock.acquire(blocking=False):
            raise BestdoriDrawBusy("another Bestdori operation is in progress")
        try:
            return self.history.reconcile(self.store.background_dir)
        finally:
            self._operation_lock.release()

    def reset(self) -> dict[str, int]:
        if not self._operation_lock.acquire(blocking=False):
            raise BestdoriDrawBusy("another Bestdori operation is in progress")
        try:
            return self.history.reset()
        finally:
            self._operation_lock.release()
