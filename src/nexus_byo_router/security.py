from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit


Resolver = Callable[[str, int], Awaitable[list[str]]]


async def default_resolver(host: str, port: int) -> list[str]:
    def _resolve():
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        return sorted({item[4][0] for item in infos})
    return await asyncio.to_thread(_resolve)


def _is_forbidden_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value)
    return any([
        ip.is_private,
        ip.is_loopback,
        ip.is_link_local,
        ip.is_multicast,
        ip.is_reserved,
        ip.is_unspecified,
    ])


async def validate_upstream_base_url(base_url: str, resolver: Resolver = default_resolver) -> str:
    parts = urlsplit(base_url.strip())
    if parts.scheme.lower() != "https":
        raise ValueError("upstream must use https")
    if not parts.hostname:
        raise ValueError("upstream hostname is required")
    if parts.username or parts.password:
        raise ValueError("userinfo is not allowed in upstream URLs")
    host = parts.hostname.lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise ValueError("local upstream hosts are not allowed")
    port = parts.port or 443
    try:
        ip = ipaddress.ip_address(host)
        if _is_forbidden_ip(str(ip)):
            raise ValueError("private or reserved upstream IPs are not allowed")
    except ValueError as exc:
        if "not allowed" in str(exc):
            raise
        addresses = await resolver(host, port)
        if not addresses:
            raise ValueError("upstream hostname did not resolve")
        for address in addresses:
            if _is_forbidden_ip(address):
                raise ValueError("upstream resolved to private or reserved IP space")

    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc, path, "", ""))


def chat_completions_url(base_url: str) -> str:
    trimmed = base_url.rstrip("/")
    if trimmed.endswith("/chat/completions"):
        return trimmed
    if trimmed.endswith("/v1"):
        return trimmed + "/chat/completions"
    return trimmed + "/v1/chat/completions"
