"""Run an isolated, same-origin dashboard preview; never touches deployed state."""
from __future__ import annotations

import argparse
import io
import json
import mimetypes
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit, urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

PROJECT = Path(__file__).resolve().parent.parent


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--api-port", type=int, default=8788)
    parser.add_argument("--data-root", type=Path, default=PROJECT / "output" / "notes-preview")
    parser.add_argument("--empty", action="store_true", help="Do not create demonstration notes")
    args = parser.parse_args()
    if args.port == args.api_port:
        parser.error("preview and API ports must differ")
    for port in (args.port, args.api_port):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    data = args.data_root.resolve()
    data.mkdir(parents=True, exist_ok=True)
    backgrounds = data / "web" / "backgrounds"
    backgrounds.mkdir(parents=True, exist_ok=True)
    if not (backgrounds / "bg.jpg").exists():
        shutil.copyfile(PROJECT / "backgrounds" / "bg.jpg", backgrounds / "bg.jpg")
    origin = f"http://127.0.0.1:{args.port}"
    upstream = f"http://127.0.0.1:{args.api_port}"
    token = secrets.token_urlsafe(32)
    config_path = data / "HomeLabWallpaperState.json"
    config = {
        "version": 4, "backgroundDir": str(backgrounds),
        "allowedOrigins": [origin, f"http://localhost:{args.port}"],
        "listen": f"127.0.0.1:{args.api_port}", "stateDir": str(data / "dashboard-state"),
        "controlToken": token, "bingInternationalProxy": "127.0.0.1:7890",
        "probeConnectAddress": "127.0.0.1:443", "probeDomainSuffix": "home.example.com", "probeTimeoutSeconds": 1,
        "notes": {"enabled": True, "primaryDir": str(data / "notes-primary"), "backupDir": str(data / "notes-backup")},
    }
    write_json(config_path, config)
    log = (data / "backend-console.log").open("ab", buffering=0)
    process = subprocess.Popen(
        [sys.executable, str(PROJECT / "state-service" / "main.py"), "serve", "--config", str(config_path)],
        cwd=PROJECT, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    def api(path, method="GET", payload=None, raw=None, content_type="application/json"):
        body = raw if raw is not None else (json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None)
        headers = {"Origin": origin, "X-Requested-With": "homelab-dashboard"}
        if body is not None:
            headers["Content-Type"] = content_type
        with urlopen(Request(upstream + path, body, headers=headers, method=method), timeout=70) as response:
            return json.load(response)

    class PreviewHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def respond(self, status, body=b"", mime="text/plain; charset=utf-8"):
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def dispatch(self):
            raw_path = urlsplit(self.path).path
            if raw_path.startswith("/api/") or raw_path == "/healthz":
                connection = HTTPConnection("127.0.0.1", args.api_port, timeout=360)
                try:
                    lengths = self.headers.get_all("Content-Length", [])
                    if len(lengths) > 1 or self.headers.get("Transfer-Encoding"):
                        self.close_connection = True
                        self.respond(400, b"Invalid framing")
                        return
                    length = int(lengths[0]) if lengths else 0
                    if not 0 <= length <= 21 * 1024 * 1024:
                        self.close_connection = True
                        self.respond(413, b"Request too large")
                        return
                    body = self.rfile.read(length) if length else None
                    headers = {key: value for key, value in self.headers.items() if key.lower() not in {
                        "host", "connection", "content-length", "transfer-encoding", "accept-encoding", "expect",
                    }}
                    connection.request(self.command, self.path, body=body, headers=headers)
                    response = connection.getresponse()
                    content = response.read()
                    self.send_response(response.status)
                    for name, value in response.getheaders():
                        if name.lower() not in {"connection", "transfer-encoding", "content-length", "server", "date"}:
                            self.send_header(name, value)
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    if self.command != "HEAD":
                        self.wfile.write(content)
                except (OSError, ValueError):
                    self.close_connection = True
                    self.respond(502, b'{"error":"preview_backend_unavailable"}', "application/json")
                finally:
                    connection.close()
                return
            if self.command not in {"GET", "HEAD"}:
                self.respond(405)
                return
            path = unquote(raw_path)
            if path in {"/", "/index.html"}:
                target, root = PROJECT / "index.html", PROJECT
            elif path.startswith("/assets/"):
                root = PROJECT / "assets"
                target = root / path[len("/assets/"):]
            elif path.startswith("/backgrounds/"):
                root = backgrounds
                target = root / path[len("/backgrounds/"):]
            else:
                self.respond(404)
                return
            try:
                target = target.resolve()
                target.relative_to(root.resolve())
                if not target.is_file():
                    self.respond(404)
                    return
                mime = {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".html": "text/html"}.get(target.suffix.lower())
                mime = mime or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                self.respond(200, target.read_bytes(), mime)
            except (ValueError, OSError):
                self.respond(404)

        do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = dispatch

    server = None
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                api("/healthz")
                break
            except (OSError, URLError):
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError(f"Backend failed to start; inspect {data / 'backend-console.log'}")
                time.sleep(0.15)
        if not args.empty and not (data / "seeded.json").exists():
            if not api("/api/notes")["notes"]:
                from PIL import Image, ImageDraw
                sample = Image.new("RGB", (640, 240), "#14283c")
                draw = ImageDraw.Draw(sample)
                draw.rounded_rectangle((30, 30, 610, 210), radius=24, fill="#244664", outline="#55c9d5", width=3)
                draw.text((70, 90), "NOTES / LOCAL PREVIEW", fill="#e8f8ff", font_size=26)
                raw = io.BytesIO()
                sample.save(raw, format="PNG")
                image_id = str(uuid4())
                api("/api/notes/uploads?" + urlencode({"id": image_id, "name": "preview.png"}), "POST", raw=raw.getvalue(), content_type="application/octet-stream")
                demos = [
                    ("Markdown 示例", "# Markdown 示例\n\n**粗体**、*斜体*、~~删除线~~，以及 [项目文档](https://www.python.org/)。\n\n| 项目 | 状态 |\n| --- | --- |\n| 主存储 | 本地隔离目录 |\n| 图片与备份 | 本地测试目录 |\n\n```python\ndef hello(name):\n    return f'Hello, {name}!'\n```\n\n![本地上传图片](/api/notes/images/" + image_id + ")\n"),
                    ("待办清单", "# 视觉与端到端检查\n\n- [ ] 创建和编辑一篇笔记\n- [ ] 切换编辑 / 预览\n- [ ] 点击任务复选框\n- [ ] 上传或粘贴图片\n- [ ] 保存后刷新页面\n- [ ] 调整列表顺序\n- [ ] 缩窄窗口检查手机布局\n\n> 这些是测试样例，可以自由修改或删除。\n"),
                    ("欢迎使用笔记", "# 记录当下，随时回来\n\n这是独立的本地测试实例。\n\n左侧选择笔记，点击 **编辑** 修改 Markdown；底部 **保存全部** 将所有修改同步到本地测试后端。\n\n- 保存设置位于网页底部的 **网页设置 → 笔记设置**。\n- 浏览器刷新后可以检查保存与草稿恢复。\n- 测试数据保留在 `output/notes-preview`，不会写入现有部署。\n\n可以从左侧的 **新建笔记** 开始。\n"),
                ]
                for title, body in demos:
                    api("/api/notes/" + str(uuid4()), "PUT", {"expectedRevision": None, "operationId": str(uuid4()), "title": title, "body": body})
            write_json(data / "seeded.json", {"seeded": True})
        server = ThreadingHTTPServer(("127.0.0.1", args.port), PreviewHandler)
        write_json(data / "preview.json", {"url": origin + "/", "pid": os.getpid(), "apiPid": process.pid, "dataRoot": str(data)})
        print(f"Notes preview: {origin}/", flush=True)
        def stop(_signum, _frame):
            threading.Thread(target=server.shutdown, daemon=True).start()
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        server.serve_forever(poll_interval=0.25)
    finally:
        if server:
            server.server_close()
        if process.poll() is None:
            try:
                with urlopen(Request(upstream + "/_control/shutdown", data=b"", headers={"X-Control-Token": token}, method="POST"), timeout=3):
                    pass
                process.wait(timeout=8)
            except (OSError, subprocess.TimeoutExpired):
                process.terminate()
                process.wait(timeout=5)
        log.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
