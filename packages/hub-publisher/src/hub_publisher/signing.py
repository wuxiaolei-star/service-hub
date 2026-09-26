"""Ed25519 key generation and package signing for plugin authors (B9/G9).

The byte-level wire format (what exactly is signed, and the ``.sig`` document
grammar) lives in ``python_hub_contracts.signing``; this module adds the
publisher-side key management and file handling. ``cryptography`` is an
*optional* dependency (``pip install 'python-hub-publisher[signing]'``) so the
build/validate/dev workflow does not drag it in; every entry point raises
:class:`SigningDependencyError` with an actionable message when it is missing.

Key files are two-line text documents (comment header + one base64 line):

- ``<prefix>.sec``: the raw 32-byte Ed25519 seed. Keep it offline and out of
  source control; anyone holding it can sign packages the Hub will accept.
- ``<prefix>.pub``: the raw 32-byte public key. Its base64 line goes into the
  Hub's ``plugins.signature.public_keys`` list verbatim.
"""

from __future__ import annotations

import base64
import contextlib
import os
from pathlib import Path
from typing import Any

from python_hub_contracts import (
    SignatureDocumentError,
    key_fingerprint,
    normalize_public_key,
    render_signature_document,
    signature_message,
)

PUBLIC_KEY_SUFFIX = ".pub"
SECRET_KEY_SUFFIX = ".sec"
SIGNATURE_SUFFIX = ".sig"

_UNTRUSTED_PUBLIC_COMMENT = "untrusted comment: hub-plugin ed25519 public key"
_UNTRUSTED_SECRET_COMMENT = "untrusted comment: hub-plugin ed25519 secret key"
_MISSING_DEPENDENCY = (
    "signing support needs the optional dependency group 'signing'; "
    "install it with: pip install 'python-hub-publisher[signing]'"
)


class SigningDependencyError(Exception):
    """``cryptography`` is not installed in the publisher environment."""


class SigningKeyError(ValueError):
    """A key file is missing, malformed, or not an Ed25519 key."""


def _load_cryptography() -> tuple[Any, Any]:
    """Import ``cryptography`` lazily so the error names the fix, not the trace."""
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError as error:
        raise SigningDependencyError(_MISSING_DEPENDENCY) from error
    return ed25519, serialization


def generate_keypair() -> tuple[bytes, bytes]:
    """Generate one (raw 32-byte public key, raw 32-byte seed) pair."""
    ed25519, serialization = _load_cryptography()
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    seed = private_key.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    return public_key, seed


def write_keypair(out_prefix: Path) -> tuple[Path, Path, str]:
    """Write ``<prefix>.sec`` (0600 where honoured) and ``<prefix>.pub``.

    Returns ``(secret_path, public_path, fingerprint)``. Existing files are
    never overwritten: a silent overwrite is how private keys get lost or
    trust sets go stale.
    """
    public_key, seed = generate_keypair()
    fingerprint = key_fingerprint(public_key)
    secret_path = out_prefix.with_name(out_prefix.name + SECRET_KEY_SUFFIX)
    public_path = out_prefix.with_name(out_prefix.name + PUBLIC_KEY_SUFFIX)
    _write_new_file(secret_path, _key_file_body(_UNTRUSTED_SECRET_COMMENT, seed))
    _write_new_file(public_path, _key_file_body(_UNTRUSTED_PUBLIC_COMMENT, public_key))
    with contextlib.suppress(OSError):
        # Windows ignores POSIX modes; ACLs govern access there instead.
        os.chmod(secret_path, 0o600)
    return secret_path, public_path, fingerprint


def load_public_key_file(path: Path) -> bytes:
    """Read a ``.pub`` file and return the raw 32-byte public key."""
    ed25519, _ = _load_cryptography()
    raw = _read_key_file(path)
    try:
        ed25519.Ed25519PublicKey.from_public_bytes(raw)
    except ValueError as error:
        raise SigningKeyError(f"{path}: not a usable Ed25519 public key: {error}") from error
    return raw


def load_secret_key_file(path: Path) -> bytes:
    """Read a ``.sec`` file and return the raw 32-byte seed."""
    ed25519, _ = _load_cryptography()
    seed = _read_key_file(path)
    try:
        ed25519.Ed25519PrivateKey.from_private_bytes(seed)
    except ValueError as error:
        raise SigningKeyError(f"{path}: not a usable Ed25519 secret key: {error}") from error
    return seed


def sign_package(
    package: Path, secret_key_file: Path, output: Path | None = None
) -> tuple[Path, str]:
    """Sign one ``.pypkg`` and write the ``.sig`` document next to it.

    The package is hashed in a streaming pass (``.pypkg`` files can be far
    larger than memory) and the digest is signed with the same domain
    separation the Hub verifies. Returns ``(signature_path, fingerprint)``.
    """
    from .archive import sha256_file

    package_sha256 = sha256_file(package)
    ed25519, serialization = _load_cryptography()
    seed = load_secret_key_file(secret_key_file)
    private_key = ed25519.Ed25519PrivateKey.from_private_bytes(seed)
    signature = private_key.sign(signature_message(package_sha256))
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    fingerprint = key_fingerprint(public_key)
    destination = (
        output if output is not None else package.with_name(package.name + SIGNATURE_SUFFIX)
    )
    try:
        document = render_signature_document(public_key, package_sha256, signature)
    except SignatureDocumentError as error:  # pragma: no cover - inputs are generated above
        raise SigningKeyError(f"signature could not be rendered: {error}") from error
    destination.write_text(document, encoding="utf-8")
    return destination, fingerprint


def _key_file_body(comment: str, raw: bytes) -> str:
    encoded = base64.b64encode(raw).decode("ascii")
    return f"{comment} {key_fingerprint(raw)}\n{encoded}\n"


def _read_key_file(path: Path) -> bytes:
    """Parse a two-line key file (``untrusted comment:`` header + one base64 line)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise SigningKeyError(f"{path}: cannot read key file: {error}") from error
    for line in text.splitlines():
        candidate = line.strip()
        if not candidate or candidate.startswith("#") or candidate.startswith("untrusted comment:"):
            continue
        try:
            return base64.b64decode(normalize_public_key(candidate), validate=True)
        except SignatureDocumentError as error:
            raise SigningKeyError(f"{path}: {error}") from error
    raise SigningKeyError(f"{path}: no base64 key line found")


def _write_new_file(path: Path, content: str) -> None:
    if path.exists():
        raise SigningKeyError(f"{path}: already exists; refusing to overwrite a key file")
    try:
        path.write_text(content, encoding="utf-8")
    except OSError as error:
        raise SigningKeyError(f"{path}: cannot write key file: {error}") from error
