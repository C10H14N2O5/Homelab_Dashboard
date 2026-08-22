from __future__ import annotations

import io
import tempfile
import threading
from pathlib import Path

from PIL import Image

from homelab_state import config
from homelab_state.core import atomic_write_json


FIXED_TIME = "2026-07-22T12:00:00Z"
TEST_ORIGIN = "https://home.example.test"


def image_bytes(image_format: str = "PNG") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (3, 2), (35, 110, 180)).save(output, format=image_format)
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
        self.config_path = self.root / config.DEFAULT_CONFIG_NAME

    def close(self) -> None:
        self.temporary.cleanup()

    def write_config(self, **overrides: object) -> config.AppConfig:
        value = {
            "version": 3,
            "backgroundDir": r"web\html\backgrounds",
            "allowedOrigins": [TEST_ORIGIN],
            "listen": "127.0.0.1:8787",
            "stateDir": config.DEFAULT_DATA_DIRECTORY,
            "controlToken": "x" * 43,
            "bingInternationalProxy": "127.0.0.1:7890",
            "probeConnectAddress": "127.0.0.1:443",
            "probeDomainSuffix": "home.example.test",
            "probeTimeoutSeconds": 5,
        }
        value.update(overrides)
        atomic_write_json(self.config_path, value)
        return config.load_config(self.config_path)
