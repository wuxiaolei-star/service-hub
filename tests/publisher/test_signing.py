"""Publisher-side signing tests: keygen, sign, and the missing-dependency path."""

from __future__ import annotations

import base64
import hashlib
import importlib.abc
import sys
from pathlib import Path

import pytest
from hub_publisher.cli import main
from hub_publisher.signing import key_fingerprint, sign_package, write_keypair
from python_hub_contracts import parse_signature_document


@pytest.fixture
def no_cryptography(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate a publisher venv without the optional dependency group.

    Purging ``sys.modules`` is not enough (cached submodule imports bypass the
    finder), so a meta-path blocker also refuses every ``cryptography.*``
    import for the duration of the test.
    """
    for name in [n for n in sys.modules if n == "cryptography" or n.startswith("cryptography.")]:
        monkeypatch.delitem(sys.modules, name, raising=False)

    class _Blocker(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):  # type: ignore[no-untyped-def]
            if fullname == "cryptography" or fullname.startswith("cryptography."):
                raise ImportError(f"simulated missing dependency: {fullname}")
            return None

    monkeypatch.setattr(sys, "meta_path", [_Blocker(), *sys.meta_path])


@pytest.fixture
def keypair(tmp_path: Path) -> tuple[Path, Path, str]:
    secret, public, fingerprint = write_keypair(tmp_path / "hub-plugin-key")
    return secret, public, fingerprint


def test_keygen_writes_secret_and_public_key_files(tmp_path: Path) -> None:
    secret_path, public_path, fingerprint = write_keypair(tmp_path / "hub-plugin-key")

    secret_body = secret_path.read_text(encoding="utf-8").splitlines()
    public_body = public_path.read_text(encoding="utf-8").splitlines()
    assert secret_body[0].startswith("untrusted comment: hub-plugin ed25519 secret key")
    assert public_body[0].startswith("untrusted comment: hub-plugin ed25519 public key")
    seed = base64.b64decode(secret_body[1], validate=True)
    public_key = base64.b64decode(public_body[1], validate=True)
    assert len(seed) == 32 and len(public_key) == 32
    assert fingerprint == key_fingerprint(public_key)


def test_keygen_refuses_to_overwrite_existing_key_files(tmp_path: Path) -> None:
    write_keypair(tmp_path / "hub-plugin-key")

    with pytest.raises(Exception, match="refusing to overwrite"):
        write_keypair(tmp_path / "hub-plugin-key")


def test_sign_package_writes_document_that_the_hub_verifies(
    tmp_path: Path, keypair: tuple[Path, Path, str]
) -> None:
    secret, public, fingerprint = keypair
    package = tmp_path / "plugin.pypkg"
    package.write_bytes(b"package bytes")

    destination, signed_fingerprint = sign_package(package, secret)

    assert destination == tmp_path / "plugin.pypkg.sig"
    assert signed_fingerprint == fingerprint
    document = parse_signature_document(destination.read_bytes())
    assert document.package_sha256 == hashlib.sha256(b"package bytes").hexdigest()
    assert document.key_fingerprint == fingerprint
    public_b64 = public.read_text(encoding="utf-8").splitlines()[1].strip()
    assert public_b64 == base64.b64encode(
        base64.b64decode(public_b64, validate=True)
    ).decode("ascii")


def test_sign_package_honours_explicit_output_path(
    tmp_path: Path, keypair: tuple[Path, Path, str]
) -> None:
    secret, _, _ = keypair
    package = tmp_path / "plugin.pypkg"
    package.write_bytes(b"package bytes")
    output = tmp_path / "custom.sig"

    destination, _ = sign_package(package, secret, output)

    assert destination == output
    assert output.exists()


def test_sign_package_rejects_missing_or_malformed_key_files(tmp_path: Path) -> None:
    package = tmp_path / "plugin.pypkg"
    package.write_bytes(b"package bytes")

    with pytest.raises(Exception, match="cannot read key file"):
        sign_package(package, tmp_path / "missing.sec")

    malformed = tmp_path / "malformed.sec"
    malformed.write_text("untrusted comment: junk\nnot base64!!\n", encoding="utf-8")
    with pytest.raises(Exception, match="base64"):
        sign_package(package, malformed)


def test_sign_package_without_cryptography_prints_install_hint(
    tmp_path: Path, keypair: tuple[Path, Path, str], no_cryptography: None, capsys
) -> None:
    secret, _, _ = keypair
    package = tmp_path / "plugin.pypkg"
    package.write_bytes(b"package bytes")

    exit_code = main(["sign", str(package), "--key", str(secret)])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "python-hub-publisher[signing]" in captured.err


def test_keygen_without_cryptography_prints_install_hint(
    tmp_path: Path, no_cryptography: None, capsys
) -> None:
    exit_code = main(["keygen", "--out-prefix", str(tmp_path / "hub-plugin-key")])

    assert exit_code == 1
    assert "python-hub-publisher[signing]" in capsys.readouterr().err


def test_cli_keygen_end_to_end(tmp_path: Path, capsys) -> None:
    exit_code = main(["keygen", "--out-prefix", str(tmp_path / "hub-plugin-key")])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert (tmp_path / "hub-plugin-key.sec").exists()
    assert (tmp_path / "hub-plugin-key.pub").exists()
    assert "plugins.signature.public_keys" in out


def test_cli_sign_end_to_end(tmp_path: Path, keypair: tuple[Path, Path, str], capsys) -> None:
    secret, _, fingerprint = keypair
    package = tmp_path / "plugin.pypkg"
    package.write_bytes(b"package bytes")

    exit_code = main(["sign", str(package), "--key", str(secret)])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert fingerprint in out
    assert (tmp_path / "plugin.pypkg.sig").exists()
