from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import http.client
import ipaddress
import socket
import ssl
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

from homelab_state.config import AppConfig, DOMAIN_LABEL_PATTERN
from homelab_state.core import SERVICE_NAME, SERVICE_VERSION


MAX_PROBE_TARGETS = 32
MAX_PROBE_WORKERS = 8
MAX_PROBE_REDIRECTS = 5


@dataclass(frozen=True)
class ProbeResult:
    target: str
    status: int | None
    latency_ms: int
    error: str | None

    def as_json(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "status": self.status,
            "latencyMs": self.latency_ms,
            "error": self.error,
        }


def validate_probe_target(target: str, domain_suffix: str):
    try:
        parsed = urlsplit(target)
        port = parsed.port
        hostname = parsed.hostname.lower() if parsed.hostname else ""
    except (AttributeError, ValueError):
        return None
    if (
        "#" in target
        or parsed.scheme.lower() != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port not in {None, 443}
    ):
        return None
    try:
        ipaddress.ip_address(hostname)
        return None
    except ValueError:
        pass
    suffix = f".{domain_suffix}"
    if not hostname.endswith(suffix):
        return None
    label = hostname[: -len(suffix)]
    if not DOMAIN_LABEL_PATTERN.fullmatch(label):
        return None
    return parsed


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(
        self,
        target_host: str,
        connect_host: str,
        connect_port: int,
        timeout: float,
        context: ssl.SSLContext,
    ) -> None:
        super().__init__(target_host, port=443, timeout=timeout, context=context)
        self._connect_host = connect_host
        self._connect_port = connect_port

    def connect(self) -> None:
        raw_socket = socket.create_connection(
            (self._connect_host, self._connect_port),
            self.timeout,
            self.source_address,
        )
        try:
            self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)
        except Exception:
            raw_socket.close()
            raise


class ProbeClient:
    def __init__(
        self,
        config: AppConfig,
        *,
        ssl_context: ssl.SSLContext | None = None,
        connection_factory=None,
        clock=time.monotonic,
    ) -> None:
        self.connect_host = config.probe_connect_host
        self.connect_port = config.probe_connect_port
        self.domain_suffix = config.probe_domain_suffix
        self.timeout_seconds = config.probe_timeout_seconds
        self.ssl_context = ssl_context or ssl.create_default_context()
        self.connection_factory = connection_factory or PinnedHTTPSConnection
        self.clock = clock
        self.executor = ThreadPoolExecutor(max_workers=MAX_PROBE_WORKERS, thread_name_prefix="service-probe")

    def close(self) -> None:
        self.executor.shutdown(wait=True, cancel_futures=True)

    def _result(self, target: str, started: float, status: int | None, error: str | None) -> ProbeResult:
        latency_ms = max(0, round((self.clock() - started) * 1000))
        return ProbeResult(target, status, latency_ms, error)

    def probe_one(
        self,
        target: str,
        started: float | None = None,
        deadline: float | None = None,
    ) -> ProbeResult:
        started = self.clock() if started is None else started
        deadline = started + self.timeout_seconds if deadline is None else deadline
        current_target = target
        last_status: int | None = None
        for redirect_count in range(MAX_PROBE_REDIRECTS + 1):
            parsed = validate_probe_target(current_target, self.domain_suffix)
            if parsed is None:
                error = "invalid_target" if redirect_count == 0 else "redirect_rejected"
                return self._result(target, started, last_status, error)
            remaining = deadline - self.clock()
            if remaining <= 0:
                return self._result(target, started, last_status, "timeout")
            connection = self.connection_factory(
                parsed.hostname,
                self.connect_host,
                self.connect_port,
                remaining,
                self.ssl_context,
            )
            try:
                request_path = parsed.path or "/"
                if parsed.query:
                    request_path = f"{request_path}?{parsed.query}"
                connection.request(
                    "GET",
                    request_path,
                    headers={
                        "Host": parsed.netloc,
                        "Accept": "*/*",
                        "User-Agent": f"{SERVICE_NAME}/{SERVICE_VERSION}",
                        "Connection": "close",
                    },
                )
                response = connection.getresponse()
                last_status = response.status
                location = response.getheader("Location")
                response.close()
            except (socket.timeout, TimeoutError):
                return self._result(target, started, last_status, "timeout")
            except (ssl.SSLCertVerificationError, ssl.SSLError):
                return self._result(target, started, last_status, "tls_error")
            except http.client.HTTPException:
                return self._result(target, started, last_status, "request_error")
            except OSError:
                return self._result(target, started, last_status, "connection_error")
            except Exception:
                return self._result(target, started, last_status, "request_error")
            finally:
                connection.close()

            if last_status in {301, 302, 303, 307, 308} and location:
                if redirect_count >= MAX_PROBE_REDIRECTS:
                    return self._result(target, started, last_status, "redirect_rejected")
                next_target = urljoin(current_target, location)
                if validate_probe_target(next_target, self.domain_suffix) is None:
                    return self._result(target, started, last_status, "redirect_rejected")
                current_target = next_target
                continue
            return self._result(target, started, last_status, None)
        return self._result(target, started, last_status, "redirect_rejected")

    def probe_many(self, targets: list[str]) -> list[ProbeResult]:
        started = self.clock()
        deadline = started + self.timeout_seconds
        results: list[ProbeResult | None] = [None] * len(targets)
        futures = {
            self.executor.submit(self.probe_one, target, started, deadline): index
            for index, target in enumerate(targets)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception:
                results[index] = ProbeResult(targets[index], None, 0, "request_error")
        return [result for result in results if result is not None]
