"""Authentication dependencies enforcing the public API role matrix."""

from __future__ import annotations

import ipaddress
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from hub_server.dependencies import get_session, get_settings
from hub_server.errors import HubError
from hub_server.services.auth import AuthService
from hub_server.settings import HubSettings

SESSION_COOKIE_NAME = "hub_session"
API_KEY_PREFIX = "hub_"
# IPv4, IPv6, and host:port peer strings all fit well inside the audit column.
_MAX_ADDRESS_LENGTH = 64

# Socket peers whose ``X-Forwarded-For`` may be honoured: the RFC1918 ranges
# mirror ``set_real_ip_from`` in web/nginx.conf (the console edge and everything
# that can reach it), plus the loopback ranges a same-host proxy or local client
# connects from. Any other peer connects directly and could set the header to
# anything, so trusting it would let one client rotate fake addresses past the
# per-address login throttling and forge audit attribution (audit M-6).
TRUSTED_PROXY_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
)

ROLE_ORDER: dict[str, int] = {
    "viewer": 0,
    "operator": 1,
    "publisher": 2,
    "admin": 3,
}


@dataclass(frozen=True)
class Actor:
    """The authenticated identity behind one request."""

    kind: str  # "user" | "api_key" | "anonymous"
    id: int | None
    name: str
    role: str


def _auth_required(message: str = "请先登录") -> HubError:
    return HubError(code="AUTH_REQUIRED", message=message, status_code=401)


def _extract_credential(request: Request) -> str | None:
    # An explicit Authorization header wins over the browser session cookie so
    # machine clients never get shadowed by an interactive login.
    authorization = request.headers.get("authorization")
    if authorization and authorization.startswith("Bearer "):
        return authorization.removeprefix("Bearer ").strip()
    return request.cookies.get(SESSION_COOKIE_NAME)


def resolve_actor(
    request: Request,
    session: Session,
    settings: HubSettings,
) -> Actor:
    """Resolve the request credential to an actor, or raise AUTH_REQUIRED.

    A request typically triggers resolution twice (router-level ``require_role``
    plus an endpoint ``get_actor``); the result is cached on ``request.state``
    so the credential is looked up exactly once. Failures are never cached.
    """
    cached = getattr(request.state, "actor", None)
    if isinstance(cached, Actor):
        return cached

    if settings.auth.mode == "off":
        actor = Actor(kind="anonymous", id=None, name="anonymous", role="admin")
        request.state.actor = actor
        return actor

    credential = _extract_credential(request)
    if credential is None:
        raise _auth_required()

    service = AuthService(session)
    if credential.startswith(API_KEY_PREFIX):
        key = service.resolve_api_key(credential)
        if key is None:
            raise _auth_required("凭证无效或已吊销")
        actor = Actor(kind="api_key", id=key.id, name=key.name, role=key.role)
    else:
        record = service.resolve_session(credential)
        if record is None:
            raise _auth_required("凭证无效或已过期")
        actor = Actor(
            kind="user",
            id=record.user.id,
            name=record.user.username,
            role=record.user.role,
        )
    request.state.actor = actor
    return actor


def _peer_is_trusted_proxy(peer: str) -> bool:
    """Whether the socket peer address belongs to a trusted proxy segment."""
    try:
        address: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(peer)
    except ValueError:
        # Not an address literal (synthetic test scopes, exotic transports):
        # fail closed and treat the socket peer itself as the client address.
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return any(address in network for network in TRUSTED_PROXY_NETWORKS)


def actor_ip(request: Request) -> str | None:
    """Best-effort real client address for audit rows and login throttling.

    The console reaches the API through the Nginx proxy, which appends the
    address it accepted to ``X-Forwarded-For``; the final entry is therefore the
    hop that actually connected to us, and an earlier forged entry cannot
    displace it. The header is honoured only when the socket peer itself belongs
    to :data:`TRUSTED_PROXY_NETWORKS` (mirroring ``web/nginx.conf``): a direct
    connection from any other address can set the header to whatever it likes,
    so falling back to the socket peer keeps login throttling bound to the real
    source and audit attribution honest (audit M-6). Requests without the
    header (hubctl, local curl) fall back to the socket peer as well.
    """
    peer = request.client.host if request.client is not None else None
    if peer is None or not _peer_is_trusted_proxy(peer):
        return peer
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        candidate = forwarded.rsplit(",", 1)[-1].strip()
        if candidate and len(candidate) <= _MAX_ADDRESS_LENGTH:
            return candidate
    return peer


def get_actor(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[HubSettings, Depends(get_settings)],
) -> Actor:
    """Resolve the request actor without an additional role floor."""
    return resolve_actor(request, session, settings)


def require_role(minimum: str) -> Callable[..., Actor]:
    """Build a dependency that requires at least one role from the matrix."""
    minimum_rank = ROLE_ORDER[minimum]

    def dependency(
        request: Request,
        session: Annotated[Session, Depends(get_session)],
        settings: Annotated[HubSettings, Depends(get_settings)],
    ) -> Actor:
        actor = resolve_actor(request, session, settings)
        if ROLE_ORDER[actor.role] < minimum_rank:
            raise HubError(code="FORBIDDEN", message="权限不足", status_code=403)
        return actor

    return dependency
