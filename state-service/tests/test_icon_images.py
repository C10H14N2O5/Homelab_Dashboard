from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image
from homelab_state import favicon, icon_images
from tests.helpers import FIXED_TIME


def fixture(fmt="PNG", animated=False):
    first = Image.new("RGBA", (80, 48))
    first.paste((255, 20, 30, 180), (4, 4, 28, 40))
    second = Image.new("RGBA", (80, 48))
    second.paste((0, 0, 255, 210), (52, 8, 76, 44))
    stream = io.BytesIO()
    options = {}
    if fmt == "JPEG":
        first = first.convert("RGB")
    if fmt == "ICO":
        first = Image.new("RGBA", (64, 64), (255, 0, 0, 120))
        options["sizes"] = [(16, 16), (32, 32), (64, 64)]
    if animated:
        options.update(save_all=True, append_images=[second], duration=[100, 200], loop=2)
    first.save(stream, format=fmt, **options)
    return stream.getvalue()


class IconImageTests(unittest.TestCase):
    def test_all_static_formats_preserve_aspect_and_normalize_to_png(self):
        for fmt in ("PNG", "JPEG", "WEBP", "GIF", "ICO"):
            with self.subTest(fmt=fmt):
                icon = icon_images.convert(fixture(fmt), True)
                self.assertEqual(icon["mimeType"], "image/png")
                self.assertFalse(icon["animated"])
                self.assertEqual((icon["width"], icon["height"]), (64, 64) if fmt == "ICO" else (80, 48))
                with Image.open(io.BytesIO(icon_images.decode_base64(icon["contentBase64"]))) as output:
                    self.assertEqual(output.format, "PNG")
                    if fmt == "PNG":
                        self.assertEqual(output.getpixel((0, 0))[3], 0)
                        self.assertEqual(output.getpixel((10, 10))[3], 180)
                    self.assertNotIn("exif", output.info)

    def test_animation_union_poster_duration_loop_and_static_favicon(self):
        for fmt in ("PNG", "WEBP", "GIF"):
            with self.subTest(fmt=fmt):
                data = fixture(fmt, True)
                icon = icon_images.convert(data, True)
                self.assertTrue(icon["animated"])
                self.assertEqual(icon["mimeType"], "image/webp")
                self.assertGreaterEqual(icon["displayBounds"][2], 76)
                with Image.open(io.BytesIO(icon_images.decode_base64(icon["contentBase64"]))) as image:
                    self.assertEqual(image.n_frames, 2)
                    self.assertEqual(image.info["loop"], 3 if fmt == "GIF" else 2)
                    durations = []
                    for index in range(2):
                        image.seek(index)
                        image.load()
                        durations.append(image.info["duration"])
                    self.assertEqual(durations, [100, 200])
                with Image.open(io.BytesIO(icon_images.decode_base64(icon["posterBase64"]))) as poster:
                    self.assertEqual(poster.size, (80, 48))
                static = icon_images.convert(data, False)
                self.assertFalse(static["animated"])
                self.assertEqual((static["width"], static["height"]), (80, 80))
                self.assertIsNone(static["posterBase64"])

    def test_default_apng_image_is_not_the_first_animation_frame(self):
        frames = [Image.new("RGBA", (16, 16), color) for color in ("red", "blue", "green")]
        stream = io.BytesIO()
        frames[0].save(stream, format="PNG", save_all=True, append_images=frames[1:], default_image=True, duration=100)
        icon = icon_images.convert(stream.getvalue(), False)
        with Image.open(io.BytesIO(icon_images.decode_base64(icon["contentBase64"]))) as image:
            self.assertEqual(image.getpixel((0, 0))[:3], (0, 0, 255))

    def test_invalid_base64_damaged_format_and_pixel_frame_limits(self):
        for text in ("", "%%%", "YQ==\n", 1):
            with self.assertRaises(ValueError):
                icon_images.decode_base64(text)
        with self.assertRaises(Exception):
            icon_images.convert(b"not a picture", True)
        stream = io.BytesIO()
        Image.new("RGB", (4, 4)).save(stream, format="BMP")
        with self.assertRaises(ValueError):
            icon_images.convert(stream.getvalue(), True)
        for name, value in (("MAX_PIXELS", 20), ("MAX_TOTAL_PIXELS", 20), ("MAX_FRAMES", 1)):
            with mock.patch.object(icon_images, name, value), self.assertRaises(ValueError):
                icon_images.convert(fixture("PNG", True), True)

    def test_worker_timeout_and_busy_limit(self):
        with mock.patch.object(icon_images._workers, "acquire", return_value=False), self.assertRaises(ValueError):
            icon_images.process_image(fixture(), True)
        process = mock.Mock()
        process.communicate.side_effect = [icon_images.subprocess.TimeoutExpired("worker", 10), (b"", b"")]
        process.poll.return_value = 0
        with mock.patch.object(icon_images.subprocess, "Popen", return_value=process), self.assertRaisesRegex(ValueError, "超时"):
            icon_images.process_image(fixture(), True)
        process.kill.assert_called_once()

    def test_v2_preview_atomic_persistence_svg_upgrade_and_remove(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / favicon.PAGE_ICON_STATE_FILE_NAME
            store = favicon.FaviconStore(path, lambda: FIXED_TIME)
            upload = {"fileName": "animation.gif", "contentBase64": icon_images.encode(fixture("GIF", True))}
            preview = store.prepare({"version": 2, **upload})
            self.assertFalse(path.exists())
            saved = store.update({"version": 2, "icon": upload})
            self.assertEqual(preview["metadata"]["contentHash"], saved["contentHash"])
            self.assertTrue(saved["animated"])
            restarted = favicon.FaviconStore(path, lambda: FIXED_TIME)
            self.assertEqual(restarted.get(), saved)
            self.assertEqual(restarted.get_image(saved["posterHash"], poster=True)[1], "image/png")
            with self.assertRaises(favicon.FaviconNotFound):
                restarted.get_asset(saved["contentHash"])
            before = path.read_bytes()
            with mock.patch.object(favicon, "atomic_write_json", side_effect=OSError("disk full")), self.assertRaises(OSError):
                store.update({"version": 2, "icon": None})
            self.assertEqual(path.read_bytes(), before)
            store.update({"version": 2, "icon": None})
            self.assertFalse(favicon.FaviconStore(path).get()["configured"])
            with self.assertRaises(favicon.FaviconNotFound):
                store.get_image(saved["contentHash"])
            store.update({"version": 1, "iconSvg": '<svg viewBox="0 0 24 24"><path d="M0 0h24v24z"/></svg>'})
            before = path.read_bytes()
            self.assertEqual(favicon.FaviconStore(path).get(modern=True)["mimeType"], "image/svg+xml")
            self.assertEqual(path.read_bytes(), before)

    def test_metadata_tampering_and_oversize_upload_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / favicon.FAVICON_STATE_FILE_NAME
            store = favicon.FaviconStore(path, lambda: FIXED_TIME)
            with self.assertRaises(favicon.InvalidFavicon):
                store.prepare({"version": 2, "fileName": "icon.png", "contentBase64": "A" * (7 * 1024 * 1024)})
            store.update({"version": 2, "icon": {"fileName": "icon.png", "contentBase64": icon_images.encode(fixture())}})
            record = json.loads(path.read_text())
            record["icon"]["width"] = 1
            path.write_text(json.dumps(record))
            with self.assertRaises(favicon.FaviconStateUnavailable):
                favicon.FaviconStore(path).get()
