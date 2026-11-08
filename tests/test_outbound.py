import asyncio
import ipaddress
import socket
import ssl

import anyio
import httpcore
import httpx
import pytest

from safeorbit import outbound


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected real network access")
    monkeypatch.setattr(anyio, "getaddrinfo", forbidden)
    monkeypatch.setattr(anyio, "connect_tcp", forbidden)


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("address", [
    "0.0.0.0", "10.1.2.3", "127.0.0.1", "169.254.169.254", "172.16.0.1",
    "192.168.1.1", "168.63.129.16", "100.100.100.200", "100.64.0.1", "192.0.0.9", "192.0.2.1",
    "192.88.99.1", "198.18.0.1", "198.51.100.1", "203.0.113.1", "224.0.0.1",
    "240.0.0.1", "255.255.255.255", "::", "::1", "fc00::1", "fe80::1",
    "ff02::1", "::ffff:8.8.8.8", "::ffff:127.0.0.1", "64:ff9b::808:808",
    "64:ff9b:1::1", "2002:0808:0808::1", "2001::1", "2001:db8::1",
    "2001:20::1", "3fff::1", "2606:4700::1%1", "not-an-ip",
])
def test_denied_addresses(address):
    with pytest.raises(httpcore.ConnectError):
        outbound._public_ip(address)


@pytest.mark.parametrize("address", ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"])
def test_public_addresses(address):
    assert str(outbound._public_ip(address)) == address


class Wire:
    def __init__(self, peer):
        self.peer = peer
        self.closed = False
        self.writes = []
        self.tls_calls = []
        self.response = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK"

    async def receive(self, max_bytes):
        response, self.response = self.response, b""
        return response

    async def send(self, item):
        self.writes.append(item)

    async def aclose(self):
        self.closed = True

    def extra(self, attribute, default=None):
        if attribute == anyio.abc.SocketAttribute.remote_address:
            return self.peer
        return default


def setup_wire(monkeypatch, answers, peer=None):
    calls = []
    dns = []
    wire = Wire(peer if peer is not None else (answers[0], 443))

    async def resolve(host, port, **kwargs):
        dns.append(host)
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET,
                 socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port)) for ip in answers]

    async def connect(remote_host, remote_port, **kwargs):
        # This is below the real httpcore AnyIOBackend, not a MockTransport.
        ipaddress.ip_address(remote_host)
        calls.append((remote_host, remote_port))
        return wire

    async def tls_wrap(stream, *, ssl_context, hostname, **kwargs):
        assert stream is wire
        wire.tls_calls.append((hostname, ssl_context.check_hostname, ssl_context.verify_mode))
        return stream

    monkeypatch.setattr(anyio, "getaddrinfo", resolve)
    monkeypatch.setattr(anyio, "connect_tcp", connect)
    monkeypatch.setattr(anyio.streams.tls.TLSStream, "wrap", tls_wrap)
    return wire, calls, dns


@pytest.mark.parametrize("address", ["8.8.8.8", "2606:4700:4700::1111"])
def test_real_pool_numeric_dial_sni_host(monkeypatch, address):
    wire, calls, dns = setup_wire(monkeypatch, [address])

    async def request():
        async with outbound.async_client() as client:
            response = await client.post("https://api.example/v1/chat/completions", content=b"test")
            assert response.text == "OK"

    run(request())
    assert dns == ["api.example"]
    assert calls == [(address, 443)]
    assert wire.tls_calls == [("api.example", True, ssl.CERT_REQUIRED)]
    assert b"Host: api.example\r\n" in b"".join(wire.writes)
    assert wire.closed


@pytest.mark.parametrize("answers", [["8.8.8.8", "127.0.0.1"], ["::1", "2606:4700::1111"], []])
def test_entire_dns_answer_rejected_before_dial(monkeypatch, answers):
    wire, calls, _ = setup_wire(monkeypatch, answers, peer=("8.8.8.8", 443))

    async def request():
        async with outbound.async_client() as client:
            with pytest.raises(httpx.ConnectError):
                await client.get("https://api.example/v1")

    run(request())
    assert calls == []
    assert wire.tls_calls == []
    assert wire.writes == []


@pytest.mark.parametrize("peer", [("1.1.1.1", 443), ("127.0.0.1", 443), ("8.8.8.8", 80), None])
def test_peer_mismatch_fails_before_tls_and_http(monkeypatch, peer):
    wire, calls, _ = setup_wire(monkeypatch, ["8.8.8.8"])
    wire.peer = peer

    async def request():
        async with outbound.async_client() as client:
            with pytest.raises(httpx.ConnectError):
                await client.get("https://api.example/v1")

    run(request())
    assert calls == [("8.8.8.8", 443)]
    assert wire.closed and not wire.tls_calls and not wire.writes


def test_new_connection_revalidates_dns_rebinding(monkeypatch):
    wire, calls, dns = setup_wire(monkeypatch, ["8.8.8.8"])

    async def request():
        async with outbound.async_client() as client:
            await client.get("https://api.example/v1")
            async def rebound(*args, **kwargs):
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
            monkeypatch.setattr(anyio, "getaddrinfo", rebound)
            with pytest.raises(httpx.ConnectError):
                await client.get("https://api.example/v1")

    run(request())
    assert calls == [("8.8.8.8", 443)]


@pytest.mark.parametrize("url", [
    "http://api.example/v1", "https://api.example:8443/v1", "https://u:p@api.example/v1",
    "https://api.example/v1?key=secret", "https://api.example/v1#frag",
])
def test_invalid_requests_never_resolve(url):
    async def request():
        async with outbound.async_client() as client:
            with pytest.raises(httpx.UnsupportedProtocol):
                await client.get(url)
    run(request())


@pytest.mark.parametrize("kwargs", [{"verify": False}, {"proxy": "http://127.0.0.1"},
                                   {"mounts": {}}, {"cert": "cert.pem"}])
def test_client_refuses_unsafe_options(kwargs):
    with pytest.raises(ValueError):
        outbound.async_client(**kwargs)


def test_environment_redirects_and_explicit_mock(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    wire, calls, _ = setup_wire(monkeypatch, ["8.8.8.8"])
    wire.response = (b"HTTP/1.1 302 Found\r\nLocation: https://127.0.0.1/\r\n"
                     b"Content-Length: 0\r\nConnection: close\r\n\r\n")

    async def request():
        async with outbound.async_client(trust_env=True, follow_redirects=True) as client:
            assert client.trust_env is False
            assert client.follow_redirects is False
            assert (await client.get("https://api.example/")).status_code == 302
        mock = httpx.MockTransport(lambda request: httpx.Response(200, text="mock"))
        async with outbound.async_client(transport=mock) as client:
            assert (await client.get("http://test/")).text == "mock"

    run(request())
    assert calls == [("8.8.8.8", 443)]


def test_host_and_sni_override_rejected():
    async def request():
        async with outbound.async_client() as client:
            with pytest.raises(httpx.LocalProtocolError):
                await client.get("https://api.example/", headers={"Host": "other.example"})
            with pytest.raises(httpx.LocalProtocolError):
                await client.get("https://api.example/", extensions={"sni_hostname": "other.example"})
    run(request())


def test_certificate_environment_is_ignored(monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", "missing-untrusted-cert.pem")
    monkeypatch.setenv("SSL_CERT_DIR", "missing-untrusted-directory")
    transport = outbound.PublicAsyncHTTPTransport()
    assert transport._pool._ssl_context.check_hostname
    assert transport._pool._ssl_context.verify_mode == ssl.CERT_REQUIRED
    run(transport.aclose())


def test_literal_public_ip_does_not_resolve(monkeypatch):
    wire, calls, dns = setup_wire(monkeypatch, ["8.8.8.8"])
    async def request():
        async with outbound.async_client() as client:
            assert (await client.get("https://8.8.8.8/")).status_code == 200
    run(request())
    assert dns == []
    assert calls == [("8.8.8.8", 443)]
    assert wire.tls_calls[0][0] == "8.8.8.8"


def test_dns_timeout_is_connect_timeout(monkeypatch):
    async def slow_dns(*args, **kwargs):
        await anyio.sleep_forever()
    monkeypatch.setattr(anyio, "getaddrinfo", slow_dns)
    async def request():
        async with outbound.async_client(timeout=0.01) as client:
            with pytest.raises(httpx.ConnectTimeout):
                await client.get("https://api.example/")
    run(request())


def test_public_ip_fallback_never_redials_hostname(monkeypatch):
    wire, calls, dns = setup_wire(monkeypatch, ["8.8.8.8", "1.1.1.1"], peer=("1.1.1.1", 443))
    connect = anyio.connect_tcp
    async def fail_first(remote_host, remote_port, **kwargs):
        if remote_host == "8.8.8.8":
            calls.append((remote_host, remote_port))
            raise OSError("unreachable")
        return await connect(remote_host, remote_port, **kwargs)
    monkeypatch.setattr(anyio, "connect_tcp", fail_first)
    async def request():
        async with outbound.async_client() as client:
            assert (await client.get("https://api.example/")).status_code == 200
    run(request())
    assert dns == ["api.example"]
    assert calls == [("8.8.8.8", 443), ("1.1.1.1", 443)]


@pytest.mark.parametrize("mode", ["hostname", "verify"])
def test_stream_refuses_disabled_tls_verification(monkeypatch, mode):
    wire, calls, dns = setup_wire(monkeypatch, ["8.8.8.8"])
    async def request():
        backend = outbound._PublicNetworkBackend()
        stream = await backend.connect_tcp("api.example", 443)
        context = ssl.create_default_context()
        context.check_hostname = False
        if mode == "verify":
            context.verify_mode = ssl.CERT_NONE
        with pytest.raises(httpcore.ConnectError):
            await stream.start_tls(context, "api.example")
    run(request())
    assert wire.closed and not wire.tls_calls and not wire.writes


def test_peer_rechecked_after_tls_before_http(monkeypatch):
    wire, calls, _ = setup_wire(monkeypatch, ["8.8.8.8"])

    async def changed_tls(stream, **kwargs):
        wire.peer = ("127.0.0.1", 443)
        return stream
    monkeypatch.setattr(anyio.streams.tls.TLSStream, "wrap", changed_tls)

    async def request():
        async with outbound.async_client() as client:
            with pytest.raises(httpx.ConnectError):
                await client.get("https://api.example/")
    run(request())
    assert wire.closed and not wire.writes
