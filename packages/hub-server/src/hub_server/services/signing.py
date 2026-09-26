"""Ed25519 verification of uploaded plugin package signatures (B9/G9).

The trust set comes from ``plugins.signature.public_keys``; the signed message
and the ``.sig`` document grammar live in ``python_hub_contracts.signing`` so
the publisher CLI and the Hub cannot drift apart. Cryptographic verification
needs ``cryptography``, a declared dependency of ``hub-server``: a deployment
that configures public keys must never silently skip verification.
"""

from __future__ import annotations

import base64
from collections.abc import Sequence
from typing import Final

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from python_hub_contracts import (
    SignatureDocumentError,
    key_fingerprint,
    normalize_public_key,
    parse_signature_document,
    signature_message,
)

MAX_SIGNATURE_BYTES: Final = 64 * 1024
"""A valid ``.sig`` document is well under 1 KiB; anything larger is rejected
before parsing so a bloated form field cannot cost memory."""


class PackageSignatureError(ValueError):
    """A package signature is malformed or does not match any configured key."""


def public_key_bytes(entry: str) -> bytes:
    """Decode one configured public key entry to its raw 32 bytes."""
    try:
        return base64.b64decode(normalize_public_key(entry), validate=True)
    except SignatureDocumentError as error:
        raise PackageSignatureError(f"configured public key is malformed: {error}") from error


def verify_package_signature(
    package_sha256: str,
    signature_document: bytes,
    public_keys: Sequence[str],
) -> str:
    """Verify one signature document against the configured trust set.

    Returns the fingerprint of the public key whose signature verified (so the
    audit trail can name the key that accepted the package). Raises
    :class:`PackageSignatureError` when the document is malformed, names an
    unsupported algorithm, or verifies against none of the configured keys.
    """
    if len(signature_document) > MAX_SIGNATURE_BYTES:
        raise PackageSignatureError(f"signature document exceeds {MAX_SIGNATURE_BYTES} bytes")
    try:
        document = parse_signature_document(signature_document)
    except SignatureDocumentError as error:
        raise PackageSignatureError(f"signature document is invalid: {error}") from error

    message = signature_message(package_sha256)
    for entry in public_keys:
        key = public_key_bytes(entry)
        try:
            Ed25519PublicKey.from_public_bytes(key).verify(document.signature, message)
        except InvalidSignature:
            continue
        return key_fingerprint(key)
    raise PackageSignatureError(
        "signature does not match any configured plugins.signature.public_keys entry"
    )
