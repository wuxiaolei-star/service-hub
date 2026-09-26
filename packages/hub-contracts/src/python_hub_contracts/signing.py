"""Wire format shared by package signing (publisher CLI) and verification (Hub).

B9/G9: a package signature covers the package's SHA-256 digest rather than the
whole file. Whole-file Ed25519 signing would require the entire archive in
memory (packages are bounded at 12 GiB), while the digest is computed anyway
while streaming the upload. To keep a digest signature from being replayed as
a signature of some unrelated 32-byte message, the signed message is
domain-separated: an ASCII prefix followed by the lowercase hex digest.

The signature travels in a self-describing UTF-8 text document (one
``key: value`` line per field). Producers render it with
:func:`render_signature_document`; consumers parse it with
:func:`parse_signature_document`. The grammar is frozen as v1: unknown keys are
ignored (forward compatibility), unknown *algorithm* values are rejected
(fail-closed), and every known key may appear at most once.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from dataclasses import dataclass
from typing import Final

SIGNATURE_ALGORITHM: Final = "ed25519"
"""The only signature algorithm this wire format (v1) defines."""

_SIGNATURE_MESSAGE_PREFIX: Final = b"hub-plugin-package:v1:"
_PUBLIC_KEY_BYTES: Final = 32
_SIGNATURE_BYTES: Final = 64

_UNTRUSTED_COMMENT: Final = "untrusted comment: hub-plugin package signature"


class SignatureDocumentError(ValueError):
    """A signature document (or one of its fields) is malformed."""


def signature_message(package_sha256: str) -> bytes:
    """Build the exact byte message an Ed25519 key signs for one package.

    Both the publisher CLI and the Hub must derive this from the *recomputed*
    digest of the actual package bytes; a digest claimed by the uploader is
    never an input to verification.
    """
    if len(package_sha256) != 64 or any(c not in "0123456789abcdef" for c in package_sha256):
        raise SignatureDocumentError("package_sha256 must be a lowercase 64-char hex digest")
    return _SIGNATURE_MESSAGE_PREFIX + package_sha256.encode("ascii")


def key_fingerprint(public_key: bytes) -> str:
    """Return the short human-comparable fingerprint of one raw public key.

    The first 16 hex characters of SHA-256 over the raw 32-byte key: enough to
    tell keys apart when auditing ``hub.yaml`` or a ``.sig`` file, short enough
    to read aloud.
    """
    if len(public_key) != _PUBLIC_KEY_BYTES:
        raise SignatureDocumentError("public key must be exactly 32 raw bytes")
    return hashlib.sha256(public_key).hexdigest()[:16]


def normalize_public_key(value: str) -> str:
    """Validate one base64-encoded Ed25519 public key and return canonical base64.

    Used by the Hub settings (fail fast on typos at startup) and by the CLI when
    reading ``.pub`` files. Strict decoding rejects whitespace and padding games;
    the length check pins the key to raw 32-byte Ed25519.
    """
    if isinstance(value, str):
        candidate = value.strip()
        try:
            raw = base64.b64decode(candidate, validate=True)
        except (binascii.Error, ValueError) as error:
            raise SignatureDocumentError(f"public key is not valid base64: {value!r}") from error
    else:
        raise SignatureDocumentError("public key must be a base64 string")
    if len(raw) != _PUBLIC_KEY_BYTES:
        raise SignatureDocumentError(
            f"public key must decode to {_PUBLIC_KEY_BYTES} raw bytes, got {len(raw)}"
        )
    return base64.b64encode(raw).decode("ascii")


@dataclass(frozen=True, slots=True)
class SignatureDocument:
    """Parsed fields of one ``.sig`` document.

    ``key_fingerprint`` and ``package_sha256`` are advisory annotations rendered
    for humans; verification always recomputes the digest and matches the key
    against the configured trust set, so a lie in either field can only cause a
    verification failure, never a bypass.
    """

    algorithm: str
    signature: bytes
    key_fingerprint: str | None = None
    package_sha256: str | None = None


def render_signature_document(public_key: bytes, package_sha256: str, signature: bytes) -> str:
    """Render the ``.sig`` document the publisher CLI writes next to a package."""
    if len(signature) != _SIGNATURE_BYTES:
        raise SignatureDocumentError(
            f"signature must be exactly {_SIGNATURE_BYTES} raw bytes, got {len(signature)}"
        )
    lines = [
        f"{_UNTRUSTED_COMMENT}",
        f"algorithm: {SIGNATURE_ALGORITHM}",
        f"key_fingerprint: {key_fingerprint(public_key)}",
        f"package_sha256: {package_sha256}",
        f"signature: {base64.b64encode(signature).decode('ascii')}",
    ]
    return "\n".join(lines) + "\n"


def parse_signature_document(raw: bytes) -> SignatureDocument:
    """Parse and structurally validate one ``.sig`` document.

    Raises :class:`SignatureDocumentError` on encoding, grammar, algorithm,
    duplicate-field or base64/length problems. Cryptographic verification
    against a trusted key is a separate step by design.
    """
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SignatureDocumentError("signature document is not valid UTF-8") from error

    fields: dict[str, str] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition(":")
        if not separator:
            raise SignatureDocumentError(
                f"signature document line {line_number} is not 'key: value': {stripped!r}"
            )
        key = key.strip()
        value = value.strip()
        if key in fields:
            raise SignatureDocumentError(f"signature document repeats field {key!r}")
        fields[key] = value

    algorithm = fields.get("algorithm")
    if algorithm is None:
        raise SignatureDocumentError("signature document is missing the 'algorithm' field")
    if algorithm != SIGNATURE_ALGORITHM:
        raise SignatureDocumentError(
            f"unsupported signature algorithm {algorithm!r} (expected {SIGNATURE_ALGORITHM!r})"
        )
    encoded = fields.get("signature")
    if encoded is None:
        raise SignatureDocumentError("signature document is missing the 'signature' field")
    try:
        signature = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise SignatureDocumentError("signature field is not valid base64") from error
    if len(signature) != _SIGNATURE_BYTES:
        raise SignatureDocumentError(
            f"signature must decode to {_SIGNATURE_BYTES} raw bytes, got {len(signature)}"
        )

    fingerprint = fields.get("key_fingerprint")
    if fingerprint is not None and len(fingerprint) != 16:
        raise SignatureDocumentError("key_fingerprint must be 16 hex characters")
    package_sha256 = fields.get("package_sha256")
    if package_sha256 is not None and (
        len(package_sha256) != 64 or any(c not in "0123456789abcdef" for c in package_sha256)
    ):
        raise SignatureDocumentError("package_sha256 must be a lowercase 64-char hex digest")
    return SignatureDocument(
        algorithm=algorithm,
        signature=signature,
        key_fingerprint=fingerprint,
        package_sha256=package_sha256,
    )
