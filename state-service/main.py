from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


API_PATH = "/api/background-preferences"
HEALTH_PATH = "/healthz"
CONTROL_PATH = "/_control/shutdown"
DEFAULT_CONFIG_NAME = "HomeLabWallpaperState.json"
DEFAULT_DATA_DIRECTORY = "HomeLabWallpaperState-data"
DEFAULT_BACKGROUND = "bg.jpg"
DEFAULT_ORIGIN = "https://home.c10h14n2o5.com"
DEFAULT_LISTEN = "127.0.0.1:8787"
SERVICE_NAME = "HomeLabWallpaperState"
SERVICE_VERSION = 1
STATE_VERSION = 1
MAX_PATCH_BODY = 4 * 1024
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".avif"}


class ConfigurationError(ValueError):
    pass


class InvalidBackground(ValueError):
    pass


class BackgroundMissing(FileNotFoundError):
    pass


def application_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def default_config_path() -> Path:
    return application_directory() / DEFAULT_CONFIG_NAME


def configure_console_encoding() -> None:
    if os.name != "nt":
        return
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleCP(65001)
        kernel32.SetConsoleOutputCP(65001)
    except (AttributeError, OSError):
        # Redirected and detached processes may not have an attached console.
        pass
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            # Keep startup working for streams supplied by non-console hosts.
            pass


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_origin(raw: str) -> str:
    value = raw.strip()
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ConfigurationError("origin must use http or https")
    if not parsed.netloc or parsed.username or parsed.password:
        raise ConfigurationError("origin must contain a host and no credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ConfigurationError("origin must not contain a path, query, or fragment")
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def parse_listen(value: str) -> tuple[str, int]:
    text = value.strip()
    if text.count(":") != 1:
        raise ConfigurationError("listen must use 127.0.0.1:<port>")
    host, port_text = text.rsplit(":", 1)
    if host != "127.0.0.1":
        raise ConfigurationError("listen must bind to 127.0.0.1")
    try:
        port = int(port_text)
    except ValueError as error:
        raise ConfigurationError("listen port must be an integer") from error
    if not 1 <= port <= 65535:
        raise ConfigurationError("listen port must be between 1 and 65535")
    return host, port


def path_is_within(candidate: Path, directory: Path) -> bool:
    try:
        candidate.resolve().relative_to(directory.resolve())
        return True
    except ValueError:
        return False


def resolve_config_path(value: str, base: Path) -> Path:
    path = Path(os.path.expandvars(value)).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


@dataclass(frozen=True)
class AppConfig:
    config_path: Path
    background_dir: Path
    allowed_origins: frozenset[str]
    listen_host: str
    listen_port: int
    state_dir: Path
    control_token: str

    @property
    def listen(self) -> str:
        return f"{self.listen_host}:{self.listen_port}"

    @property
    def state_file(self) -> Path:
        return self.state_dir / "state.json"

    @property
    def log_file(self) -> Path:
        return self.state_dir / "service.log"

    @property
    def local_base_url(self) -> str:
        return f"http://{self.listen}"


def load_config(path: Path) -> AppConfig:
    config_path = path.resolve()
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigurationError(f"configuration file does not exist: {config_path}") from None
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigurationError(f"unable to read configuration: {error}") from error
    if not isinstance(raw, dict):
        raise ConfigurationError("configuration must be a JSON object")
    expected = {"version", "backgroundDir", "allowedOrigins", "listen", "stateDir", "controlToken"}
    if set(raw) != expected:
        missing = sorted(expected - set(raw))
        unknown = sorted(set(raw) - expected)
        details = []
        if missing:
            details.append(f"missing fields: {', '.join(missing)}")
        if unknown:
            details.append(f"unknown fields: {', '.join(unknown)}")
        raise ConfigurationError("; ".join(details))
    if raw["version"] != 1:
        raise ConfigurationError(f"unsupported configuration version: {raw['version']!r}")
    if not isinstance(raw["backgroundDir"], str) or not raw["backgroundDir"].strip():
        raise ConfigurationError("backgroundDir must be a non-empty string")
    if not isinstance(raw["stateDir"], str) or not raw["stateDir"].strip():
        raise ConfigurationError("stateDir must be a non-empty string")
    origins = raw["allowedOrigins"]
    if not isinstance(origins, list) or not origins or not all(isinstance(item, str) for item in origins):
        raise ConfigurationError("allowedOrigins must be a non-empty string array")
    if not isinstance(raw["listen"], str):
        raise ConfigurationError("listen must be a string")
    token = raw["controlToken"]
    if not isinstance(token, str) or len(token) < 32:
        raise ConfigurationError("controlToken must contain at least 32 characters")

    base = config_path.parent
    background_dir = resolve_config_path(raw["backgroundDir"], base)
    state_dir = resolve_config_path(raw["stateDir"], base)
    host, port = parse_listen(raw["listen"])
    normalized_origins = frozenset(normalize_origin(item) for item in origins)
    if not background_dir.is_dir():
        raise ConfigurationError(f"background directory does not exist: {background_dir}")
    if not (background_dir / DEFAULT_BACKGROUND).is_file():
        raise ConfigurationError(f"background directory must contain {DEFAULT_BACKGROUND}: {background_dir}")
    public_root = background_dir.parent
    if path_is_within(config_path, public_root):
        raise ConfigurationError("configuration file must be outside the public Web root")
    if path_is_within(state_dir, public_root):
        raise ConfigurationError("stateDir must be outside the public Web root")
    state_dir.mkdir(parents=True, exist_ok=True)
    return AppConfig(
        config_path=config_path,
        background_dir=background_dir,
        allowed_origins=normalized_origins,
        listen_host=host,
        listen_port=port,
        state_dir=state_dir,
        control_token=token,
    )


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_background_name(name: str) -> str:
    if not isinstance(name, str) or not name or name.strip() != name:
        raise InvalidBackground("invalid background filename")
    if name in {".", ".."} or any(character in name for character in "/\\:\x00"):
        raise InvalidBackground("invalid background filename")
    if Path(name).name != name or Path(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise InvalidBackground("invalid background filename")
    return name


class StateStore:
    def __init__(self, background_dir: Path, state_file: Path, clock=utc_timestamp) -> None:
        self._lock = threading.Lock()
        self.background_dir = background_dir.resolve()
        self.state_file = state_file.resolve()
        self.clock = clock
        self.state = self._load_or_initialize()

    def _require_background(self, name: str) -> None:
        validate_background_name(name)
        path = self.background_dir / name
        if not path.is_file() or path.is_symlink():
            raise BackgroundMissing("background file does not exist")

    def _new_state(self) -> dict[str, Any]:
        self._require_background(DEFAULT_BACKGROUND)
        return {
            "version": STATE_VERSION,
            "landscape": DEFAULT_BACKGROUND,
            "portrait": DEFAULT_BACKGROUND,
            "updatedAt": self.clock(),
        }

    def _validate_state_shape(self, state: Any) -> dict[str, Any]:
        if not isinstance(state, dict):
            raise ValueError("state file must contain a JSON object")
        expected = {"version", "landscape", "portrait", "updatedAt"}
        if set(state) != expected:
            raise ValueError("state file contains missing or unknown fields")
        if state["version"] != STATE_VERSION:
            raise ValueError(f"unsupported state version {state['version']!r}")
        if not all(isinstance(state[key], str) for key in ("landscape", "portrait", "updatedAt")):
            raise ValueError("state fields have invalid types")
        return dict(state)

    def _load_or_initialize(self) -> dict[str, Any]:
        try:
            state = self._validate_state_shape(json.loads(self.state_file.read_text(encoding="utf-8")))
        except FileNotFoundError:
            state = self._new_state()
            atomic_write_json(self.state_file, state)
            return state
        changed = self._heal(state)
        try:
            datetime.fromisoformat(state["updatedAt"].replace("Z", "+00:00"))
        except ValueError:
            changed = True
        if changed:
            state["updatedAt"] = self.clock()
            atomic_write_json(self.state_file, state)
        return state

    def _heal(self, state: dict[str, Any]) -> bool:
        self._require_background(DEFAULT_BACKGROUND)
        changed = False
        for mode in ("landscape", "portrait"):
            try:
                self._require_background(state[mode])
            except (InvalidBackground, BackgroundMissing):
                state[mode] = DEFAULT_BACKGROUND
                changed = True
        return changed

    def get(self) -> dict[str, Any]:
        with self._lock:
            previous = dict(self.state)
            changed = self._heal(self.state)
            if changed:
                self.state["updatedAt"] = self.clock()
                try:
                    atomic_write_json(self.state_file, self.state)
                except OSError:
                    self.state = previous
                    raise
            return dict(self.state)

    def update(self, mode: str, background: str) -> dict[str, Any]:
        if mode not in {"landscape", "portrait"}:
            raise ValueError("mode must be landscape or portrait")
        with self._lock:
            self._require_background(background)
            previous = dict(self.state)
            changed = self._heal(self.state)
            if self.state[mode] != background:
                self.state[mode] = background
                changed = True
            if changed:
                self.state["updatedAt"] = self.clock()
                try:
                    atomic_write_json(self.state_file, self.state)
                except OSError:
                    self.state = previous
                    raise
            return dict(self.state)


class StateHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, config: AppConfig, store: StateStore, logger: logging.Logger) -> None:
        self.config = config
        self.store = store
        self.logger = logger
        super().__init__((config.listen_host, config.listen_port), StateRequestHandler)


class StateRequestHandler(BaseHTTPRequestHandler):
    server: StateHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format_string: str, *args: Any) -> None:
        self.server.logger.info("%s - %s", self.address_string(), format_string % args)

    def _common_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")

    def _write_json(self, status: int, value: Any) -> None:
        body = (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        self.send_response(status)
        self._common_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._write_json(status, {"error": message})

    def do_GET(self) -> None:
        if self.path == API_PATH:
            try:
                self._write_json(HTTPStatus.OK, self.server.store.get())
            except Exception as error:
                self.server.logger.exception("GET %s failed", self.path)
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal server error")
            return
        if self.path == HEALTH_PATH:
            try:
                self.server.store.get()
            except Exception:
                self.server.logger.exception("health check failed")
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "unhealthy")
                return
            self._write_json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "service": SERVICE_NAME,
                    "version": SERVICE_VERSION,
                    "pid": os.getpid(),
                },
            )
            return
        self._error(HTTPStatus.NOT_FOUND, "not found")

    def do_PATCH(self) -> None:
        if self.path != API_PATH:
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        origin_values = self.headers.get_all("Origin", failobj=[])
        try:
            normalized_origin = normalize_origin(origin_values[0]) if len(origin_values) == 1 else ""
        except ConfigurationError:
            normalized_origin = ""
        if normalized_origin not in self.server.config.allowed_origins:
            self._error(HTTPStatus.FORBIDDEN, "origin is not allowed")
            return
        if self.headers.get("X-Requested-With") != "homelab-dashboard":
            self._error(HTTPStatus.FORBIDDEN, "missing dashboard request header")
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Content-Type must be application/json")
            return
        try:
            content_length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "invalid Content-Length")
            return
        if content_length < 0 or content_length > MAX_PATCH_BODY:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request body is too large")
            return
        body = self.rfile.read(content_length)
        try:
            request = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._error(HTTPStatus.BAD_REQUEST, "invalid JSON request")
            return
        if not isinstance(request, dict) or set(request) != {"mode", "background"}:
            self._error(HTTPStatus.BAD_REQUEST, "request must contain only mode and background")
            return
        if not isinstance(request["mode"], str) or request["mode"] not in {"landscape", "portrait"}:
            self._error(HTTPStatus.BAD_REQUEST, "mode must be landscape or portrait")
            return
        if not isinstance(request["background"], str):
            self._error(HTTPStatus.BAD_REQUEST, "invalid background filename")
            return
        try:
            state = self.server.store.update(request["mode"], request["background"])
        except InvalidBackground:
            self._error(HTTPStatus.BAD_REQUEST, "invalid background filename")
            return
        except BackgroundMissing:
            self._error(HTTPStatus.NOT_FOUND, "background file does not exist")
            return
        except Exception:
            self.server.logger.exception("PATCH %s failed", self.path)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal server error")
            return
        self._write_json(HTTPStatus.OK, state)

    def do_POST(self) -> None:
        if self.path != CONTROL_PATH:
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        if not secrets.compare_digest(self.headers.get("X-Control-Token", ""), self.server.config.control_token):
            self._error(HTTPStatus.FORBIDDEN, "invalid control token")
            return
        self._write_json(HTTPStatus.ACCEPTED, {"status": "stopping"})
        threading.Thread(target=self.server.shutdown, name="shutdown-request", daemon=True).start()

    def do_PUT(self) -> None:
        self._method_not_allowed()

    def do_DELETE(self) -> None:
        self._method_not_allowed()

    def _method_not_allowed(self) -> None:
        if self.path == API_PATH:
            self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
            self._common_headers()
            self.send_header("Allow", "GET, PATCH")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self._error(HTTPStatus.NOT_FOUND, "not found")


def configure_logging(log_file: Path, include_console: bool = True) -> logging.Logger:
    logger = logging.getLogger(SERVICE_NAME)
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)sZ %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S")
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    if include_console:
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        logger.addHandler(console)
    return logger


def serve(config: AppConfig) -> int:
    logger = configure_logging(config.log_file)
    try:
        store = StateStore(config.background_dir, config.state_file)
        server = StateHTTPServer(config, store, logger)
    except Exception as error:
        logger.exception("unable to start service: %s", error)
        return 1

    stopping = threading.Event()

    def request_shutdown(signum: int, _frame: Any) -> None:
        if stopping.is_set():
            return
        stopping.set()
        logger.info("shutdown requested by signal %s", signum)
        threading.Thread(target=server.shutdown, name="signal-shutdown", daemon=True).start()

    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(signum, request_shutdown)
        except (AttributeError, ValueError):
            pass
    logger.info("wallpaper state service listening on %s", config.listen)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        logger.info("wallpaper state service stopped")
    return 0


def read_json_url(url: str, timeout: float = 2.0, request: Request | None = None) -> dict[str, Any] | None:
    try:
        with urlopen(request or url, timeout=timeout) as response:
            payload = json.load(response)
    except (OSError, HTTPError, URLError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def health(config: AppConfig, timeout: float = 1.5) -> dict[str, Any] | None:
    payload = read_json_url(f"{config.local_base_url}{HEALTH_PATH}", timeout)
    if payload and payload.get("status") == "ok" and payload.get("service") == SERVICE_NAME:
        return payload
    return None


def port_has_http_listener(config: AppConfig) -> bool:
    try:
        with urlopen(f"{config.local_base_url}{HEALTH_PATH}", timeout=1.0):
            return True
    except HTTPError:
        return True
    except (OSError, URLError):
        return False


def executable_command(*arguments: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, *arguments]
    return [sys.executable, str(Path(__file__).resolve()), *arguments]


def spawn_backend(config: AppConfig) -> subprocess.Popen[Any] | None:
    if health(config):
        return None
    if port_has_http_listener(config):
        raise RuntimeError(f"{config.listen} is occupied by another HTTP service")
    creationflags = 0
    start_new_session = False
    if os.name == "nt":
        creationflags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    else:
        start_new_session = True
    return subprocess.Popen(
        executable_command("serve", "--config", str(config.config_path)),
        cwd=application_directory(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
        start_new_session=start_new_session,
        close_fds=True,
    )


def ensure_backend(config: AppConfig, timeout: float = 15.0) -> dict[str, Any]:
    current = health(config)
    if current:
        return current
    spawn_backend(config)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = health(config)
        if current:
            return current
        time.sleep(0.25)
    raise RuntimeError(f"API did not become healthy within {timeout:g} seconds; inspect {config.log_file}")


def request_stop(config: AppConfig, timeout: float = 10.0) -> bool:
    if not health(config):
        return False
    request = Request(
        f"{config.local_base_url}{CONTROL_PATH}",
        data=b"",
        method="POST",
        headers={"X-Control-Token": config.control_token},
    )
    payload = read_json_url(request.full_url, 2.0, request)
    if not payload or payload.get("status") != "stopping":
        raise RuntimeError("backend rejected the shutdown request")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not health(config, 0.5):
            return True
        time.sleep(0.2)
    raise RuntimeError("backend did not stop within the timeout")


def tail_lines(path: Path, count: int = 12) -> list[str]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            return stream.readlines()[-count:]
    except OSError:
        return []


def read_preferences(config: AppConfig) -> dict[str, Any] | None:
    return read_json_url(f"{config.local_base_url}{API_PATH}", timeout=1.5)


def clear_console() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def monitor(config: AppConfig) -> int:
    failure_count = 0
    recovery_attempted = False
    recovery_message = ""
    force_refresh = True
    try:
        import msvcrt
    except ImportError:
        msvcrt = None

    while True:
        status = health(config)
        if status:
            failure_count = 0
            recovery_attempted = False
            preferences = read_preferences(config)
        else:
            failure_count += 1
            preferences = None
            if failure_count >= 3 and not recovery_attempted:
                recovery_attempted = True
                try:
                    ensure_backend(config)
                    recovery_message = f"{time.strftime('%H:%M:%S')} 后端已自动恢复"
                    status = health(config)
                    preferences = read_preferences(config) if status else None
                except Exception as error:
                    recovery_message = f"{time.strftime('%H:%M:%S')} 自动恢复失败：{error}"

        clear_console()
        print("HomeLab Wallpaper State Monitor")
        print("=" * 60)
        print(f"API 状态      ：{'正常运行' if status else '不可用'}")
        print(f"监听地址      ：{config.listen}")
        print(f"进程 PID      ：{status.get('pid') if status else '-'}")
        print(f"背景目录      ：{config.background_dir}")
        print(f"状态目录      ：{config.state_dir}")
        if preferences:
            print(f"横屏背景      ：{preferences.get('landscape', '-')}")
            print(f"竖屏背景      ：{preferences.get('portrait', '-')}")
            print(f"最后更新时间  ：{preferences.get('updatedAt', '-')}")
        else:
            print("横屏背景      ：-")
            print("竖屏背景      ：-")
            print("最后更新时间  ：-")
        if recovery_message:
            print(f"恢复信息      ：{recovery_message}")
        print("\n最近日志：")
        logs = tail_lines(config.log_file)
        if logs:
            for line in logs:
                print(line.rstrip())
        else:
            print("（暂无日志）")
        print("\n按 R 立即刷新；按 Q 关闭监视器（API 将继续运行）。")

        waited = 0.0
        force_refresh = False
        while waited < 2.0 and not force_refresh:
            if msvcrt and msvcrt.kbhit():
                key = msvcrt.getwch().lower()
                if key == "q":
                    return 0
                if key == "r":
                    force_refresh = True
                    break
            time.sleep(0.1)
            waited += 0.1


def prompt_value(label: str, default: str) -> str:
    value = input(f"{label} [{default}]：").strip()
    return value or default


def prompt_yes_no(label: str, default: bool = True) -> bool:
    suffix = "Y/n" if default else "y/N"
    value = input(f"{label} [{suffix}]：").strip().lower()
    if not value:
        return default
    return value in {"y", "yes", "是"}


def powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def create_startup_shortcut(executable: Path) -> Path:
    if os.name != "nt":
        raise RuntimeError("Startup shortcuts are only supported on Windows")
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA is unavailable")
    startup = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    startup.mkdir(parents=True, exist_ok=True)
    shortcut = startup / f"{SERVICE_NAME}.lnk"
    script = (
        "$shell = New-Object -ComObject WScript.Shell\n"
        f"$shortcut = $shell.CreateShortcut({powershell_literal(str(shortcut))})\n"
        f"$shortcut.TargetPath = {powershell_literal(str(executable))}\n"
        f"$shortcut.WorkingDirectory = {powershell_literal(str(executable.parent))}\n"
        "$shortcut.Description = 'HomeLab wallpaper API and monitor'\n"
        "$shortcut.Save()\n"
    )
    encoded = __import__("base64").b64encode(script.encode("utf-16le")).decode("ascii")
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not shortcut.is_file():
        raise RuntimeError(f"unable to create Startup shortcut: {result.stderr.strip()}")
    return shortcut


def run_configuration_wizard(config_path: Path) -> AppConfig:
    app_dir = config_path.resolve().parent
    print("HomeLab Wallpaper State 首次配置")
    print("=" * 60)
    print("配置、状态和日志不会放入公开的 web\\html 目录。\n")

    existing: dict[str, Any] = {}
    if config_path.is_file():
        try:
            existing = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    background_default = str(existing.get("backgroundDir", r"web\html\backgrounds"))
    origin_default = ",".join(existing.get("allowedOrigins", [DEFAULT_ORIGIN]))
    listen_default = str(existing.get("listen", DEFAULT_LISTEN))
    state_default = str(existing.get("stateDir", DEFAULT_DATA_DIRECTORY))

    while True:
        background_text = prompt_value("背景目录", background_default)
        origin_text = prompt_value("允许的首页 Origin（多个值用逗号分隔）", origin_default)
        listen_text = prompt_value("监听地址", listen_default)
        state_text = prompt_value("状态目录", state_default)
        origins = [item.strip() for item in origin_text.split(",") if item.strip()]
        raw = {
            "version": 1,
            "backgroundDir": background_text,
            "allowedOrigins": origins,
            "listen": listen_text,
            "stateDir": state_text,
            "controlToken": existing.get("controlToken") or secrets.token_urlsafe(32),
        }
        try:
            atomic_write_json(config_path, raw)
            config = load_config(config_path)
            if path_is_within(app_dir, config.background_dir.parent):
                raise ConfigurationError("EXE directory must be outside the public Web root")
            break
        except Exception as error:
            print(f"\n配置无效：{error}")
            print("请重新输入。\n")

    legacy_state = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / SERVICE_NAME / "state.json"
    if not config.state_file.exists() and legacy_state.is_file() and prompt_yes_no(f"发现旧状态 {legacy_state}，是否导入"):
        shutil.copy2(legacy_state, config.state_file)
        StateStore(config.background_dir, config.state_file)
        print(f"已导入状态：{config.state_file}")

    if getattr(sys, "frozen", False) and prompt_yes_no("是否创建当前用户的开机启动快捷方式"):
        shortcut = create_startup_shortcut(Path(sys.executable).resolve())
        print(f"已创建快捷方式：{shortcut}")
    elif not getattr(sys, "frozen", False):
        print("源码运行模式不创建 Startup 快捷方式；打包后的 EXE 会提供该选项。")
    print(f"配置已保存：{config.config_path}\n")
    return config


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HomeLab wallpaper state API and monitor")
    subparsers = parser.add_subparsers(dest="command")
    for name in ("serve", "monitor", "stop", "restart"):
        command = subparsers.add_parser(name)
        command.add_argument("--config", type=Path, default=default_config_path())
    configure = subparsers.add_parser("configure")
    configure.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--config", type=Path, default=default_config_path())
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    configure_console_encoding()
    arguments = parse_arguments(list(sys.argv[1:] if argv is None else argv))
    config_path = arguments.config.resolve()
    try:
        if arguments.command == "configure":
            run_configuration_wizard(config_path)
            return 0
        if not config_path.is_file():
            if arguments.command in {None, "monitor"}:
                config = run_configuration_wizard(config_path)
            else:
                raise ConfigurationError(f"configuration file does not exist: {config_path}")
        else:
            config = load_config(config_path)

        if arguments.command == "serve":
            return serve(config)
        if arguments.command == "stop":
            stopped = request_stop(config)
            print("API 已停止。" if stopped else "API 当前未运行。")
            return 0
        if arguments.command == "restart":
            request_stop(config)
            ensure_backend(config)
            print("API 已重新启动。")
            return 0
        if arguments.command == "monitor":
            return monitor(config)
        ensure_backend(config)
        return monitor(config)
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        print(f"{SERVICE_NAME}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
