from __future__ import annotations

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from homelab_state.backgrounds import StateStore
from homelab_state.config import (
    CONFIG_VERSION,
    DEFAULT_BACKGROUND_DIRECTORY,
    DEFAULT_BING_INTERNATIONAL_PROXY,
    DEFAULT_DATA_DIRECTORY,
    DEFAULT_LISTEN,
    DEFAULT_ORIGIN,
    DEFAULT_PROBE_CONNECT_ADDRESS,
    DEFAULT_PROBE_DOMAIN_SUFFIX,
    DEFAULT_PROBE_TIMEOUT_SECONDS,
    AppConfig,
    _parse_config_value,
    default_config_path,
    load_config,
    validate_application_location,
)
from homelab_state.core import (
    SERVICE_NAME,
    SERVICE_VERSION,
    ConfigurationError,
    application_directory,
    atomic_write_json,
    configure_console_encoding,
)
from homelab_state.http_api import (
    API_PATH,
    CONTROL_PATH,
    HEALTH_PATH,
    StateHTTPServer,
)


MONITOR_REFRESH_SECONDS = 60.0
LOG_MAX_BYTES = 48 * 1024
LOG_BACKUP_COUNT = 1


def configure_logging(log_file: Path, include_console: bool = True) -> logging.Logger:
    logger = logging.getLogger(SERVICE_NAME)
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)sZ %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S")
    for path in (log_file, Path(f"{log_file}.1")):
        try:
            if path.stat().st_size > LOG_MAX_BYTES:
                path.unlink()
        except FileNotFoundError:
            pass
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
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
        server.probe_client.close()
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
    if (
        payload
        and payload.get("status") == "ok"
        and payload.get("service") == SERVICE_NAME
        and payload.get("version") == SERVICE_VERSION
    ):
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
    return [sys.executable, str(Path(__file__).resolve().parent.parent / "main.py"), *arguments]


def spawn_backend(config: AppConfig) -> subprocess.Popen[Any] | None:
    if health(config):
        return None
    if port_has_http_listener(config):
        raise RuntimeError(f"{config.listen} is occupied by another or incompatible HTTP service")
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


def confirm_monitor_shutdown() -> bool:
    clear_console()
    print("停止 API 并关闭 Monitor")
    print("=" * 60)
    print("此操作将停止 HomeLab Wallpaper State API，并关闭当前 Monitor。")
    print("请输入大写 Y 并按 Enter 确认；输入其它任何内容将取消操作。")
    try:
        return input("\n确认大退：") == "Y"
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def monitor(config: AppConfig) -> int:
    failure_count = 0
    recovery_attempted = False
    runtime_message = ""
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
                    runtime_message = f"{time.strftime('%H:%M:%S')} 后端已自动恢复"
                    status = health(config)
                    preferences = read_preferences(config) if status else None
                except Exception as error:
                    runtime_message = f"{time.strftime('%H:%M:%S')} 自动恢复失败：{error}"

        clear_console()
        print("HomeLab Wallpaper State Monitor")
        print("=" * 60)
        print(f"API 状态      ：{'正常运行' if status else '不可用'}")
        print(f"监听地址      ：{config.listen}")
        print(f"进程 PID      ：{status.get('pid') if status else '-'}")
        print(f"背景目录      ：{config.background_dir}")
        print(f"状态目录      ：{config.state_dir}")
        print(f"Bing 国际代理：{config.bing_international_proxy}")
        print(f"探测入口      ：{config.probe_connect_address}")
        print(f"探测域后缀    ：{config.probe_domain_suffix}")
        if preferences:
            print(f"横屏背景      ：{preferences.get('landscape', '-')}")
            print(f"竖屏背景      ：{preferences.get('portrait', '-')}")
            print(f"最后更新时间  ：{preferences.get('updatedAt', '-')}")
        else:
            print("横屏背景      ：-")
            print("竖屏背景      ：-")
            print("最后更新时间  ：-")
        if runtime_message:
            print(f"运行信息      ：{runtime_message}")
        print("\n最近日志：")
        logs = tail_lines(config.log_file)
        if logs:
            for line in logs:
                print(line.rstrip())
        else:
            print("（暂无日志）")
        print("\n按 R 立即刷新；按 Q 关闭监视器（API 将继续运行）；按 X 停止 API 并关闭监视器。")

        waited = 0.0
        force_refresh = False
        while waited < MONITOR_REFRESH_SECONDS and not force_refresh:
            if msvcrt and msvcrt.kbhit():
                key = msvcrt.getwch().lower()
                if key == "q":
                    return 0
                if key == "r":
                    force_refresh = True
                    break
                if key == "x":
                    if not confirm_monitor_shutdown():
                        runtime_message = f"{time.strftime('%H:%M:%S')} 已取消大退，API 继续运行"
                        force_refresh = True
                        break
                    try:
                        stopped = request_stop(config)
                    except Exception as error:
                        runtime_message = f"{time.strftime('%H:%M:%S')} 大退失败：{error}"
                        force_refresh = True
                        break
                    print("\nAPI 已停止，Monitor 已关闭。" if stopped else "\nAPI 当前未运行，Monitor 已关闭。")
                    return 0
            time.sleep(0.1)
            waited += 0.1


def prompt_value(label: str, default: str) -> str:
    value = input(f"{label} [{default}]：").lstrip("\ufeff").strip()
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
    print("HomeLab Wallpaper State 首次配置")
    print("=" * 60)
    print("配置、状态和日志不会放入公开的 web\\html 目录。\n")

    existing: dict[str, Any] = {}
    if config_path.is_file():
        try:
            existing = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    background_default = str(existing.get("backgroundDir", DEFAULT_BACKGROUND_DIRECTORY))
    origin_default = ",".join(existing.get("allowedOrigins", [DEFAULT_ORIGIN]))
    listen_default = str(existing.get("listen", DEFAULT_LISTEN))
    state_default = str(existing.get("stateDir", DEFAULT_DATA_DIRECTORY))
    bing_proxy_default = str(
        existing.get("bingInternationalProxy", DEFAULT_BING_INTERNATIONAL_PROXY)
    )
    probe_connect_default = str(existing.get("probeConnectAddress", DEFAULT_PROBE_CONNECT_ADDRESS))
    probe_domain_default = str(existing.get("probeDomainSuffix", DEFAULT_PROBE_DOMAIN_SUFFIX))
    probe_timeout_default = str(existing.get("probeTimeoutSeconds", DEFAULT_PROBE_TIMEOUT_SECONDS)).removesuffix(".0")

    while True:
        background_text = prompt_value("背景目录", background_default)
        origin_text = prompt_value("允许的首页 Origin（多个值用逗号分隔）", origin_default)
        listen_text = prompt_value("监听地址", listen_default)
        state_text = prompt_value("状态目录", state_default)
        bing_proxy_text = prompt_value("Bing 国际版 HTTP 代理", bing_proxy_default)
        probe_connect_text = prompt_value("Zoraxy 回环探测入口", probe_connect_default)
        probe_domain_text = prompt_value("允许探测的域名后缀", probe_domain_default)
        probe_timeout_text = prompt_value("单目标探测超时秒数（1-5）", probe_timeout_default)
        origins = [item.strip() for item in origin_text.split(",") if item.strip()]
        try:
            raw = {
                "version": CONFIG_VERSION,
                "backgroundDir": background_text,
                "allowedOrigins": origins,
                "listen": listen_text,
                "stateDir": state_text,
                "controlToken": existing.get("controlToken") or secrets.token_urlsafe(32),
                "bingInternationalProxy": bing_proxy_text,
                "probeConnectAddress": probe_connect_text,
                "probeDomainSuffix": probe_domain_text,
                "probeTimeoutSeconds": float(probe_timeout_text),
            }
            config = _parse_config_value(
                config_path,
                raw,
                create_state_directory=False,
            )
            validate_application_location(config)
            config.state_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_json(config.config_path, raw)
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
    parser = argparse.ArgumentParser(description="HomeLab dashboard state API and monitor")
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

        validate_application_location(config)
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
