from __future__ import annotations

import io
import json
import re
import ssl
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from homelab_state.backgrounds import (
    MAX_BACKGROUND_UPLOAD_BODY,
    BackgroundQuotaExceeded,
    InvalidBackground,
    InvalidImage,
    StateStore,
    UploadTooLarge,
    validate_background_name,
)
from homelab_state.core import SERVICE_NAME, SERVICE_VERSION


BING_DAILY_VERSION = 1
BING_BASE_URL = "https://www.bing.com"
BING_METADATA_PATH = "/HPImageArchive.aspx"
BING_IMAGE_PATH = "/th"
BING_TIMEOUT_SECONDS = 20.0
BING_OPERATION_TIMEOUT_SECONDS = 90.0
BING_READ_CHUNK_SIZE = 64 * 1024
MAX_BING_METADATA_BODY = 64 * 1024
BING_MARKETS = frozenset({"zh-CN", "en-WW", "en-US"})
BING_MARKET_MARKERS = {
    "zh-CN": ("cn", "ZH-CN"),
    "en-WW": ("row", "ROW"),
    "en-US": ("us", "EN-US"),
}
BING_IMAGE_ID_PATTERN = re.compile(
    r"^OHR\.[A-Za-z0-9][A-Za-z0-9._-]{0,179}_(?:ZH-CN|ROW|EN-US)[0-9]+$"
)
BING_IMAGE_RESOURCE_PATTERN = re.compile(
    r"^OHR\.[A-Za-z0-9][A-Za-z0-9._-]{0,179}_(?:ZH-CN|ROW|EN-US)[0-9]+_"
    r"(?:UHD|1920x1080)\.jpg$"
)
BING_BACKGROUND_NAME_PATTERN = re.compile(
    r"^bing_(cn|row|us)_([0-9]{8})_"
    r"(OHR\.[A-Za-z0-9][A-Za-z0-9._-]{0,179}_(?:ZH-CN|ROW|EN-US)[0-9]+)"
    r"(?: \((?:[2-9]|[1-9][0-9]+)\))?\.jpg$",
    re.IGNORECASE,
)


class BingUnavailable(RuntimeError):
    pass


class BingAssetMissing(BingUnavailable):
    pass


class BingMarketMismatch(BingUnavailable):
    pass


class BingDailyBusy(RuntimeError):
    pass


class _RejectBingRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        raise HTTPError(request.full_url, code, "Bing redirects are not allowed", headers, file_pointer)


@dataclass(frozen=True)
class BingImage:
    startdate: str
    image_id: str
    source: str


class BingClient:
    def __init__(self, *, timeout: float = BING_TIMEOUT_SECONDS, opener=None, monotonic=time.monotonic) -> None:
        self.timeout = timeout
        self.monotonic = monotonic
        self.opener = opener or build_opener(
            _RejectBingRedirects(),
            HTTPSHandler(context=ssl.create_default_context()),
        )

    @staticmethod
    def _metadata_url(market: str) -> str:
        if market not in BING_MARKETS:
            raise BingUnavailable("unsupported Bing market")
        return f"{BING_BASE_URL}{BING_METADATA_PATH}?{urlencode({
            'format': 'js',
            'idx': '0',
            'n': '1',
            'uhd': '1',
            'mkt': market,
        })}"

    @staticmethod
    def _image_url(image_id: str, variant: str) -> str:
        if not BING_IMAGE_ID_PATTERN.fullmatch(image_id) or variant not in {"UHD", "1920x1080"}:
            raise BingUnavailable("invalid Bing image identifier")
        return f"{BING_BASE_URL}{BING_IMAGE_PATH}?{urlencode({'id': f'{image_id}_{variant}.jpg'})}"

    @staticmethod
    def _validate_url(url: str) -> None:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "www.bing.com"
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 443}
            or parsed.fragment
        ):
            raise BingUnavailable("invalid Bing URL")
        query = parse_qsl(parsed.query, keep_blank_values=True)
        if parsed.path == BING_METADATA_PATH:
            if (
                len(query) != 5
                or dict(query).keys() != {"format", "idx", "n", "uhd", "mkt"}
                or dict(query).get("format") != "js"
                or dict(query).get("idx") != "0"
                or dict(query).get("n") != "1"
                or dict(query).get("uhd") != "1"
                or dict(query).get("mkt") not in BING_MARKETS
            ):
                raise BingUnavailable("invalid Bing metadata URL")
            return
        if parsed.path == BING_IMAGE_PATH:
            if (
                len(query) != 1
                or query[0][0] != "id"
                or not BING_IMAGE_RESOURCE_PATTERN.fullmatch(query[0][1])
            ):
                raise BingUnavailable("invalid Bing image URL")
            return
        raise BingUnavailable("invalid Bing URL path")

    def _read(
        self,
        url: str,
        maximum_body: int,
        accepted_types: frozenset[str],
        *,
        missing_is_distinct: bool = False,
        deadline: float | None = None,
    ) -> bytes:
        self._validate_url(url)
        request = Request(
            url,
            headers={
                "Accept": ", ".join(sorted(accepted_types)),
                "User-Agent": f"{SERVICE_NAME}/{SERVICE_VERSION}",
            },
        )
        try:
            request_timeout = self._remaining_timeout(deadline)
            with self.opener.open(request, timeout=request_timeout) as response:
                self._validate_url(response.geturl())
                content_type = response.headers.get_content_type().lower()
                if content_type not in accepted_types:
                    raise BingUnavailable("Bing returned an unexpected content type")
                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        declared_length = int(content_length)
                    except ValueError:
                        raise BingUnavailable("Bing returned an invalid response length") from None
                    if declared_length < 0 or declared_length > maximum_body:
                        raise BingUnavailable("Bing response is too large")
                payload = bytearray()
                while len(payload) < maximum_body + 1:
                    read_timeout = self._remaining_timeout(deadline)
                    self._set_response_timeout(response, read_timeout)
                    chunk = response.read(min(BING_READ_CHUNK_SIZE, maximum_body + 1 - len(payload)))
                    if not chunk:
                        break
                    payload.extend(chunk)
        except HTTPError as error:
            if missing_is_distinct and error.code == HTTPStatus.NOT_FOUND:
                raise BingAssetMissing("Bing image variant is unavailable") from None
            raise BingUnavailable(f"Bing request failed with HTTP {error.code}") from None
        except BingUnavailable:
            raise
        except (OSError, URLError, ssl.SSLError, TimeoutError):
            raise BingUnavailable("unable to reach Bing") from None
        if len(payload) > maximum_body:
            raise BingUnavailable("Bing response is too large")
        return bytes(payload)

    def _remaining_timeout(self, deadline: float | None) -> float:
        if deadline is None:
            return self.timeout
        remaining = deadline - self.monotonic()
        if remaining <= 0:
            raise BingUnavailable("Bing daily operation timed out")
        return min(self.timeout, remaining)

    @staticmethod
    def _set_response_timeout(response: Any, timeout: float) -> None:
        socket_object = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
        if socket_object is not None and hasattr(socket_object, "settimeout"):
            socket_object.settimeout(timeout)

    def daily_image(self, market: str, *, deadline: float | None = None) -> BingImage:
        payload = self._read(
            self._metadata_url(market),
            MAX_BING_METADATA_BODY,
            frozenset({"application/json", "text/json"}),
            deadline=deadline,
        )
        try:
            value = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise BingUnavailable("Bing returned invalid JSON") from None
        if not isinstance(value, dict) or not isinstance(value.get("images"), list) or len(value["images"]) != 1:
            raise BingUnavailable("Bing returned an invalid image list")
        image = value["images"][0]
        if not isinstance(image, dict):
            raise BingUnavailable("Bing returned invalid image metadata")
        startdate = image.get("startdate")
        urlbase = image.get("urlbase")
        if not isinstance(startdate, str) or not re.fullmatch(r"[0-9]{8}", startdate):
            raise BingUnavailable("Bing returned an invalid image date")
        try:
            datetime.strptime(startdate, "%Y%m%d")
        except ValueError:
            raise BingUnavailable("Bing returned an invalid image date") from None
        if not isinstance(urlbase, str):
            raise BingUnavailable("Bing returned an invalid image URL")
        parsed_base = urlsplit(urlbase)
        base_query = parse_qsl(parsed_base.query, keep_blank_values=True)
        if (
            parsed_base.scheme
            or parsed_base.netloc
            or parsed_base.fragment
            or parsed_base.path != BING_IMAGE_PATH
            or len(base_query) != 1
            or base_query[0][0] != "id"
        ):
            raise BingUnavailable("Bing returned an invalid image URL")
        image_id = base_query[0][1]
        if not BING_IMAGE_ID_PATTERN.fullmatch(image_id):
            raise BingUnavailable("Bing returned an invalid image identifier")
        source, marker = BING_MARKET_MARKERS[market]
        if not re.search(rf"_{re.escape(marker)}[0-9]+$", image_id):
            raise BingMarketMismatch(f"Bing did not return the requested {source} market")
        return BingImage(startdate=startdate, image_id=image_id, source=source)

    def download_image(self, image: BingImage, *, deadline: float | None = None) -> tuple[bytes, str]:
        try:
            return (
                self._read(
                    self._image_url(image.image_id, "UHD"),
                    MAX_BACKGROUND_UPLOAD_BODY,
                    frozenset({"image/jpeg"}),
                    missing_is_distinct=True,
                    deadline=deadline,
                ),
                "UHD",
            )
        except BingAssetMissing:
            return (
                self._read(
                    self._image_url(image.image_id, "1920x1080"),
                    MAX_BACKGROUND_UPLOAD_BODY,
                    frozenset({"image/jpeg"}),
                    missing_is_distinct=True,
                    deadline=deadline,
                ),
                "1920x1080",
            )


class BingDailyManager:
    def __init__(
        self,
        store: StateStore,
        *,
        client: BingClient | None = None,
        operation_timeout: float = BING_OPERATION_TIMEOUT_SECONDS,
        monotonic=time.monotonic,
    ) -> None:
        self.store = store
        self.client = client or BingClient()
        self.operation_timeout = operation_timeout
        self.monotonic = monotonic
        self._operation_lock = threading.Lock()

    @staticmethod
    def _background_name(image: BingImage) -> str:
        return validate_background_name(
            f"bing_{image.source}_{image.startdate}_{image.image_id}.jpg"
        )

    def _find_existing_exact(self, requested_name: str) -> str | None:
        requested = requested_name.casefold()
        requested_stem = requested[:-4]
        collision = re.compile(
            rf"^{re.escape(requested_stem)} \((?:[2-9]|[1-9][0-9]+)\)\.jpg$"
        )
        for name in self.store.background_names():
            folded = name.casefold()
            if folded == requested or collision.fullmatch(folded):
                return name
        return None

    def _find_existing_international_date(
        self,
        startdate: str,
        *,
        sources: frozenset[str] = frozenset({"row", "us"}),
    ) -> tuple[str, str] | None:
        for name in self.store.background_names():
            parsed = self._parse_background_name(name)
            if parsed is None or parsed[1] != startdate:
                continue
            source = parsed[0]
            if source in sources:
                return name, source
        return None

    @staticmethod
    def _parse_background_name(name: str) -> tuple[str, str, str] | None:
        match = BING_BACKGROUND_NAME_PATTERN.fullmatch(name)
        if not match:
            return None
        source = match.group(1).lower()
        image_id = match.group(3)
        expected_marker = {"cn": "_ZH-CN", "row": "_ROW", "us": "_EN-US"}[source]
        if not re.search(rf"{re.escape(expected_marker)}[0-9]+$", image_id):
            return None
        return source, match.group(2), image_id

    @staticmethod
    def _failed(slot: str, error: str, *, market: str | None = None, fallback: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "slot": slot,
            "status": "failed",
            "error": error,
        }
        if market is not None:
            result["market"] = market
        if slot == "international":
            result["fallback"] = fallback
        return result

    def _save(
        self,
        slot: str,
        image: BingImage,
        *,
        fallback: bool = False,
        deadline: float,
    ) -> dict[str, Any]:
        requested_name = self._background_name(image)
        existing = self._find_existing_exact(requested_name)
        if existing is not None:
            result = {
                "slot": slot,
                "market": {"cn": "zh-CN", "row": "ROW", "us": "en-US"}[image.source],
                "status": "existing",
                "startdate": image.startdate,
                "imageId": image.image_id,
                "background": existing,
            }
            if slot == "international":
                result["fallback"] = fallback
            return result
        payload, variant = self.client.download_image(image, deadline=deadline)
        background = self.store.upload_background(requested_name, io.BytesIO(payload), len(payload))
        result = {
            "slot": slot,
            "market": {"cn": "zh-CN", "row": "ROW", "us": "en-US"}[image.source],
            "status": "created",
            "startdate": image.startdate,
            "imageId": image.image_id,
            "variant": variant,
            "background": background["background"],
        }
        if slot == "international":
            result["fallback"] = fallback
        return result

    def _china(self, deadline: float) -> dict[str, Any]:
        try:
            return self._save(
                "cn",
                self.client.daily_image("zh-CN", deadline=deadline),
                deadline=deadline,
            )
        except BackgroundQuotaExceeded:
            return self._failed("cn", "background_file_quota_exceeded", market="zh-CN")
        except (InvalidBackground, InvalidImage, UploadTooLarge):
            return self._failed("cn", "bing_invalid_image", market="zh-CN")
        except BingUnavailable:
            return self._failed("cn", "bing_unavailable", market="zh-CN")

    def _international(self, deadline: float) -> dict[str, Any]:
        try:
            row_image = self.client.daily_image("en-WW", deadline=deadline)
            # A US fallback already stored for this Bing date satisfies the
            # international slot. A ROW file only counts when its exact ID is
            # found by _save(), so a changed ROW image on the same date is not
            # accidentally skipped.
            existing = self._find_existing_international_date(
                row_image.startdate,
                sources=frozenset({"us"}),
            )
            if existing is not None:
                name, source = existing
                return {
                    "slot": "international",
                    "market": "ROW" if source == "row" else "en-US",
                    "fallback": source == "us",
                    "status": "existing",
                    "startdate": row_image.startdate,
                    "imageId": self._parse_background_name(name)[2],
                    "background": name,
                }
            return self._save("international", row_image, deadline=deadline)
        except BackgroundQuotaExceeded:
            return self._failed(
                "international",
                "background_file_quota_exceeded",
                market="ROW",
            )
        except (BingUnavailable, InvalidBackground, InvalidImage, UploadTooLarge):
            pass

        try:
            us_image = self.client.daily_image("en-US", deadline=deadline)
            existing = self._find_existing_international_date(us_image.startdate)
            if existing is not None:
                name, source = existing
                return {
                    "slot": "international",
                    "market": "ROW" if source == "row" else "en-US",
                    "fallback": source == "us",
                    "status": "existing",
                    "startdate": us_image.startdate,
                    "imageId": self._parse_background_name(name)[2],
                    "background": name,
                }
            return self._save("international", us_image, fallback=True, deadline=deadline)
        except BackgroundQuotaExceeded:
            return self._failed(
                "international",
                "background_file_quota_exceeded",
                market="en-US",
                fallback=True,
            )
        except (InvalidBackground, InvalidImage, UploadTooLarge):
            return self._failed(
                "international",
                "bing_invalid_image",
                market="en-US",
                fallback=True,
            )
        except BingUnavailable:
            return self._failed(
                "international",
                "bing_unavailable",
                market="en-US",
                fallback=True,
            )

    def fetch_daily(self) -> dict[str, Any]:
        if not self._operation_lock.acquire(blocking=False):
            raise BingDailyBusy("another Bing daily operation is in progress")
        try:
            deadline = self.monotonic() + self.operation_timeout
            return {
                "version": BING_DAILY_VERSION,
                "results": [self._china(deadline), self._international(deadline)],
            }
        finally:
            self._operation_lock.release()
