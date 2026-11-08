"""Public-HTTPS-only outbound transport, pinned to the audited HTTP stack."""

import ipaddress
import socket
import ssl

import anyio
import httpcore
import httpx
from httpcore._backends.anyio import AnyIOBackend
from httpcore._backends.base import AsyncNetworkBackend, AsyncNetworkStream
from httpx._config import create_ssl_context


_V4_DENY = tuple(ipaddress.ip_network(net) for net in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
    "168.63.129.16/32", "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24",
    "192.88.99.0/24", "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24",
    "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4",
))
_V6_GLOBAL = ipaddress.ip_network("2000::/3")
_V6_DENY = tuple(ipaddress.ip_network(net) for net in (
    "2001::/23", "2001:db8::/32", "2002::/16", "3fff::/20",
))


def _public_ip(value):
    try:
        if "%" in value:
            raise ValueError
        address = ipaddress.ip_address(value)
    except ValueError:
        raise httpcore.ConnectError("Outbound address is not permitted") from None
    if isinstance(address, ipaddress.IPv4Address):
        allowed = address.is_global and not any(address in net for net in _V4_DENY)
    else:
        # Only native global unicast: exclude mapped, NAT64, 6to4, Teredo,
        # local-use and special-purpose IPv6, including on Python 3.9.
        allowed = (address.is_global and address in _V6_GLOBAL
                   and not any(address in net for net in _V6_DENY))
    if not allowed:
        raise httpcore.ConnectError("Outbound address is not permitted")
    return address


class _CheckedStream(AsyncNetworkStream):
    def __init__(self, stream, address, hostname, tls=False):
        self._stream = stream
        self._address = address
        self._hostname = hostname
        self._tls = tls

    async def _check_peer(self):
        peer = self._stream.get_extra_info("server_addr")
        try:
            valid = (isinstance(peer, tuple) and len(peer) >= 2
                     and _public_ip(peer[0]) == self._address and peer[1] == 443)
        except (httpcore.ConnectError, TypeError, ValueError):
            valid = False
        if not valid:
            await self.aclose()
            raise httpcore.ConnectError("Outbound peer verification failed")

    async def read(self, max_bytes, timeout=None):
        return await self._stream.read(max_bytes, timeout)

    async def write(self, buffer, timeout=None):
        await self._check_peer()
        if not self._tls:
            await self.aclose()
            raise httpcore.ConnectError("Outbound TLS is required")
        await self._stream.write(buffer, timeout)

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        await self._check_peer()
        if (server_hostname != self._hostname or not ssl_context.check_hostname
                or ssl_context.verify_mode != ssl.CERT_REQUIRED):
            await self.aclose()
            raise httpcore.ConnectError("Outbound TLS verification is required")
        stream = await self._stream.start_tls(
            ssl_context, server_hostname=server_hostname, timeout=timeout,
        )
        checked = _CheckedStream(stream, self._address, self._hostname, tls=True)
        await checked._check_peer()
        return checked

    async def aclose(self):
        await self._stream.aclose()

    def get_extra_info(self, info):
        return self._stream.get_extra_info(info)


class _PublicNetworkBackend(AsyncNetworkBackend):
    def __init__(self):
        self._backend = AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None,
                          socket_options=None):
        if port != 443 or local_address is not None:
            raise httpcore.ConnectError("Only public HTTPS on port 443 is permitted")
        try:
            with anyio.fail_after(timeout):
                try:
                    literal = ipaddress.ip_address(host)
                except ValueError:
                    literal = None
                if literal is not None:
                    addresses = [_public_ip(host)]
                else:
                    answers = await anyio.getaddrinfo(
                        host, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM,
                        proto=socket.IPPROTO_TCP,
                    )
                    # Validate the entire answer set before any connection attempt.
                    addresses = list(dict.fromkeys(_public_ip(row[4][0]) for row in answers))
                if not addresses:
                    raise httpcore.ConnectError("Outbound DNS returned no addresses")
                for address in addresses:
                    try:
                        stream = await self._backend.connect_tcp(
                            host=str(address), port=443, timeout=timeout,
                            local_address=None, socket_options=socket_options,
                        )
                    except httpcore.ConnectError:
                        continue
                    checked = _CheckedStream(stream, address, host)
                    await checked._check_peer()
                    return checked
                raise httpcore.ConnectError("Outbound connection failed")
        except TimeoutError:
            raise httpcore.ConnectTimeout("Outbound connection timed out") from None
        except OSError:
            raise httpcore.ConnectError("Outbound DNS lookup failed") from None

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise httpcore.ConnectError("Unix sockets are not permitted")

    async def sleep(self, seconds):
        await anyio.sleep(seconds)


class PublicAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """No proxies, alternate sockets, insecure TLS, or hostname redialing."""

    def __init__(self, *, limits=None):
        limits = limits if limits is not None else httpx.Limits()
        # AsyncHTTPTransport's request/stream adaptation is reused; its pool is
        # constructed here because httpx does not expose network_backend.
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=create_ssl_context(verify=True, trust_env=False),
            max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry,
            network_backend=_PublicNetworkBackend(),
        )

    async def handle_async_request(self, request):
        url = request.url
        if (url.scheme != "https" or url.port not in (None, 443) or not url.host
                or url.userinfo or url.query or url.fragment
                or "\\" in str(url) or "%" in url.host
                or any(char.isspace() for char in str(url))):
            raise httpx.UnsupportedProtocol("Only public HTTPS on port 443 is permitted")
        expected_hosts = {url.netloc.decode("ascii"), url.netloc.decode("ascii") + ":443"}
        if request.headers.get("host") not in expected_hosts:
            raise httpx.LocalProtocolError("Outbound Host must match URL")
        sni = request.extensions.get("sni_hostname")
        if sni is not None and sni != url.raw_host.decode("ascii"):
            raise httpx.LocalProtocolError("Outbound SNI must match URL")
        return await super().handle_async_request(request)


def async_client(**kwargs):
    """Create a safe client; transport injection is for trusted Python tests only."""
    for option in ("proxy", "mounts", "cert"):
        if kwargs.get(option) is not None:
            raise ValueError("Outbound client does not permit " + option)
        kwargs.pop(option, None)
    if kwargs.pop("verify", True) is not True:
        raise ValueError("Outbound certificate verification cannot be overridden")
    kwargs["trust_env"] = False
    kwargs["follow_redirects"] = False
    if kwargs.get("transport") is None:
        kwargs["transport"] = PublicAsyncHTTPTransport(limits=kwargs.get("limits"))
    return httpx.AsyncClient(**kwargs)
