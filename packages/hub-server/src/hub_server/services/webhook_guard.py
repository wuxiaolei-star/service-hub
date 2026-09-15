"""Outbound webhook target validation: the SSRF guard for callback delivery.

Callback URLs are operator-supplied and the scheduler posts to them from inside the
deployment, where the Hub API and the service manager listen on loopback and the
container can reach its own gateway and cloud metadata endpoints. Two layers guard
that boundary:

* :func:`normalize_webhook_url` runs at registration and is deterministic (no DNS):
  it pins the scheme, rejects embedded credentials, applies the optional host
  allow-list, and refuses address literals outside the global unicast space,
  including the integer/hex/abbreviated IPv4 spellings that ``inet_aton`` accepts.
* :func:`ensure_delivery_target_allowed` runs immediately before every POST and
  re-resolves the hostname, so rows written before the guard existed and records
  whose DNS answer changed after registration are still refused.

The residual risk is the window between that resolution and the socket connect the
HTTP client performs; closing it fully requires pinning the connection to the
validated address, which ``urllib`` cannot express without breaking TLS
verification. Deployments that need a callback on a private network must opt in
explicitly with ``webhooks.allow_private_networks``; ``webhooks.allowed_hosts``
narrows the set of hosts on top of the address guard, it does not widen it.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from hub_server.errors import WebhookUrlForbiddenError
from hub_server.settings import WebhooksSettings

ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})
LOCAL_HOST_NAMES: frozenset[str] = frozenset(
    {"localhost", "metadata", "metadata.google.internal", "instance-data"}
)
LOCAL_HOST_SUFFIXES: tuple[str, ...] = (".localhost",)

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


def normalize_webhook_url(url: str, policy: WebhooksSettings | None = None) -> str:
    """Return ``url`` stripped, or raise when it targets a forbidden destination.

    Scheme, embedded credentials, and the host allow-list always apply. The address
    guard is skipped only when the deployment opted into
    ``webhooks.allow_private_networks``, which is what lets a callback reach a
    service on the local network.

    Raises:
        WebhookUrlForbiddenError: scheme, credentials, allow-list, or host shape
            makes the URL unsafe to call back from inside the deployment.
    """
    active = policy or WebhooksSettings()
    candidate = url.strip()
    try:
        parts = urlsplit(candidate)
        port = parts.port  # reading it forces urlsplit's lazy port validation
    except ValueError as error:  # malformed IPv6 literal, non-numeric or out-of-range port
        raise WebhookUrlForbiddenError(reason="地址无法解析") from error
    if port is not None and not 1 <= port <= 65535:
        raise WebhookUrlForbiddenError(reason="端口不合法")
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise WebhookUrlForbiddenError(reason="只支持 http 与 https")
    if parts.username or parts.password:
        raise WebhookUrlForbiddenError(reason="不得内嵌用户名或口令")
    # A fully-qualified name may carry a root dot ("localhost."), which must not
    # slip past the local-name and allow-list comparisons.
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise WebhookUrlForbiddenError(reason="缺少主机名")
    _ensure_host_allowlisted(host, active)
    if not active.allow_private_networks:
        _ensure_host_is_public(host)
    return candidate


def ensure_delivery_target_allowed(url: str, policy: WebhooksSettings | None = None) -> None:
    """Re-validate a stored callback URL, resolving its host just before delivery.

    Raises:
        WebhookUrlForbiddenError: the URL broke the registration rules, or its
            hostname currently resolves to a non-global address.
    """
    active = policy or WebhooksSettings()
    normalize_webhook_url(url, active)
    if active.allow_private_networks:
        return
    host = urlsplit(url).hostname
    if host is None:  # pragma: no cover - normalize_webhook_url already rejected this
        raise WebhookUrlForbiddenError(reason="缺少主机名")
    for address in _resolve(host):
        _ensure_address_is_public(address)


def _ensure_host_allowlisted(host: str, policy: WebhooksSettings) -> None:
    """Apply the optional host allow-list; an empty list allows every host."""
    if not policy.allowed_hosts:
        return
    if any(_host_matches(host, pattern) for pattern in policy.allowed_hosts):
        return
    raise WebhookUrlForbiddenError(reason="主机不在 webhooks.allowed_hosts 白名单内")


def _host_matches(host: str, pattern: str) -> bool:
    """Match one host against an exact name or a ``*.`` wildcard pattern."""
    if pattern.startswith("*."):
        suffix = pattern[1:]
        return host.endswith(suffix) and len(host) > len(suffix)
    return host == pattern


def _ensure_host_is_public(host: str) -> None:
    """Reject address literals and well-known local names without touching DNS."""
    address = _parse_host_address(host)
    if address is not None:
        _ensure_address_is_public(address)
        return
    if host in LOCAL_HOST_NAMES or host.endswith(LOCAL_HOST_SUFFIXES):
        raise WebhookUrlForbiddenError(reason="不得指向本机或云元数据服务")


def _ensure_address_is_public(address: IPAddress) -> None:
    """Reject every address outside the global unicast space, mapped IPv4 included."""
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if not address.is_global:
        raise WebhookUrlForbiddenError(reason=f"{address} 不是公网地址")


def _parse_host_address(host: str) -> IPAddress | None:
    """Parse ``host`` as an address literal, including relaxed IPv4 spellings.

    ``inet_aton`` accepts the forms every libc resolver also accepts
    (``127.1``, ``2130706433``, ``0x7f000001``), so treating them as host names
    would hand the guard a name that resolves to loopback.
    """
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    if ":" in host:
        return None
    try:
        packed = socket.inet_aton(host)
    except OSError:
        return None
    return ipaddress.IPv4Address(packed)


def _resolve(host: str) -> list[IPAddress]:
    """Resolve ``host`` to every address it currently answers with."""
    literal = _parse_host_address(host)
    if literal is not None:
        return [literal]
    try:
        info = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        # Unresolvable hosts cannot reach anything; the POST reports the DNS error.
        return []
    resolved: list[IPAddress] = []
    for entry in info:
        try:
            resolved.append(ipaddress.ip_address(entry[4][0]))
        except ValueError:  # pragma: no cover - getaddrinfo yields literal addresses
            continue
    return resolved
