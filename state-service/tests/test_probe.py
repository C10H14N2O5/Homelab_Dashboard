from __future__ import annotations

import socket
import ssl
import threading
import unittest
from unittest import mock

from homelab_state import probe
from tests.helpers import TemporaryProject


# Deterministic, public test-only TLS material for service.home.example.test.
# It protects no real service and exists only to exercise the pinned SNI path.
TEST_CERTIFICATE_DER = "MIIDAzCCAeugAwIBAgIIULMcMQ0qRsIwDQYJKoZIhvcNAQELBQAwJDEiMCAGA1UEAxMZc2VydmljZS5ob21lLmV4YW1wbGUudGVzdDAeFw0yNjA3MjExNTQxNTJaFw0zNjA3MjIxNTQxNTJaMCQxIjAgBgNVBAMTGXNlcnZpY2UuaG9tZS5leGFtcGxlLnRlc3QwggEiMA0GCSqGSIb3DQEBAQUAA4IBDwAwggEKAoIBAQDSaxoBcAH00t96vPpMoOJ0Hya8zTPzWHbXox7lljL7FZ4TzLx0GmsG6LozdQ0Ghy3q175crgQbdSMvq/5mVp4M5Lid3bOj7ZQETQXrFgUjNp/tRmuGeLrLVw7lCNkD3UWTQ9JcZQLaJF1GTtrChW/kJL7FsK2LeZHDHN9p+NhXOCG4l1YKQqR88AtWLmz7Na4I0bkppD6IU9gK/G1usEyELueCbzrMLpV3jW9WBnobSGNp0cC3qpIxDNJiHOUJd2Qd14wt6go33YDoHhGXBuiCxipc9Hc/ckFhr+xMqsINhr5kg+Qq7wgkh+eue++/B21wCl+bsh8hUoSNV5bvXAx9AgMBAAGjOTA3MCQGA1UdEQQdMBuCGXNlcnZpY2UuaG9tZS5leGFtcGxlLnRlc3QwDwYDVR0TAQH/BAUwAwEB/zANBgkqhkiG9w0BAQsFAAOCAQEAJXd+c+xWObYBWDUZgETrapwNzSKHx6cp9mDyKM2jgG3D40i3jqP7PVIpqJUKs/2MrDTOQGsMnjiToVyuQJGq3nM7tVHjdkFqzeiA6zjlA0WSLiltv9WEmqWZIGiDMtkYJVW0uBeWl1vpNach3xQELguWMZiuxTVJ5P911RfkPsVroRPK1kpgDkoVS6MlzRGwOiULg/73usbN5FuaLQdZ511/oqZ2RmMNSNrnCoSBkIi0SqZIXgBRnlUZ+Pr5hUeH4SUThTMz/qdgPe2kBJLBgeJpvkJ8hCGE2vJmLTMqLpZIjf8FTyjSzdf+LRtBc6kEBTHQ32pIieOPLBZ8LXLaLw=="
TEST_PRIVATE_KEY_DER = "MIIEvwIBADANBgkqhkiG9w0BAQEFAASCBKkwggSlAgEAAoIBAQDSaxoBcAH00t96vPpMoOJ0Hya8zTPzWHbXox7lljL7FZ4TzLx0GmsG6LozdQ0Ghy3q175crgQbdSMvq/5mVp4M5Lid3bOj7ZQETQXrFgUjNp/tRmuGeLrLVw7lCNkD3UWTQ9JcZQLaJF1GTtrChW/kJL7FsK2LeZHDHN9p+NhXOCG4l1YKQqR88AtWLmz7Na4I0bkppD6IU9gK/G1usEyELueCbzrMLpV3jW9WBnobSGNp0cC3qpIxDNJiHOUJd2Qd14wt6go33YDoHhGXBuiCxipc9Hc/ckFhr+xMqsINhr5kg+Qq7wgkh+eue++/B21wCl+bsh8hUoSNV5bvXAx9AgMBAAECggEBAKLxKRw/3mP1//UN7k30sCf4D4lY5ofk03K88KodG7Fv1g/DFZRa+NJK2hYK6GFLwPUAyHsAjlGs7eh2qfQZ0x5s58Hn8rpYuaxy8MhRLv1LyaB2TcG+L/hSnEgkB9mEOeKGPJor5n+CdXCCk8jaiAEFq21vGZTOOV/8Nc3cU7uFZkkFcMwLStX4TRJnL7pBkBiaw384N080h7mgdRZnif3szzCt/HCFxUkfT0t/xfC9VcM+6IvkBxkc5VEyH1mGSNQzcFG0EYmZjsrWUYmBO9+DmKrv2O9hbrSuA3VKkQZTypJXvalsNBFprMhyBHtAGevY0NiRVVpYD5iHDwgRxskCgYEA83c/Ja8dOufMvMkyBAe3ffm10301wX/Sopb/nD3i7AZxvDmvFh4mTHBOfz6TWC9LZaQ2hMN1DrwS+EuhC02LvNMUWNNEtRBZlX5F8KSJjl4mIhZIRI1G3Hw7Q5peEfCnWVck19hgHxJcQFG+1K9ib5ucT6B5PYpehdTyCh1UsdsCgYEA3UBOhl1TKP0Qzx3/zJHJqMd5ntInko4mb7RKtUqGc5oQWsjCf5YUr2no+F7NL9JJNWsG5DVwgc6F5pEqMz4WHWr21PBE2TJQk4yarT28tkyeic1DU7MtMIFaL4PPk6XsUFRVWlenWQbh8egmZX+/MiuKuIkt0HiHVIqpU/cPZocCgYEAsbBrVv6KT86Tn4kbxtM2+WtBaBwvGo1G1sFrxsVoE6jUY3KaNYNFom4o7DdoZGkIFyOVYqogieN72NmhrNUo0mQfqoBu2dMX/sJNG0eK2WwfWqt2jQmrGQpWWo2UcHV6Pfsa+amC5cqJp6vBogpqxlgE6hyNv/t35yc2i2lCwhcCgYEAyQrpD6D3stQZlQo+aQXER3BSrCVsPfibJn+eLxP0DlFdMREh40tdqnNDdYS3UA/14+tHzNBeViVyeKtJvFxbPgn3FlDpTq8wlpJ57pedxczjdnYlCoZStrOjXm+YjCKGFAw9rqTddMETSHxZf3tQIed82ugT0E3lMsX4zUo/HTsCgYBv6HjiGFqy47F6i2KnCE8UD2fQ3QvPKct4gYac9lpZ4A7CbyrIxfHYIQMFpLalHJhxZWtIIJSq8P/+/MTnIOcsjo/30+nRu1Zc7Oq2bywBDBau3JgNfJC7JF1G9nu6PbPzORGfh/mcwz/Qemh2UffsjUYpGuF+hbR27Ezq+HSABA=="


def pem(label: str, der_base64: str) -> str:
    lines = [der_base64[index : index + 64] for index in range(0, len(der_base64), 64)]
    return f"-----BEGIN {label}-----\n" + "\n".join(lines) + f"\n-----END {label}-----\n"


TEST_CERTIFICATE_PEM = pem("CERTIFICATE", TEST_CERTIFICATE_DER)
TEST_PRIVATE_KEY_PEM = pem("PRIVATE KEY", TEST_PRIVATE_KEY_DER)


class FakeResponse:
    def __init__(self, status: int, location: str | None = None) -> None:
        self.status = status
        self.location = location
        self.closed = False

    def getheader(self, name: str) -> str | None:
        return self.location if name == "Location" else None

    def close(self) -> None:
        self.closed = True


class ProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = TemporaryProject()
        self.config = self.project.write_config()
        self.clients: list[probe.ProbeClient] = []

    def tearDown(self) -> None:
        for client in self.clients:
            client.close()
        self.project.close()

    def make_client(self, connection_factory) -> probe.ProbeClient:
        client = probe.ProbeClient(self.config, connection_factory=connection_factory)
        self.clients.append(client)
        return client

    def test_target_validation_rejects_unsafe_shapes(self) -> None:
        valid = (
            "https://service.home.example.test/",
            "https://service.home.example.test:443/path?token=not-logged",
        )
        invalid = (
            "http://service.home.example.test/",
            "https://service.home.example.test:444/",
            "https://user:pass@service.home.example.test/",
            "https://service.home.example.test/#fragment",
            "https://service.home.example.test/#",
            "https://127.0.0.1/",
            "https://home.example.test/",
            "https://deep.service.home.example.test/",
            "https://service.example.test/",
        )
        for target in valid:
            with self.subTest(target=target):
                self.assertIsNotNone(probe.validate_probe_target(target, self.config.probe_domain_suffix))
        for target in invalid:
            with self.subTest(target=target):
                self.assertIsNone(probe.validate_probe_target(target, self.config.probe_domain_suffix))

    def test_pinned_connection_uses_loopback_and_target_sni(self) -> None:
        raw_socket = mock.Mock()
        wrapped_socket = mock.Mock()
        context = mock.Mock()
        context.wrap_socket.return_value = wrapped_socket
        connection = probe.PinnedHTTPSConnection(
            "service.home.example.test",
            "127.0.0.1",
            443,
            2.0,
            context,
        )
        with mock.patch.object(probe.socket, "create_connection", return_value=raw_socket) as create_connection:
            connection.connect()
        create_connection.assert_called_once_with(("127.0.0.1", 443), 2.0, None)
        context.wrap_socket.assert_called_once_with(raw_socket, server_hostname="service.home.example.test")
        self.assertIs(connection.sock, wrapped_socket)

    def test_real_tls_probe_connects_to_loopback_with_target_host_and_sni(self) -> None:
        certificate = self.project.root / "test-certificate.pem"
        private_key = self.project.root / "test-private-key.pem"
        certificate.write_text(TEST_CERTIFICATE_PEM, encoding="ascii")
        private_key.write_text(TEST_PRIVATE_KEY_PEM, encoding="ascii")
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        observed: dict[str, str] = {}
        errors: list[Exception] = []
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.load_cert_chain(certificate, private_key)

        def record_sni(_socket, server_name, _context) -> None:
            observed["sni"] = server_name

        server_context.set_servername_callback(record_sni)

        def serve_once() -> None:
            try:
                connection, _address = listener.accept()
                with server_context.wrap_socket(connection, server_side=True) as secured:
                    request = b""
                    while b"\r\n\r\n" not in request:
                        request += secured.recv(4096)
                    observed["request"] = request.decode("ascii")
                    secured.sendall(b"HTTP/1.1 521 Web Server Is Down\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=serve_once, daemon=True)
        thread.start()
        config = self.project.write_config(probeConnectAddress=f"127.0.0.1:{port}")
        client_context = ssl.create_default_context(cadata=TEST_CERTIFICATE_PEM)
        client = probe.ProbeClient(config, ssl_context=client_context)
        self.clients.append(client)
        try:
            result = client.probe_one("https://service.home.example.test/health")
        finally:
            listener.close()
            thread.join(timeout=2)

        self.assertEqual(errors, [])
        self.assertEqual(result.status, 521)
        self.assertIsNone(result.error)
        self.assertEqual(observed["sni"], "service.home.example.test")
        self.assertIn("GET /health HTTP/1.1\r\n", observed["request"])
        self.assertIn("Host: service.home.example.test\r\n", observed["request"])

    def test_probe_preserves_host_and_returns_http_status(self) -> None:
        observed: dict[str, object] = {}
        response = FakeResponse(521)

        class Connection:
            def __init__(self, target_host, connect_host, connect_port, timeout, context) -> None:
                observed["constructor"] = (target_host, connect_host, connect_port, timeout)

            def request(self, method, path, headers) -> None:
                observed["request"] = (method, path, headers)

            def getresponse(self):
                return response

            def close(self) -> None:
                observed["closed"] = True

        client = self.make_client(Connection)
        result = client.probe_one("https://service.home.example.test/path?safe=1")
        self.assertEqual(result.status, 521)
        self.assertIsNone(result.error)
        self.assertEqual(observed["constructor"][:3], ("service.home.example.test", "127.0.0.1", 443))
        method, path, headers = observed["request"]
        self.assertEqual((method, path), ("GET", "/path?safe=1"))
        self.assertEqual(headers["Host"], "service.home.example.test")
        self.assertTrue(response.closed)
        self.assertTrue(observed["closed"])

    def test_redirect_rejection_preserves_last_status(self) -> None:
        response = FakeResponse(302, "https://outside.example.test/")
        connection = mock.Mock()
        connection.getresponse.return_value = response
        client = self.make_client(lambda *_args: connection)
        result = client.probe_one("https://service.home.example.test/")
        self.assertEqual(result.status, 302)
        self.assertEqual(result.error, "redirect_rejected")

    def test_probe_error_codes_are_stable(self) -> None:
        cases = (
            (socket.timeout(), "timeout"),
            (ssl.SSLError("tls"), "tls_error"),
            (ConnectionRefusedError(), "connection_error"),
            (probe.http.client.BadStatusLine("bad"), "request_error"),
        )
        for exception, expected in cases:
            with self.subTest(expected=expected):
                connection = mock.Mock()
                connection.request.side_effect = exception
                client = self.make_client(lambda *_args, connection=connection: connection)
                result = client.probe_one("https://service.home.example.test/")
                self.assertIsNone(result.status)
                self.assertEqual(result.error, expected)

    def test_batch_preserves_input_order_and_uses_shared_worker_limit(self) -> None:
        client = self.make_client(mock.Mock())
        self.assertEqual(client.executor._max_workers, 8)
        targets = [f"https://service{index}.home.example.test/" for index in range(12)]

        def delayed_probe(target: str, _started: float, _deadline: float) -> probe.ProbeResult:
            index = targets.index(target)
            threading.Event().wait((len(targets) - index) / 1000)
            return probe.ProbeResult(target, 200 + index, index, None)

        with mock.patch.object(client, "probe_one", side_effect=delayed_probe):
            results = client.probe_many(targets)
        self.assertEqual([result.target for result in results], targets)
        self.assertEqual([result.status for result in results], list(range(200, 212)))

    def test_queued_probe_honors_shared_batch_deadline(self) -> None:
        factory = mock.Mock()
        client = self.make_client(factory)
        with mock.patch.object(client, "clock", return_value=10.0):
            result = client.probe_one("https://service.home.example.test/", started=0.0, deadline=5.0)
        self.assertEqual(result.error, "timeout")
        factory.assert_not_called()
