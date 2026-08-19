from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from homelab_state.core import atomic_write_json, utc_timestamp
from homelab_state.probe import validate_probe_target


CARDS_VERSION = 1
CARDS_STATE_FILE_NAME = "DashboardCards.json"
MAX_CARDS_PER_CHANNEL = 32
MAX_CARD_ID_LENGTH = 64
MAX_CARD_NAME_LENGTH = 80
MAX_CARD_DESCRIPTION_LENGTH = 160
MAX_CARD_URL_LENGTH = 2048
MAX_RAW_SVG_BYTES = 64 * 1024
MAX_CANONICAL_SVG_BYTES = 24 * 1024
MAX_SVG_ELEMENTS = 256
MAX_SVG_DEPTH = 16
MAX_SVG_TITLE_BYTES = 256
MAX_CARDS_REQUEST_BODY = 1024 * 1024
MAX_CARDS_STATE_FILE_BYTES = 2 * MAX_CARDS_REQUEST_BODY + 64 * 1024

CHANNELS = ("services", "quickLinks")
SERVICE_CARD_FIELDS = {"id", "iconSvg", "name", "description", "url", "probeUrl"}
QUICK_LINK_CARD_FIELDS = {"id", "iconSvg", "name", "url"}

SVG_NAMESPACE = "http://www.w3.org/2000/svg"
XLINK_NAMESPACE = "http://www.w3.org/1999/xlink"

CARD_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}")
SVG_ID_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_.:-]{0,127}")
FORBIDDEN_XML_DECLARATION_PATTERN = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
LEADING_XML_DECLARATION_PATTERN = re.compile(r"^\s*<\?xml\s+[^?]*\?>", re.IGNORECASE)
INTERNAL_URL_PATTERN = re.compile(r"url\(\s*#([A-Za-z_][A-Za-z0-9_.:-]{0,127})\s*\)")
SAFE_COLOR_PATTERN = re.compile(
    r"(?:none|currentColor|transparent|inherit|[A-Za-z]+|#[0-9A-Fa-f]{3,8}|"
    r"(?:rgb|rgba|hsl|hsla)\([0-9eE+.,%\s-]+\))"
)

ALLOWED_SVG_ELEMENTS = {
    "svg",
    "g",
    "path",
    "rect",
    "circle",
    "ellipse",
    "line",
    "polyline",
    "polygon",
    "defs",
    "linearGradient",
    "radialGradient",
    "stop",
    "clipPath",
    "mask",
    "symbol",
    "title",
}
DRAWABLE_SVG_ELEMENTS = {
    "path",
    "rect",
    "circle",
    "ellipse",
    "line",
    "polyline",
    "polygon",
}
GLOBAL_SVG_ATTRIBUTES = {
    "id",
    "fill",
    "fill-opacity",
    "fill-rule",
    "clip-rule",
    "stroke",
    "stroke-width",
    "stroke-linecap",
    "stroke-linejoin",
    "stroke-miterlimit",
    "stroke-opacity",
    "opacity",
    "transform",
    "clip-path",
    "mask",
    "vector-effect",
    "color",
}
ELEMENT_SVG_ATTRIBUTES = {
    "svg": {"viewBox", "width", "height", "x", "y", "preserveAspectRatio", "focusable"},
    "g": set(),
    "path": {"d", "pathLength"},
    "rect": {"x", "y", "width", "height", "rx", "ry", "pathLength"},
    "circle": {"cx", "cy", "r", "pathLength"},
    "ellipse": {"cx", "cy", "rx", "ry", "pathLength"},
    "line": {"x1", "y1", "x2", "y2", "pathLength"},
    "polyline": {"points", "pathLength"},
    "polygon": {"points", "pathLength"},
    "defs": set(),
    "linearGradient": {
        "x1",
        "y1",
        "x2",
        "y2",
        "gradientUnits",
        "gradientTransform",
        "spreadMethod",
        "href",
    },
    "radialGradient": {
        "cx",
        "cy",
        "r",
        "fx",
        "fy",
        "fr",
        "gradientUnits",
        "gradientTransform",
        "spreadMethod",
        "href",
    },
    "stop": {"offset", "stop-color", "stop-opacity"},
    "clipPath": {"clipPathUnits"},
    "mask": {"x", "y", "width", "height", "maskUnits", "maskContentUnits"},
    "symbol": {"viewBox", "preserveAspectRatio"},
    "title": set(),
}
COLOR_ATTRIBUTES = {"fill", "stroke", "color", "stop-color"}
URL_REFERENCE_ATTRIBUTES = {"fill", "stroke", "clip-path", "mask"}
HREF_ELEMENTS = {"linearGradient", "radialGradient"}


class InvalidCards(ValueError):
    def __init__(self, message: str, *, card_id: str | None = None) -> None:
        super().__init__(message)
        self.card_id = (
            card_id
            if isinstance(card_id, str) and CARD_ID_PATTERN.fullmatch(card_id)
            else None
        )


class CardsAlreadyInitialized(RuntimeError):
    pass


class CardsNotInitialized(RuntimeError):
    pass


class CardsStateUnavailable(RuntimeError):
    pass


class CardsRevisionConflict(RuntimeError):
    def __init__(self, current_revision: int) -> None:
        super().__init__("card channel revision does not match")
        self.current_revision = current_revision


def _strict_json_loads(value: str) -> Any:
    def reject_duplicate_fields(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON field: {key}")
            result[key] = item
        return result

    return json.loads(value, object_pairs_hook=reject_duplicate_fields)


def _contains_control(value: str) -> bool:
    return any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)


def _contains_forbidden_xml_character(value: str) -> bool:
    return any(
        codepoint not in {0x09, 0x0A, 0x0D}
        and not 0x20 <= codepoint <= 0xD7FF
        and not 0xE000 <= codepoint <= 0xFFFD
        and not 0x10000 <= codepoint <= 0x10FFFF
        for codepoint in map(ord, value)
    )


def _validate_text(value: Any, field: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or len(value) > maximum
        or _contains_control(value)
    ):
        raise InvalidCards(f"{field} must be a non-empty trimmed string of at most {maximum} characters")
    return value


def _validate_navigation_url(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_CARD_URL_LENGTH
        or value.strip() != value
        or _contains_control(value)
        or any(character.isspace() for character in value)
        or "\\" in value
    ):
        raise InvalidCards(f"{field} must be a valid HTTPS URL")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (AttributeError, ValueError):
        raise InvalidCards(f"{field} must be a valid HTTPS URL") from None
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port is not None and not 1 <= port <= 65535
    ):
        raise InvalidCards(f"{field} must be a valid HTTPS URL")
    return value


def _split_svg_name(name: str) -> tuple[str, str]:
    if not name.startswith("{"):
        return "", name
    namespace, separator, local = name[1:].partition("}")
    if not separator:
        return "", name
    return namespace, local


def _normalize_svg_attribute_name(name: str) -> tuple[str, str]:
    namespace, local = _split_svg_name(name)
    if namespace == XLINK_NAMESPACE and local == "href":
        return "", "href"
    return namespace, local


def _canonical_svg_reference(value: str, id_mapping: dict[str, str]) -> str:
    match = INTERNAL_URL_PATTERN.fullmatch(value)
    if match is None or match.group(1) not in id_mapping:
        raise InvalidCards("SVG references must target an ID in the same icon")
    return f"url(#{id_mapping[match.group(1)]})"


def _canonical_href(value: str, id_mapping: dict[str, str]) -> str:
    if not value.startswith("#") or not SVG_ID_PATTERN.fullmatch(value[1:]):
        raise InvalidCards("SVG href must target an ID in the same icon")
    target = value[1:]
    if target not in id_mapping:
        raise InvalidCards("SVG href must target an ID in the same icon")
    return f"#{id_mapping[target]}"


def _validate_svg_attribute_value(
    element_name: str,
    attribute_name: str,
    value: str,
    id_mapping: dict[str, str],
) -> str:
    if not isinstance(value, str) or _contains_control(value) or len(value) > MAX_RAW_SVG_BYTES:
        raise InvalidCards("SVG contains an invalid attribute value")
    if attribute_name == "id":
        if value not in id_mapping:
            raise InvalidCards("SVG contains an invalid ID")
        return id_mapping[value]
    if attribute_name == "href":
        if element_name not in HREF_ELEMENTS:
            raise InvalidCards("SVG href is not allowed on this element")
        return _canonical_href(value, id_mapping)
    if attribute_name in URL_REFERENCE_ATTRIBUTES and "url(" in value.lower():
        return _canonical_svg_reference(value, id_mapping)
    if attribute_name in {"clip-path", "mask"} and value != "none":
        raise InvalidCards("SVG clip-path and mask must use an internal reference")
    if attribute_name in COLOR_ATTRIBUTES and not SAFE_COLOR_PATTERN.fullmatch(value):
        raise InvalidCards("SVG contains an unsupported color value")
    if "url(" in value.lower() or "javascript:" in value.lower() or "data:" in value.lower():
        raise InvalidCards("SVG contains an external or unsafe reference")
    return value


def sanitize_svg(value: Any, card_id: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidCards("iconSvg must be a non-empty SVG string")
    try:
        raw_size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        raise InvalidCards("iconSvg contains invalid Unicode") from None
    if raw_size > MAX_RAW_SVG_BYTES:
        raise InvalidCards("iconSvg is too large")
    if FORBIDDEN_XML_DECLARATION_PATTERN.search(value):
        raise InvalidCards("SVG DTD and entities are not allowed")
    xml_text = LEADING_XML_DECLARATION_PATTERN.sub("", value, count=1)
    if "<?" in xml_text:
        raise InvalidCards("SVG processing instructions are not allowed")
    try:
        source_root = ET.fromstring(xml_text)
    except (ET.ParseError, RecursionError):
        raise InvalidCards("iconSvg must contain well-formed SVG XML") from None

    root_namespace, root_name = _split_svg_name(source_root.tag)
    if root_name != "svg" or root_namespace not in {"", SVG_NAMESPACE}:
        raise InvalidCards("iconSvg root must be an SVG element")

    elements: list[tuple[ET.Element, str]] = []
    identifiers: list[str] = []
    seen_identifiers: set[str] = set()
    drawable_count = 0
    title_count = 0

    def inspect(element: ET.Element, depth: int) -> None:
        nonlocal drawable_count, title_count
        if depth > MAX_SVG_DEPTH:
            raise InvalidCards("SVG nesting is too deep")
        namespace, name = _split_svg_name(element.tag)
        if namespace not in {"", SVG_NAMESPACE} or name not in ALLOWED_SVG_ELEMENTS:
            raise InvalidCards(f"SVG element {name!r} is not allowed")
        elements.append((element, name))
        if len(elements) > MAX_SVG_ELEMENTS:
            raise InvalidCards("SVG contains too many elements")
        if name == "title":
            title_count += 1
            title_text = element.text
            if (
                title_count > 1
                or depth != 2
                or element.attrib
                or list(element)
                or not isinstance(title_text, str)
                or not title_text.strip()
                or _contains_forbidden_xml_character(title_text)
                or len(title_text.encode("utf-8")) > MAX_SVG_TITLE_BYTES
            ):
                raise InvalidCards("SVG title must be one direct, plain-text child of the root SVG")
        if name in DRAWABLE_SVG_ELEMENTS:
            drawable_count += 1
        if name != "title" and element.text and element.text.strip():
            raise InvalidCards("SVG text content is not allowed")
        if element.tail and element.tail.strip():
            raise InvalidCards("SVG text content is not allowed")
        for raw_attribute, attribute_value in element.attrib.items():
            namespace, attribute = _normalize_svg_attribute_name(raw_attribute)
            if namespace or attribute.lower().startswith("on") or attribute == "class":
                raise InvalidCards(f"SVG attribute {attribute!r} is not allowed")
            if attribute == "style":
                continue
            if attribute not in GLOBAL_SVG_ATTRIBUTES | ELEMENT_SVG_ATTRIBUTES[name]:
                raise InvalidCards(f"SVG attribute {attribute!r} is not allowed")
            if attribute == "id":
                if not SVG_ID_PATTERN.fullmatch(attribute_value) or attribute_value in seen_identifiers:
                    raise InvalidCards("SVG IDs must be valid and unique")
                seen_identifiers.add(attribute_value)
                identifiers.append(attribute_value)
        for child in list(element):
            inspect(child, depth + 1)

    inspect(source_root, 1)
    if drawable_count == 0:
        raise InvalidCards("SVG must contain at least one drawable element")

    prefix = f"card-{hashlib.sha256(card_id.encode('utf-8')).hexdigest()[:12]}-"
    id_mapping = {identifier: f"{prefix}id-{index}" for index, identifier in enumerate(identifiers, 1)}

    def clone(element: ET.Element, name: str, *, is_root: bool = False) -> ET.Element:
        attributes: dict[str, str] = {}
        if is_root:
            attributes["xmlns"] = SVG_NAMESPACE
        normalized_attributes: list[tuple[str, str]] = []
        for raw_attribute, attribute_value in element.attrib.items():
            namespace, attribute = _normalize_svg_attribute_name(raw_attribute)
            if namespace:
                raise InvalidCards(f"SVG attribute {attribute!r} is not allowed")
            if attribute == "style":
                continue
            normalized_attributes.append((attribute, attribute_value))
        for attribute, attribute_value in sorted(normalized_attributes):
            attributes[attribute] = _validate_svg_attribute_value(
                name,
                attribute,
                attribute_value,
                id_mapping,
            )
        target = ET.Element(name, attributes)
        for child in list(element):
            _, child_name = _split_svg_name(child.tag)
            if child_name == "title":
                continue
            target.append(clone(child, child_name))
        return target

    canonical_root = clone(source_root, root_name, is_root=True)
    canonical = ET.tostring(canonical_root, encoding="unicode", short_empty_elements=True)
    if len(canonical.encode("utf-8")) > MAX_CANONICAL_SVG_BYTES:
        raise InvalidCards("sanitized iconSvg is too large")
    return canonical


def _validate_card(card: Any, channel: str, domain_suffix: str) -> dict[str, Any]:
    expected_fields = SERVICE_CARD_FIELDS if channel == "services" else QUICK_LINK_CARD_FIELDS
    candidate_id = card.get("id") if isinstance(card, dict) else None
    safe_card_id = (
        candidate_id
        if isinstance(candidate_id, str) and CARD_ID_PATTERN.fullmatch(candidate_id)
        else None
    )
    if not isinstance(card, dict) or set(card) != expected_fields:
        raise InvalidCards(
            f"{channel} card fields are missing or unknown",
            card_id=safe_card_id,
        )
    card_id = card["id"]
    if safe_card_id is None:
        raise InvalidCards("card id must be 1-64 ASCII letters, digits, dots, underscores, colons or hyphens")
    try:
        name = _validate_text(card["name"], "name", MAX_CARD_NAME_LENGTH)
        url = _validate_navigation_url(card["url"], "url")
        result: dict[str, Any] = {
            "id": card_id,
            "iconSvg": sanitize_svg(card["iconSvg"], card_id),
            "name": name,
        }
        if channel == "services":
            description = _validate_text(card["description"], "description", MAX_CARD_DESCRIPTION_LENGTH)
            probe_url = card["probeUrl"]
            if probe_url is not None:
                probe_url = _validate_navigation_url(probe_url, "probeUrl")
            effective_probe_url = probe_url or url
            if validate_probe_target(effective_probe_url, domain_suffix) is None:
                raise InvalidCards("effective service probe URL is not allowed by the configured probe policy")
            result.update({"description": description, "url": url, "probeUrl": probe_url})
        else:
            result["url"] = url
        return result
    except InvalidCards as error:
        if error.card_id is None:
            error.card_id = card_id
        raise


def validate_cards(cards: Any, channel: str, domain_suffix: str) -> list[dict[str, Any]]:
    if channel not in CHANNELS:
        raise InvalidCards("channel must be services or quickLinks")
    if not isinstance(cards, list) or len(cards) > MAX_CARDS_PER_CHANNEL:
        raise InvalidCards("cards must be an array containing at most 32 cards")
    validated: list[dict[str, Any]] = []
    identifiers: set[str] = set()
    for card in cards:
        normalized = _validate_card(card, channel, domain_suffix)
        if normalized["id"] in identifiers:
            raise InvalidCards("card IDs must be unique within a channel")
        identifiers.add(normalized["id"])
        validated.append(normalized)
    return validated


def _validate_timestamp(value: Any) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise InvalidCards("updatedAt must be a UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise InvalidCards("updatedAt must be a UTC timestamp") from None
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise InvalidCards("updatedAt must be a UTC timestamp")
    return value


class CardsStore:
    def __init__(self, state_file: Path, domain_suffix: str, clock=utc_timestamp) -> None:
        self.state_file = Path(os.path.abspath(os.fspath(state_file)))
        self.domain_suffix = domain_suffix
        self.clock = clock
        self._lock = threading.Lock()
        self._state: dict[str, Any] | None = None
        self._load_error: Exception | None = None
        try:
            self._state = self._read_state_file()
        except FileNotFoundError:
            pass
        except Exception as error:
            self._load_error = error

    @classmethod
    def for_state_directory(cls, state_dir: Path, domain_suffix: str, clock=utc_timestamp) -> "CardsStore":
        return cls(state_dir / CARDS_STATE_FILE_NAME, domain_suffix, clock)

    def _read_state_file(self) -> dict[str, Any]:
        try:
            file_stat = os.lstat(self.state_file)
        except FileNotFoundError:
            raise
        if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
            raise CardsStateUnavailable("card state path is not a regular file")
        if file_stat.st_size > MAX_CARDS_STATE_FILE_BYTES:
            raise CardsStateUnavailable("card state file is too large")
        try:
            raw = _strict_json_loads(self.state_file.read_text(encoding="utf-8"))
            return self._validate_persisted_state(raw)
        except CardsStateUnavailable:
            raise
        except Exception as error:
            raise CardsStateUnavailable("card state file is damaged or incompatible") from error

    def _validate_channel_state(self, value: Any, channel: str) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) != {"revision", "updatedAt", "cards"}:
            raise InvalidCards(f"stored {channel} channel has missing or unknown fields")
        revision = value["revision"]
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise InvalidCards("stored card channel revision is invalid")
        return {
            "revision": revision,
            "updatedAt": _validate_timestamp(value["updatedAt"]),
            "cards": validate_cards(value["cards"], channel, self.domain_suffix),
        }

    def _validate_persisted_state(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) != {"version", "initialized", "channels"}:
            raise InvalidCards("card state contains missing or unknown fields")
        if value["version"] != CARDS_VERSION or isinstance(value["version"], bool):
            raise InvalidCards("unsupported card state version")
        if value["initialized"] is not True or not isinstance(value["channels"], dict):
            raise InvalidCards("stored card state must be initialized")
        if set(value["channels"]) != set(CHANNELS):
            raise InvalidCards("stored card state channels are invalid")
        return {
            "version": CARDS_VERSION,
            "initialized": True,
            "channels": {
                channel: self._validate_channel_state(value["channels"][channel], channel)
                for channel in CHANNELS
            },
        }

    def _require_available(self) -> None:
        if self._load_error is not None:
            raise CardsStateUnavailable("card state file is damaged or incompatible") from self._load_error

    def _write_state(self, state: dict[str, Any]) -> None:
        try:
            atomic_write_json(self.state_file, state)
        except OSError:
            raise

    def get(self) -> dict[str, Any]:
        with self._lock:
            self._require_available()
            if self._state is None:
                return {"version": CARDS_VERSION, "initialized": False, "channels": None}
            return copy.deepcopy(self._state)

    def initialize(self, request: Any) -> dict[str, Any]:
        with self._lock:
            self._require_available()
            if self._state is not None:
                raise CardsAlreadyInitialized("cards are already initialized")
            try:
                existing = self._read_state_file()
            except FileNotFoundError:
                existing = None
            except Exception as error:
                self._load_error = error
                raise CardsStateUnavailable("card state file is damaged or incompatible") from error
            if existing is not None:
                self._state = existing
                raise CardsAlreadyInitialized("cards are already initialized")
            if (
                not isinstance(request, dict)
                or set(request) != {"version", "services", "quickLinks"}
                or request.get("version") != CARDS_VERSION
                or isinstance(request.get("version"), bool)
            ):
                raise InvalidCards("initialize request must contain version 1, services and quickLinks")
            timestamp = self.clock()
            _validate_timestamp(timestamp)
            state = {
                "version": CARDS_VERSION,
                "initialized": True,
                "channels": {
                    "services": {
                        "revision": 1,
                        "updatedAt": timestamp,
                        "cards": validate_cards(request["services"], "services", self.domain_suffix),
                    },
                    "quickLinks": {
                        "revision": 1,
                        "updatedAt": timestamp,
                        "cards": validate_cards(request["quickLinks"], "quickLinks", self.domain_suffix),
                    },
                },
            }
            self._write_state(state)
            self._state = state
            return copy.deepcopy(state)

    def update(self, request: Any) -> dict[str, Any]:
        with self._lock:
            self._require_available()
            if self._state is None:
                raise CardsNotInitialized("cards are not initialized")
            if (
                not isinstance(request, dict)
                or set(request) != {"version", "channel", "expectedRevision", "cards"}
                or request.get("version") != CARDS_VERSION
                or isinstance(request.get("version"), bool)
                or request.get("channel") not in CHANNELS
            ):
                raise InvalidCards(
                    "update request must contain version 1, channel, expectedRevision and cards"
                )
            expected_revision = request["expectedRevision"]
            if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
                raise InvalidCards("expectedRevision must be a positive integer")
            channel = request["channel"]
            current_revision = self._state["channels"][channel]["revision"]
            if expected_revision != current_revision:
                raise CardsRevisionConflict(current_revision)
            cards = validate_cards(request["cards"], channel, self.domain_suffix)
            timestamp = self.clock()
            _validate_timestamp(timestamp)
            state = copy.deepcopy(self._state)
            state["channels"][channel] = {
                "revision": current_revision + 1,
                "updatedAt": timestamp,
                "cards": cards,
            }
            self._write_state(state)
            self._state = state
            return copy.deepcopy(state)
