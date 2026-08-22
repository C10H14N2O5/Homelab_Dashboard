from __future__ import annotations

import json
import os
import threading
import unittest
from unittest import mock
from urllib.error import URLError
from urllib.request import ProxyHandler

from homelab_state import backgrounds, bing
from tests.helpers import FIXED_TIME, TemporaryProject, image_bytes


PROXY_URL = "http://127.0.0.1:7890"


class BingClientTests(unittest.TestCase):
    def test_metadata_parses_date_id_and_required_market(self) -> None:
        client = bing.BingClient(
            PROXY_URL,
            direct_opener=mock.Mock(),
            international_opener=mock.Mock(),
        )
        payload = json.dumps({
            "images": [{
                "startdate": "20260820",
                "urlbase": "/th?id=OHR.JulierPass_ZH-CN3064797820",
            }],
        }).encode()
        with mock.patch.object(client, "_read", return_value=payload):
            image = client.daily_image("zh-CN")
        self.assertEqual(
            image,
            bing.BingImage(
                startdate="20260820",
                image_id="OHR.JulierPass_ZH-CN3064797820",
                source="cn",
            ),
        )

        mismatch = json.dumps({
            "images": [{
                "startdate": "20260820",
                "urlbase": "/th?id=OHR.LynnCanalOrca_EN-US0537229184",
            }],
        }).encode()
        with mock.patch.object(client, "_read", return_value=mismatch):
            with self.assertRaises(bing.BingMarketMismatch):
                client.daily_image("en-WW")

    def test_metadata_rejects_invalid_dates_urls_and_identifiers(self) -> None:
        client = bing.BingClient(
            PROXY_URL,
            direct_opener=mock.Mock(),
            international_opener=mock.Mock(),
        )
        cases = (
            {"images": []},
            {"images": [{"startdate": "20260230", "urlbase": "/th?id=OHR.Test_ROW1"}]},
            {"images": [{"startdate": "20260820", "urlbase": "https://evil.test/th?id=OHR.Test_ROW1"}]},
            {"images": [{"startdate": "20260820", "urlbase": "/th?id=../unsafe_ROW1"}]},
            {"images": [{"startdate": "20260820", "urlbase": "/th?id=OHR.Test_ROW1&extra=1"}]},
        )
        for value in cases:
            with self.subTest(value=value), mock.patch.object(
                client,
                "_read",
                return_value=json.dumps(value).encode(),
            ):
                with self.assertRaises(bing.BingUnavailable):
                    client.daily_image("en-WW")

    def test_fixed_https_urls_response_limits_and_redirect_rejection(self) -> None:
        direct_opener = mock.Mock()
        international_opener = mock.Mock()
        client = bing.BingClient(
            PROXY_URL,
            direct_opener=direct_opener,
            international_opener=international_opener,
        )
        invalid_urls = (
            "http://www.bing.com/HPImageArchive.aspx?format=js&idx=0&n=1&uhd=1&mkt=zh-CN",
            "https://evil.example/HPImageArchive.aspx?format=js&idx=0&n=1&uhd=1&mkt=zh-CN",
            "https://www.bing.com.evil.example/th?id=OHR.Test_ROW1_UHD.jpg",
            "https://www.bing.com:444/th?id=OHR.Test_ROW1_UHD.jpg",
            "https://www.bing.com/th?id=OHR.Test_ROW1_UHD.jpg&rf=LaDigue_UHD.jpg",
        )
        for url in invalid_urls:
            with self.subTest(url=url), self.assertRaises(bing.BingUnavailable):
                client._validate_url(url)

        response = mock.MagicMock()
        metadata_url = client._metadata_url("zh-CN")
        response.geturl.return_value = metadata_url
        response.headers.get_content_type.return_value = "application/json"
        response.headers.get.return_value = str(bing.MAX_BING_METADATA_BODY + 1)
        response.__enter__.return_value = response
        direct_opener.open.return_value = response
        with self.assertRaisesRegex(bing.BingUnavailable, "too large"):
            client._read(
                metadata_url,
                bing.MAX_BING_METADATA_BODY,
                frozenset({"application/json"}),
                opener=direct_opener,
                proxied=False,
            )

        response.headers.get.return_value = None
        response.geturl.return_value = "https://evil.example/th?id=OHR.Test_ROW1_UHD.jpg"
        with self.assertRaisesRegex(bing.BingUnavailable, "invalid Bing URL"):
            client._read(
                metadata_url,
                bing.MAX_BING_METADATA_BODY,
                frozenset({"application/json"}),
                opener=direct_opener,
                proxied=False,
            )

        response.geturl.return_value = metadata_url
        response.read.side_effect = [b"x" * (bing.MAX_BING_METADATA_BODY + 1)]
        with self.assertRaisesRegex(bing.BingUnavailable, "too large"):
            client._read(
                metadata_url,
                bing.MAX_BING_METADATA_BODY,
                frozenset({"application/json"}),
                opener=direct_opener,
                proxied=False,
            )
        real_client = bing.BingClient(PROXY_URL)
        for opener in (real_client.direct_opener, real_client.international_opener):
            self.assertTrue(
                any(
                    isinstance(handler, bing._RejectBingRedirects)
                    for handler in opener.handlers
                )
            )

    def test_direct_and_proxy_openers_ignore_environment_proxy_configuration(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "HTTP_PROXY": "http://192.0.2.1:3128",
                "HTTPS_PROXY": "http://192.0.2.1:3128",
            },
        ):
            client = bing.BingClient(PROXY_URL)

        direct_proxy_handlers = [
            handler
            for handler in client.direct_opener.handlers
            if isinstance(handler, ProxyHandler)
        ]
        international_proxy_handlers = [
            handler
            for handler in client.international_opener.handlers
            if isinstance(handler, ProxyHandler)
        ]
        self.assertEqual(direct_proxy_handlers, [])
        self.assertEqual(len(international_proxy_handlers), 1)
        self.assertEqual(
            international_proxy_handlers[0].proxies,
            {"https": PROXY_URL},
        )

    def test_market_metadata_and_image_downloads_use_the_expected_opener(self) -> None:
        direct_opener = mock.Mock()
        international_opener = mock.Mock()
        client = bing.BingClient(
            PROXY_URL,
            direct_opener=direct_opener,
            international_opener=international_opener,
        )
        china_payload = json.dumps({
            "images": [{
                "startdate": "20260820",
                "urlbase": "/th?id=OHR.China_ZH-CN100",
            }],
        }).encode()
        row_payload = json.dumps({
            "images": [{
                "startdate": "20260820",
                "urlbase": "/th?id=OHR.World_ROW200",
            }],
        }).encode()
        with mock.patch.object(
            client,
            "_read",
            side_effect=[china_payload, row_payload],
        ) as read:
            china = client.daily_image("zh-CN")
            row = client.daily_image("en-WW")

        self.assertIs(read.call_args_list[0].kwargs["opener"], direct_opener)
        self.assertFalse(read.call_args_list[0].kwargs["proxied"])
        self.assertIs(read.call_args_list[1].kwargs["opener"], international_opener)
        self.assertTrue(read.call_args_list[1].kwargs["proxied"])

        with mock.patch.object(client, "_read", return_value=b"jpeg") as read:
            client.download_image(china)
            client.download_image(row)
        self.assertIs(read.call_args_list[0].kwargs["opener"], direct_opener)
        self.assertFalse(read.call_args_list[0].kwargs["proxied"])
        self.assertIs(read.call_args_list[1].kwargs["opener"], international_opener)
        self.assertTrue(read.call_args_list[1].kwargs["proxied"])

    def test_proxied_transport_failures_are_distinct_from_direct_failures(self) -> None:
        transport = mock.Mock()
        transport.open.side_effect = URLError("offline")
        client = bing.BingClient(
            PROXY_URL,
            direct_opener=transport,
            international_opener=transport,
        )
        metadata_url = client._metadata_url("en-WW")
        with self.assertRaises(bing.BingProxyUnavailable):
            client._read(
                metadata_url,
                bing.MAX_BING_METADATA_BODY,
                frozenset({"application/json"}),
                opener=transport,
                proxied=True,
            )
        with self.assertRaises(bing.BingUnavailable) as raised:
            client._read(
                metadata_url,
                bing.MAX_BING_METADATA_BODY,
                frozenset({"application/json"}),
                opener=transport,
                proxied=False,
            )
        self.assertNotIsInstance(raised.exception, bing.BingProxyUnavailable)

    def test_operation_deadline_caps_individual_request_timeout(self) -> None:
        client = bing.BingClient(
            PROXY_URL,
            timeout=20,
            direct_opener=mock.Mock(),
            international_opener=mock.Mock(),
            monotonic=lambda: 45,
        )
        self.assertEqual(client._remaining_timeout(55), 10)
        with self.assertRaisesRegex(bing.BingUnavailable, "timed out"):
            client._remaining_timeout(45)

    def test_image_download_uses_no_rf_and_falls_back_only_after_uhd_404(self) -> None:
        client = bing.BingClient(
            PROXY_URL,
            direct_opener=mock.Mock(),
            international_opener=mock.Mock(),
        )
        image = bing.BingImage("20260820", "OHR.Test_ROW123", "row")
        payload = image_bytes("JPEG")
        with mock.patch.object(
            client,
            "_read",
            side_effect=[bing.BingAssetMissing("missing"), payload],
        ) as read:
            downloaded, variant = client.download_image(image)
        self.assertEqual(downloaded, payload)
        self.assertEqual(variant, "1920x1080")
        self.assertIn("_UHD.jpg", read.call_args_list[0].args[0])
        self.assertIn("_1920x1080.jpg", read.call_args_list[1].args[0])
        self.assertNotIn("rf=", read.call_args_list[0].args[0])
        self.assertNotIn("rf=", read.call_args_list[1].args[0])
        for call in read.call_args_list:
            self.assertIs(call.kwargs["opener"], client.international_opener)
            self.assertTrue(call.kwargs["proxied"])


class FakeBingClient:
    def __init__(self) -> None:
        self.images = {
            "zh-CN": bing.BingImage("20260820", "OHR.China_ZH-CN100", "cn"),
            "en-WW": bing.BingImage("20260820", "OHR.World_ROW200", "row"),
            "en-US": bing.BingImage("20260820", "OHR.America_EN-US300", "us"),
        }
        self.failures: dict[str, Exception] = {}
        self.download_failures: dict[str, Exception] = {}
        self.daily_calls: list[str] = []
        self.download_calls: list[str] = []
        self.deadlines: list[float | None] = []
        self.download_started: threading.Event | None = None
        self.download_continue: threading.Event | None = None

    def daily_image(self, market: str, *, deadline: float | None = None) -> bing.BingImage:
        self.daily_calls.append(market)
        self.deadlines.append(deadline)
        if market in self.failures:
            raise self.failures[market]
        return self.images[market]

    def download_image(
        self,
        image: bing.BingImage,
        *,
        deadline: float | None = None,
    ) -> tuple[bytes, str]:
        self.download_calls.append(image.source)
        self.deadlines.append(deadline)
        if self.download_started is not None:
            self.download_started.set()
        if self.download_continue is not None:
            self.download_continue.wait(timeout=2)
        if image.source in self.download_failures:
            raise self.download_failures[image.source]
        return image_bytes("JPEG"), "UHD"


class BingDailyManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = TemporaryProject()
        self.config = self.project.write_config()
        self.store = backgrounds.StateStore(
            self.config.background_dir,
            self.config.state_file,
            lambda: FIXED_TIME,
        )
        self.client = FakeBingClient()
        self.manager = bing.BingDailyManager(self.store, client=self.client)

    def tearDown(self) -> None:
        self.project.close()

    def test_creates_two_market_files_and_repeated_fetch_is_idempotent(self) -> None:
        first = self.manager.fetch_daily()
        self.assertEqual(first["version"], 1)
        self.assertEqual([item["status"] for item in first["results"]], ["created", "created"])
        names = [item["background"] for item in first["results"]]
        self.assertEqual(
            names,
            [
                "bing_cn_20260820_OHR.China_ZH-CN100.jpg",
                "bing_row_20260820_OHR.World_ROW200.jpg",
            ],
        )
        self.assertTrue(all((self.project.backgrounds / name).is_file() for name in names))

        second = self.manager.fetch_daily()
        self.assertEqual([item["status"] for item in second["results"]], ["existing", "existing"])
        self.assertEqual([item["background"] for item in second["results"]], names)
        self.assertEqual(self.client.download_calls, ["cn", "row"])

    def test_one_operation_deadline_is_propagated_to_every_request(self) -> None:
        manager = bing.BingDailyManager(
            self.store,
            client=self.client,
            operation_timeout=90,
            monotonic=lambda: 10,
        )
        manager.fetch_daily()
        self.assertEqual(self.client.deadlines, [100, 100, 100, 100])

    def test_parser_rejects_filename_prefix_and_market_marker_mismatches(self) -> None:
        self.assertIsNone(
            self.manager._parse_background_name(
                "bing_cn_20260820_OHR.World_ROW200.jpg"
            )
        )
        self.assertIsNone(
            self.manager._parse_background_name(
                "bing_row_20260820_OHR.China_ZH-CN100.jpg"
            )
        )

    def test_changed_row_id_on_same_date_is_not_treated_as_existing(self) -> None:
        first = self.manager.fetch_daily()
        old_name = first["results"][1]["background"]
        self.client.images["en-WW"] = bing.BingImage(
            "20260820",
            "OHR.WorldChanged_ROW201",
            "row",
        )
        second = self.manager.fetch_daily()
        self.assertEqual(second["results"][1]["status"], "created")
        self.assertNotEqual(second["results"][1]["background"], old_name)

    def test_row_failure_falls_back_to_us_and_us_satisfies_the_date(self) -> None:
        self.client.failures["en-WW"] = bing.BingMarketMismatch("wrong market")
        first = self.manager.fetch_daily()
        international = first["results"][1]
        self.assertEqual(international["status"], "created")
        self.assertEqual(international["market"], "en-US")
        self.assertTrue(international["fallback"])
        self.assertTrue(international["background"].startswith("bing_us_20260820_"))

        del self.client.failures["en-WW"]
        second = self.manager.fetch_daily()
        international = second["results"][1]
        self.assertEqual(international["status"], "existing")
        self.assertEqual(international["market"], "en-US")
        self.assertTrue(international["fallback"])
        self.assertFalse(any(path.name.startswith("bing_row_") for path in self.project.backgrounds.iterdir()))

    def test_proxy_metadata_failure_stops_before_us_without_affecting_china(self) -> None:
        self.client.failures["en-WW"] = bing.BingProxyUnavailable("proxy offline")
        result = self.manager.fetch_daily()

        self.assertEqual(result["results"][0]["status"], "created")
        self.assertEqual(
            result["results"][1],
            {
                "slot": "international",
                "status": "failed",
                "error": "bing_unavailable",
                "market": "ROW",
                "fallback": False,
            },
        )
        self.assertEqual(self.client.daily_calls, ["zh-CN", "en-WW"])

    def test_proxy_image_failure_stops_before_us_without_direct_retry(self) -> None:
        self.client.download_failures["row"] = bing.BingProxyUnavailable(
            "proxy offline"
        )
        result = self.manager.fetch_daily()

        self.assertEqual(result["results"][0]["status"], "created")
        self.assertEqual(result["results"][1]["status"], "failed")
        self.assertEqual(result["results"][1]["error"], "bing_unavailable")
        self.assertEqual(result["results"][1]["market"], "ROW")
        self.assertEqual(self.client.daily_calls, ["zh-CN", "en-WW"])
        self.assertEqual(self.client.download_calls, ["cn", "row"])

    def test_invalid_row_image_falls_back_and_full_upstream_failure_is_partial(self) -> None:
        self.client.download_failures["row"] = backgrounds.InvalidImage("bad image")
        result = self.manager.fetch_daily()
        self.assertEqual(result["results"][0]["status"], "created")
        self.assertEqual(result["results"][1]["status"], "created")
        self.assertEqual(result["results"][1]["market"], "en-US")

        for name in list(self.store.background_names()):
            if name.startswith("bing_"):
                (self.project.backgrounds / name).unlink()
        self.client.failures["en-WW"] = bing.BingUnavailable("offline")
        self.client.failures["en-US"] = bing.BingUnavailable("offline")
        result = self.manager.fetch_daily()
        self.assertEqual(result["results"][0]["status"], "created")
        self.assertEqual(result["results"][1]["status"], "failed")
        self.assertEqual(result["results"][1]["error"], "bing_unavailable")

    def test_same_image_bytes_are_stored_separately_and_quota_is_per_item(self) -> None:
        result = self.manager.fetch_daily()
        china, international = result["results"]
        self.assertNotEqual(china["background"], international["background"])
        self.assertEqual(
            (self.project.backgrounds / china["background"]).read_bytes(),
            (self.project.backgrounds / international["background"]).read_bytes(),
        )

        other_project = TemporaryProject()
        try:
            other_config = other_project.write_config()
            other_store = backgrounds.StateStore(other_config.background_dir, other_config.state_file, lambda: FIXED_TIME)
            manager = bing.BingDailyManager(other_store, client=FakeBingClient())
            with mock.patch.object(
                other_store,
                "upload_background",
                side_effect=backgrounds.BackgroundQuotaExceeded("full"),
            ):
                failed = manager.fetch_daily()
            self.assertEqual(
                [item["error"] for item in failed["results"]],
                ["background_file_quota_exceeded", "background_file_quota_exceeded"],
            )
        finally:
            other_project.close()

    def test_quota_failure_preserves_the_other_market_success(self) -> None:
        original_upload = self.store.upload_background
        initial_names = set(self.store.background_names())
        calls = 0

        def upload_once(name, stream, content_length):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise backgrounds.BackgroundQuotaExceeded("full")
            return original_upload(name, stream, content_length)

        with mock.patch.object(self.store, "upload_background", side_effect=upload_once):
            result = self.manager.fetch_daily()
        self.assertEqual(result["results"][0]["status"], "created")
        self.assertEqual(result["results"][1]["status"], "failed")
        self.assertEqual(
            result["results"][1]["error"],
            "background_file_quota_exceeded",
        )
        created_names = set(self.store.background_names()) - initial_names
        self.assertEqual(created_names, {result["results"][0]["background"]})

    def test_operation_lock_rejects_concurrent_fetches(self) -> None:
        self.client.download_started = threading.Event()
        self.client.download_continue = threading.Event()
        errors: list[Exception] = []

        def fetch() -> None:
            try:
                self.manager.fetch_daily()
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=fetch)
        thread.start()
        self.assertTrue(self.client.download_started.wait(timeout=1))
        with self.assertRaises(bing.BingDailyBusy):
            self.manager.fetch_daily()
        self.client.download_continue.set()
        thread.join(timeout=2)
        self.assertEqual(errors, [])
