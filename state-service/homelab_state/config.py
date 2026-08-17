from __future__ import annotations

import ipaddress
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from homelab_state.core import (
    DEFAULT_BACKGROUND,
    ConfigurationError,
    application_directory,
    path_is_within,
)


DEFAULT_CONFIG_NAME = "HomeLabWallpaperState.json"
DEFAULT_DATA_DIRECTORY = "HomeLabWallpaperState-data"
DEFAULT_BACKGROUND_DIRECTORY = r"D:\Zoraxy\www\html\backgrounds"
DEFAULT_ORIGIN = "https://home.example.com"
DEFAULT_LISTEN = "127.0.0.1:8787"
DEFAULT_PROBE_CONNECT_ADDRESS = "127.0.0.1:443"
DEFAULT_PROBE_DOMAIN_SUFFIX = "home.example.com"
DEFAULT_PROBE_TIMEOUT_SECONDS = 5.0
CONFIG_VERSION = 2
DOMAIN_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def default_config_path() -> Path:
    return application_directory() / DEFAULT_CONFIG_NAME


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


def parse_probe_connect_address(value: str) -> tuple[str, int]:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError("probeConnectAddress must be a non-empty string")
    text = value.strip()
    try:
        parsed = urlsplit(f"//{text}")
        host = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise ConfigurationError("probeConnectAddress must contain a loopback IP and port") from error
    if not host or port is None or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise ConfigurationError("probeConnectAddress must contain a loopback IP and port")
    try:
        address = ipaddress.ip_address(host)
    except ValueError as error:
        raise ConfigurationError("probeConnectAddress host must be a loopback IP address") from error
    if not address.is_loopback:
        raise ConfigurationError("probeConnectAddress host must be a loopback IP address")
    if not 1 <= port <= 65535:
        raise ConfigurationError("probeConnectAddress port must be between 1 and 65535")
    return str(address), port


def normalize_probe_domain_suffix(value: str) -> str:
    if not isinstance(value, str) or value != value.strip() or not value:
        raise ConfigurationError("probeDomainSuffix must be a canonical domain name")
    domain = value.lower()
    if len(domain) > 253 or domain.endswith(".") or "." not in domain:
        raise ConfigurationError("probeDomainSuffix must be a canonical domain name")
    if any(not DOMAIN_LABEL_PATTERN.fullmatch(label) for label in domain.split(".")):
        raise ConfigurationError("probeDomainSuffix must be a canonical domain name")
    return domain


def normalize_probe_timeout(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError("probeTimeoutSeconds must be a number")
    timeout = float(value)
    if not 1.0 <= timeout <= 5.0:
        raise ConfigurationError("probeTimeoutSeconds must be between 1 and 5")
    return timeout


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
    probe_connect_host: str
    probe_connect_port: int
    probe_domain_suffix: str
    probe_timeout_seconds: float

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

    @property
    def probe_connect_address(self) -> str:
        host = f"[{self.probe_connect_host}]" if ":" in self.probe_connect_host else self.probe_connect_host
        return f"{host}:{self.probe_connect_port}"


def validate_application_location(config: AppConfig) -> None:
    if path_is_within(application_directory(), config.background_dir.parent):
        raise ConfigurationError("application directory must be outside the public Web root")


def _parse_config_value(
    config_path: Path,
    raw: Any,
    *,
    create_state_directory: bool,
) -> AppConfig:
    config_path = config_path.resolve()
    if not isinstance(raw, dict):
        raise ConfigurationError("configuration must be a JSON object")
    expected = {
        "version",
        "backgroundDir",
        "allowedOrigins",
        "listen",
        "stateDir",
        "controlToken",
        "probeConnectAddress",
        "probeDomainSuffix",
        "probeTimeoutSeconds",
    }
    if set(raw) != expected:
        missing = sorted(expected - set(raw))
        unknown = sorted(set(raw) - expected)
        details = []
        if missing:
            details.append(f"missing fields: {', '.join(missing)}")
        if unknown:
            details.append(f"unknown fields: {', '.join(unknown)}")
        raise ConfigurationError("; ".join(details))
    if raw["version"] != CONFIG_VERSION:
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
    probe_connect_host, probe_connect_port = parse_probe_connect_address(raw["probeConnectAddress"])
    probe_domain_suffix = normalize_probe_domain_suffix(raw["probeDomainSuffix"])
    probe_timeout_seconds = normalize_probe_timeout(raw["probeTimeoutSeconds"])
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
    if create_state_directory:
        state_dir.mkdir(parents=True, exist_ok=True)
    return AppConfig(
        config_path=config_path,
        background_dir=background_dir,
        allowed_origins=normalized_origins,
        listen_host=host,
        listen_port=port,
        state_dir=state_dir,
        control_token=token,
        probe_connect_host=probe_connect_host,
        probe_connect_port=probe_connect_port,
        probe_domain_suffix=probe_domain_suffix,
        probe_timeout_seconds=probe_timeout_seconds,
    )


def load_config(path: Path) -> AppConfig:
    config_path = path.resolve()
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigurationError(f"configuration file does not exist: {config_path}") from None
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigurationError(f"unable to read configuration: {error}") from error
    return _parse_config_value(config_path, raw, create_state_directory=True)
