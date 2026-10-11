"""Bounded conversion of untrusted icon files to canonical browser resources."""
from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import warnings

MAX_INPUT = 5 * 1024 * 1024
MAX_STATIC = 2 * 1024 * 1024
MAX_ANIMATION = 8 * 1024 * 1024
MAX_FRAMES = 200
MAX_PIXELS = 16_000_000
MAX_TOTAL_PIXELS = 64_000_000
_workers = threading.BoundedSemaphore(2)


def decode_base64(value, limit=MAX_INPUT):
    if not isinstance(value, str) or not value or len(value) > 4 * ((limit + 2) // 3):
        raise ValueError("图标内容为空或超过大小限制")
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("图标内容不是有效的 Base64") from exc
    if not data or len(data) > limit:
        raise ValueError("图标超过大小限制")
    return data


def encode(data):
    return base64.b64encode(data).decode("ascii")


def convert(data, allow_animation, canonical=False):
    from PIL import Image, ImageOps
    allowed = {"PNG", "JPEG", "WEBP", "GIF", "ICO"}
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in allowed:
                raise ValueError("仅支持 PNG、JPEG、WebP、GIF 和 ICO 图片")
            count = getattr(source, "n_frames", 1)
            if count > MAX_FRAMES:
                raise ValueError("动画最多允许 200 帧")
            start = 1 if source.format == "PNG" and source.info.get("default_image") else 0
            loop = source.info.get("loop", 1)
            if source.format == "GIF" and source.info.get("loop", 0) > 0:
                loop += 1  # GIF counts repetitions; WebP counts total plays.
            frames, durations = [], []
            total = 0
            for index in range(start, count):
                source.seek(index)
                width, height = source.size
                if canonical and max(width, height) > 512:
                    raise ValueError("已保存的图标画布超过限制")
                total += width * height
                if max(width, height) > 4096 or width * height > MAX_PIXELS or total > MAX_TOTAL_PIXELS:
                    raise ValueError("图片尺寸或动画累计解码尺寸超过限制")
                source.load()
                frame = ImageOps.exif_transpose(source).convert("RGBA")
                frame.thumbnail((512, 512), Image.Resampling.LANCZOS)
                # New images carry no EXIF/ICC/text metadata into the output.
                clean = Image.new("RGBA", frame.size)
                clean.paste(frame)
                frames.append(clean)
                durations.append(max(20, int(source.info.get("duration", 100))))
            if not frames:
                raise ValueError("图片没有可显示的画面")
    animated = allow_animation and len(frames) > 1
    if animated and all(frame.tobytes() == frames[0].tobytes() for frame in frames[1:]):
        animated = False
    if not allow_animation:
        frame = frames[0]
        side = max(frame.size)
        square = Image.new("RGBA", (side, side))
        square.paste(frame, ((side - frame.width) // 2, (side - frame.height) // 2))
        frames = [square]
    if not animated:
        frames = frames[:1]
    size = frames[0].size
    if any(frame.size != size for frame in frames):
        raise ValueError("动画画布尺寸不一致")
    bounds = None
    for frame in frames:
        box = frame.getchannel("A").getbbox()
        if box:
            bounds = box if bounds is None else (min(bounds[0], box[0]), min(bounds[1], box[1]),
                                                max(bounds[2], box[2]), max(bounds[3], box[3]))
    width, height = size
    if bounds is None:
        bounds = (0, 0, width, height)
    left, top, right, bottom = bounds
    bounds = [max(0, left - 1), max(0, top - 1), min(width, right + 1), min(height, bottom + 1)]
    output = io.BytesIO()
    if animated:
        frames[0].save(output, format="WEBP", save_all=True, append_images=frames[1:],
                       duration=durations, loop=loop, lossless=True, method=0, kmin=1, kmax=1)
    else:
        frames[0].save(output, format="PNG")
    content = output.getvalue()
    if len(content) > (MAX_ANIMATION if animated else MAX_STATIC):
        raise ValueError("处理后的图标过大，请缩小图片或减少动画帧数")
    poster = None
    if animated:
        output = io.BytesIO()
        # Derive the poster from the served WebP, including codec treatment of
        # invisible RGB values, so persisted validation is stable across restart.
        with Image.open(io.BytesIO(content)) as encoded:
            poster_frame = encoded.convert("RGBA")
            poster_frame.info.clear()
            poster_frame.save(output, format="PNG")
        poster = output.getvalue()
        if len(poster) > MAX_STATIC:
            raise ValueError("动画首帧过大")
    return {"mimeType": "image/webp" if animated else "image/png", "contentBase64": encode(content),
            "posterBase64": encode(poster) if poster else None, "width": width, "height": height,
            "animated": animated, "displayBounds": bounds}


def process_image(data, allow_animation, *, limit=MAX_INPUT, timeout=10, canonical=False):
    if len(data) > limit:
        raise ValueError("图标超过大小限制")
    if not _workers.acquire(timeout=0):
        raise ValueError("图标处理繁忙，请稍后重试")
    process = None
    try:
        command = [sys.executable, "--icon-worker"] if getattr(sys, "frozen", False) else [sys.executable, "-m", "homelab_state.icon_images"]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(Path(__file__).parent.parent) + os.pathsep + environment.get("PYTHONPATH", "")
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=environment, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            output, _ = process.communicate(json.dumps({"data": encode(data), "animate": allow_animation,
                                                        "limit": limit, "canonical": canonical}).encode(), timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=2)
            raise ValueError("图标处理超时，请缩小文件或减少动画帧数") from None
        if process.returncode or len(output) > 16 * 1024 * 1024:
            raise ValueError("图标处理失败")
        result = json.loads(output)
        if not result.get("ok"):
            raise ValueError(result.get("error", "图标处理失败"))
        return result["icon"]
    finally:
        # Never admit another conversion while an abandoned decoder is alive.
        if process is None or process.poll() is not None:
            _workers.release()


def main():
    try:
        request = json.loads(sys.stdin.buffer.read(16 * 1024 * 1024 + 1))
        result = {"ok": True, "icon": convert(decode_base64(request["data"], request["limit"]), request["animate"], request.get("canonical", False))}
    except Exception as exc:
        result = {"ok": False, "error": str(exc)}
    sys.stdout.buffer.write(json.dumps(result).encode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
