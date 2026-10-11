"""Exercise multi-format icons and persistence against the actual packaged EXE."""
from __future__ import annotations
import argparse
import base64
import io
import json
import subprocess
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from PIL import Image


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("exe", type=Path)
    parser.add_argument("config", type=Path)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    base = "http://" + config["listen"]
    headers = {"Origin": config["allowedOrigins"][0], "X-Requested-With": "homelab-dashboard",
               "Content-Type": "application/json", "X-Icon-Format-Version": "2"}
    process = None

    def request(path, method="GET", value=None, extra=None):
        body = json.dumps(value).encode() if value is not None else None
        return urlopen(Request(base + path, data=body, headers=headers | (extra or {}), method=method), timeout=30)

    def api(path, method="GET", value=None):
        with request(path, method, value) as response:
            return json.load(response)

    def start():
        nonlocal process
        process = subprocess.Popen([str(args.exe), "serve", "--config", str(args.config)],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            try:
                health = api("/healthz")
                assert health["version"] == 15
                return
            except OSError:
                if process.poll() is not None:
                    raise RuntimeError("Packaged icon smoke backend exited")
                time.sleep(.2)
        raise RuntimeError("Packaged icon smoke startup timeout")

    def stop():
        if process and process.poll() is None:
            with request("/_control/shutdown", "POST", extra={"X-Control-Token": config["controlToken"]}):
                pass
            process.wait(timeout=10)

    frames = [Image.new("RGBA", (48, 32), color) for color in ((255, 0, 0, 150), (0, 0, 255, 200))]
    image = io.BytesIO()
    frames[0].save(image, format="PNG", save_all=True, append_images=frames[1:], duration=[100, 200], loop=0)
    upload = {"fileName": "smoke.apng", "contentBase64": base64.b64encode(image.getvalue()).decode()}
    saved = {}
    try:
        start()
        for kind in ("favicon", "page-icon"):
            path = "/api/" + kind
            previous = api(path)
            preview = api(path + "/preview", "POST", {"version": 2, **upload})
            assert api(path) == previous, "Preview wrote state"
            saved[kind] = api(path, "PATCH", {"version": 2, "icon": upload})
            assert saved[kind]["contentHash"] == preview["metadata"]["contentHash"]
            assert saved[kind]["animated"] == (kind == "page-icon")
        stop()
        start()
        for kind, state in saved.items():
            path = "/api/" + kind
            assert api(path) == state, "Configured image did not survive restart"
            with request(path + "/image?v=" + state["contentHash"]) as response:
                assert response.headers["Content-Type"] == state["mimeType"]
                assert response.headers["X-Content-Type-Options"] == "nosniff"
                with Image.open(io.BytesIO(response.read())) as decoded:
                    assert decoded.n_frames == (2 if kind == "page-icon" else 1)
            if state["animated"]:
                with request(path + "/poster.png?v=" + state["posterHash"]) as response:
                    assert response.headers["Content-Type"] == "image/png"
            api(path, "PATCH", {"version": 2, "icon": None})
        stop()
        start()
        for kind, state in saved.items():
            path = "/api/" + kind
            assert not api(path)["configured"], "Removal did not survive restart"
            for resource in (path + "/image?v=" + state["contentHash"],):
                try:
                    request(resource)
                    raise AssertionError("Removed image still served")
                except HTTPError as error:
                    assert error.code == 404
        print("Packaged multi-format icon preview/resources/poster/restart/removal passed.")
    finally:
        try:
            stop()
        finally:
            if process and process.poll() is None:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    main()
