"""Outbound webhook guard tests: registration rules and delivery-time re-checks."""

from __future__ import annotations

import email.message
import socket
import urllib.error
import urllib.request
from collections.abc import Callable

import pytest
from hub_server.errors import WebhookUrlForbiddenError
from hub_server.services import webhook_guard, webhooks
from hub_server.services.webhook_guard import (
    ensure_delivery_target_allowed,
    normalize_webhook_url,
)
from hub_server.settings import WebhooksSettings

PUBLIC_ADDRESS = "93.184.216.34"

GetAddrInfo = Callable[..., list[tuple[int, int, int, str, tuple[str, int]]]]


def _stub_resolution(monkeypatch: pytest.MonkeyPatch, *addresses: str) -> None:
    """Force every DNS lookup to answer with the given addresses."""

    def fake_getaddrinfo(
        host: str, port: object, **kwargs: object
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 0))
            for address in addresses
        ]

    monkeypatch.setattr(webhook_guard.socket, "getaddrinfo", fake_getaddrinfo)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8001/health",
        "http://[::1]:8001/health",
        "http://[::ffff:127.0.0.1]/health",
        "http://10.1.2.3/callback",
        "http://172.16.9.9/callback",
        "http://192.168.1.10/callback",
        "http://169.254.169.254/latest/meta-data/",
        "http://100.64.0.1/callback",
        "http://2130706433/callback",
        "http://127.1/callback",
        "http://0x7f000001/callback",
        "http://localhost/callback",
        "http://localhost./callback",
        "http://metadata.google.internal/computeMetadata/v1/",
    ],
)
def test_registration_rejects_internal_targets(url: str) -> None:
    """Every internal spelling of a callback target must fail registration."""
    with pytest.raises(WebhookUrlForbiddenError):
        normalize_webhook_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://hooks.example.test/done",
        "file:///etc/passwd",
        "https://user:password@hooks.example.test/done",
        "https:///done",
        "http://./done",
        "https://example.com:99999/done",
    ],
)
def test_registration_rejects_unsupported_url_shapes(url: str) -> None:
    """Only plain http/https URLs without embedded credentials may be registered."""
    with pytest.raises(WebhookUrlForbiddenError):
        normalize_webhook_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://hooks.example.test/done",
        "http://example.com/callback",
        "https://example.com:8443/callback",
        f"http://{PUBLIC_ADDRESS}/callback",
    ],
)
def test_registration_accepts_public_targets(url: str) -> None:
    assert normalize_webhook_url(url) == url


def test_registration_strips_surrounding_whitespace() -> None:
    assert normalize_webhook_url("  https://hooks.example.test/done\n") == (
        "https://hooks.example.test/done"
    )


def test_allowlist_rejects_an_unlisted_host() -> None:
    policy = WebhooksSettings(allowed_hosts=["hooks.example.com"])

    with pytest.raises(WebhookUrlForbiddenError):
        normalize_webhook_url("https://evil.example.net/done", policy)


def test_allowlist_matches_exact_and_wildcard_hosts() -> None:
    """A wildcard covers subdomains only, never the bare apex domain."""
    policy = WebhooksSettings(allowed_hosts=["hooks.example.com", "*.corp.example"])

    assert normalize_webhook_url("https://hooks.example.com/done", policy)
    assert normalize_webhook_url("https://hooks.example.com./done", policy)
    assert normalize_webhook_url("https://a.corp.example/done", policy)
    with pytest.raises(WebhookUrlForbiddenError):
        normalize_webhook_url("https://corp.example/done", policy)


def test_private_networks_require_an_explicit_opt_in() -> None:
    policy = WebhooksSettings(allow_private_networks=True)

    assert normalize_webhook_url("http://10.1.2.3/callback", policy) == "http://10.1.2.3/callback"
    assert (
        normalize_webhook_url("http://localhost:9000/hook", policy) == "http://localhost:9000/hook"
    )


def test_allowlist_still_applies_after_the_private_opt_in() -> None:
    policy = WebhooksSettings(allow_private_networks=True, allowed_hosts=["hooks.corp.example"])

    assert normalize_webhook_url("http://hooks.corp.example/done", policy)
    with pytest.raises(WebhookUrlForbiddenError):
        normalize_webhook_url("http://10.1.2.3/callback", policy)


def test_delivery_re_checks_rules_recorded_before_the_guard() -> None:
    """Rows written before the guard existed must not be dialed."""
    with pytest.raises(WebhookUrlForbiddenError):
        ensure_delivery_target_allowed("http://127.0.0.1:8001/health")


def test_delivery_rejects_a_host_that_resolves_to_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rebinding answer is refused even though the URL itself looks public."""
    _stub_resolution(monkeypatch, "127.0.0.1")

    with pytest.raises(WebhookUrlForbiddenError):
        ensure_delivery_target_allowed("https://hooks.example.test/done")


def test_delivery_rejects_one_bad_address_in_a_mixed_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_resolution(monkeypatch, PUBLIC_ADDRESS, "169.254.169.254")

    with pytest.raises(WebhookUrlForbiddenError):
        ensure_delivery_target_allowed("https://hooks.example.test/done")


def test_delivery_allows_a_resolved_public_address(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_resolution(monkeypatch, PUBLIC_ADDRESS)

    ensure_delivery_target_allowed("https://hooks.example.test/done")


def test_delivery_skips_resolution_after_the_private_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The opt-in must not pay a DNS lookup it has already decided to ignore."""
    calls: list[str] = []

    def fail_getaddrinfo(host: str, port: object, **kwargs: object) -> GetAddrInfo:
        raise AssertionError(f"unexpected resolution of {host!r}")

    monkeypatch.setattr(webhook_guard.socket, "getaddrinfo", fail_getaddrinfo)
    policy = WebhooksSettings(allow_private_networks=True)

    ensure_delivery_target_allowed("http://10.1.2.3/callback", policy)

    assert calls == []


class _FakeResponse:
    """Minimal urlopen result: a context manager exposing ``status``."""

    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: object) -> bool:
        return False


class _ScriptedOpener:
    """_DELIVERY_OPENER double: each outcome is a status int or an HTTPError."""

    def __init__(self, *outcomes: object) -> None:
        self._outcomes = list(outcomes)
        self.requested: list[str] = []
        self.headers_seen: list[dict[str, str]] = []

    def open(self, request: urllib.request.Request, timeout: float) -> _FakeResponse:
        self.requested.append(request.full_url)
        self.headers_seen.append({k.lower(): v for k, v in request.header_items()})
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, int):
            return _FakeResponse(outcome)
        raise outcome


def _redirect(code: int, location: str) -> urllib.error.HTTPError:
    headers = email.message.Message()
    headers["Location"] = location
    return urllib.error.HTTPError(
        "https://hooks.example.test/done", code, "redirect", headers, None
    )


def _scripted_opener(
    monkeypatch: pytest.MonkeyPatch, *outcomes: object
) -> _ScriptedOpener:
    opener = _ScriptedOpener(*outcomes)
    monkeypatch.setattr(webhooks, "_DELIVERY_OPENER", opener)
    return opener


def test_a_redirect_to_a_private_address_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A public first hop must not bounce the delivery onto an internal target."""
    opener = _scripted_opener(
        monkeypatch, _redirect(302, "http://169.254.169.254/latest/meta-data/")
    )

    with pytest.raises(WebhookUrlForbiddenError):
        webhooks._post_json("https://hooks.example.test/done", b"{}", {})

    assert opener.requested == ["https://hooks.example.test/done"]


def test_a_redirect_outside_the_allow_list_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The allow-list applies to every hop, not only the registered URL."""
    policy = WebhooksSettings(allowed_hosts=["hooks.example.test"])
    opener = _scripted_opener(monkeypatch, _redirect(302, "https://evil.example.net/hook"))

    with pytest.raises(WebhookUrlForbiddenError):
        webhooks._post_json("https://hooks.example.test/done", b"{}", {}, policy)

    assert opener.requested == ["https://hooks.example.test/done"]


def test_a_redirect_chain_longer_than_three_hops_is_abandoned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_resolution(monkeypatch, PUBLIC_ADDRESS)
    opener = _scripted_opener(
        monkeypatch,
        _redirect(302, "https://hop1.example.test/1"),
        _redirect(302, "https://hop2.example.test/2"),
        _redirect(302, "https://hop3.example.test/3"),
        _redirect(302, "https://hop4.example.test/4"),
    )

    with pytest.raises(WebhookUrlForbiddenError) as refused:
        webhooks._post_json("https://hooks.example.test/done", b"{}", {})

    assert "重定向跳数超过上限" in refused.value.message
    assert len(opener.requested) == 4  # the original plus the three allowed hops


def test_a_public_redirect_is_followed_without_the_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Allowed hops are re-posted to, but the signature never reaches them."""
    _stub_resolution(monkeypatch, PUBLIC_ADDRESS)
    policy = WebhooksSettings(allowed_hosts=["hooks.example.test", "*.example.net"])
    opener = _scripted_opener(monkeypatch, _redirect(302, "https://m.example.net/hook"), 200)

    status = webhooks._post_json(
        "https://hooks.example.test/done",
        b"{}",
        {"Content-Type": "application/json", "X-Hub-Signature": "sha256=deadbeef"},
        policy,
    )

    assert status == 200
    assert opener.requested == [
        "https://hooks.example.test/done",
        "https://m.example.net/hook",
    ]
    assert "x-hub-signature" not in opener.headers_seen[1]
    assert opener.headers_seen[0]["x-hub-signature"] == "sha256=deadbeef"


def test_a_relative_redirect_stays_on_the_validated_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_resolution(monkeypatch, PUBLIC_ADDRESS)
    opener = _scripted_opener(monkeypatch, _redirect(303, "/elsewhere"), 200)

    status = webhooks._post_json("https://hooks.example.test/done", b"{}", {})

    assert status == 200
    assert opener.requested == [
        "https://hooks.example.test/done",
        "https://hooks.example.test/elsewhere",
    ]


def test_a_non_redirect_http_error_is_not_masked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opener = _scripted_opener(
        monkeypatch,
        urllib.error.HTTPError(
            "https://hooks.example.test/done", 500, "boom", email.message.Message(), None
        ),
    )

    with pytest.raises(urllib.error.HTTPError):
        webhooks._post_json("https://hooks.example.test/done", b"{}", {})

    assert opener.requested == ["https://hooks.example.test/done"]
