"""Artifact signature verification tests: service gate + install endpoint (B9/G9)."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest
import zstandard
from fastapi.testclient import TestClient
from hub_publisher.archive import create_plugin_package
from hub_publisher.builder import create_build_manifest
from hub_publisher.project import PluginProject
from hub_publisher.signing import sign_package, write_keypair
from hub_server.errors import HubError
from hub_server.main import create_app
from hub_server.models import AuditLogRecord
from hub_server.services.archives import PluginArchiveService
from hub_server.settings import (
    AuthSettings,
    DatabaseSettings,
    DeploymentSettings,
    HubSettings,
    PluginSignatureSettings,
    RunnerSettings,
    StorageSettings,
    UploadSettings,
)


def _make_pypkg() -> tuple[io.BytesIO, str]:
    """Build one structurally valid conda-pack package (same recipe as test_archives)."""
    members = {
        "plugin.yaml": (Path(__file__).parents[1] / "fixtures" / "valid-plugin.yaml").read_bytes(),
        "build.json": json.dumps(
            json.loads(
                (Path(__file__).parents[1] / "fixtures" / "valid-build.json").read_text("utf-8")
            ),
            separators=(",", ":"),
        ).encode(),
        "plugin/main.py": b"def run():\n    return None\n",
        "runtime/env.tar.zst": b"runtime archive",
    }
    checksums = {
        name: hashlib.sha256(content).hexdigest() for name, content in members.items()
    }
    members["checksums.json"] = json.dumps(checksums, sort_keys=True).encode()

    tar_bytes = io.BytesIO()
    with tarfile.open(fileobj=tar_bytes, mode="w:") as archive:
        for directory in ("plugin", "runtime"):
            info = tarfile.TarInfo(directory)
            info.type = tarfile.DIRTYPE
            archive.addfile(info)
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))

    package = zstandard.ZstdCompressor().compress(tar_bytes.getvalue())
    return io.BytesIO(package), hashlib.sha256(package).hexdigest()


@pytest.fixture
def keypair(tmp_path: Path) -> tuple[Path, Path, str]:
    return write_keypair(tmp_path / "hub-plugin-key")


def _public_b64(public: Path) -> str:
    """The base64 line of a ``.pub`` file, exactly as it goes into the config."""
    return public.read_text(encoding="utf-8").splitlines()[1].strip()


def _signed_upload(
    tmp_path: Path, secret: Path, public: Path
) -> tuple[io.BytesIO, str, str, bytes]:
    """One valid package plus its signature and the signer's registered public key."""
    upload, package_sha256 = _make_pypkg()
    package_path = tmp_path / "package.pypkg"
    package_path.write_bytes(upload.getvalue())
    signature_path, _ = sign_package(package_path, secret)
    return (
        io.BytesIO(package_path.read_bytes()),
        package_sha256,
        _public_b64(public),
        signature_path.read_bytes(),
    )


def test_signed_package_verifies_and_reports_the_trusting_fingerprint(
    tmp_path: Path, keypair: tuple[Path, Path, str]
) -> None:
    secret, public, fingerprint = keypair
    upload, package_sha256, public_b64, signature = _signed_upload(tmp_path, secret, public)

    verified = PluginArchiveService(tmp_path / "data").verify_and_install(
        upload, package_sha256, signature=signature, public_keys=[public_b64]
    )

    assert verified.signature_fingerprint == fingerprint


def test_tampered_package_is_rejected_before_decompression(
    tmp_path: Path, keypair: tuple[Path, Path, str]
) -> None:
    """A byte-flipped package with the original signature must die at the signature gate."""
    secret, public, _ = keypair
    upload, _, public_b64, signature = _signed_upload(tmp_path, secret, public)
    tampered = bytearray(upload.getvalue())
    tampered[len(tampered) // 2] ^= 0xFF
    tampered_sha256 = hashlib.sha256(bytes(tampered)).hexdigest()

    with pytest.raises(HubError) as raised:
        PluginArchiveService(tmp_path / "data").verify_and_install(
            io.BytesIO(bytes(tampered)),
            tampered_sha256,
            signature=signature,
            public_keys=[public_b64],
        )

    assert raised.value.status_code == 422
    # The zstd frame is corrupted too; hitting PLUGIN_PACKAGE_INVALID instead
    # would mean verification ran after decompression.
    assert raised.value.code == "PLUGIN_PACKAGE_SIGNATURE_INVALID"
    assert not list((tmp_path / "data" / "plugins" / ".staging").iterdir())


def test_unsigned_package_keeps_current_behaviour_when_require_signed_is_false(
    tmp_path: Path, keypair: tuple[Path, Path, str]
) -> None:
    _, public, _ = keypair
    upload, package_sha256 = _make_pypkg()

    verified = PluginArchiveService(tmp_path / "data").verify_and_install(
        upload,
        package_sha256,
        signature=None,
        public_keys=[_public_b64(public)],
        require_signed=False,
    )

    assert verified.signature_fingerprint is None


def test_unsigned_package_is_rejected_when_require_signed_is_true(tmp_path: Path) -> None:
    upload, package_sha256 = _make_pypkg()

    with pytest.raises(HubError) as raised:
        PluginArchiveService(tmp_path / "data").verify_and_install(
            upload, package_sha256, require_signed=True
        )

    assert raised.value.status_code == 422
    assert raised.value.code == "PLUGIN_PACKAGE_SIGNATURE_REQUIRED"


def test_signature_by_untrusted_key_is_rejected(
    tmp_path: Path, keypair: tuple[Path, Path, str]
) -> None:
    secret, public, _ = keypair
    upload, package_sha256, _, signature = _signed_upload(tmp_path, secret, public)
    _, other_public, _ = write_keypair(tmp_path / "other-key")
    other_b64 = _public_b64(other_public)

    with pytest.raises(HubError) as raised:
        PluginArchiveService(tmp_path / "data").verify_and_install(
            upload, package_sha256, signature=signature, public_keys=[other_b64]
        )

    assert raised.value.code == "PLUGIN_PACKAGE_SIGNATURE_INVALID"


def test_public_key_rotation_accepts_any_configured_key(
    tmp_path: Path, keypair: tuple[Path, Path, str]
) -> None:
    secret, public, fingerprint = keypair
    _, other_public, other_fingerprint = write_keypair(tmp_path / "rotated-key")
    upload, package_sha256, public_b64, signature = _signed_upload(tmp_path, secret, public)
    other_b64 = _public_b64(other_public)

    verified = PluginArchiveService(tmp_path / "data").verify_and_install(
        upload, package_sha256, signature=signature, public_keys=[other_b64, public_b64]
    )

    assert verified.signature_fingerprint == fingerprint
    assert verified.signature_fingerprint != other_fingerprint


def test_malformed_signature_document_is_rejected(
    tmp_path: Path, keypair: tuple[Path, Path, str]
) -> None:
    secret, public, _ = keypair
    _, package_sha256, public_b64, _ = _signed_upload(tmp_path, secret, public)
    body = _make_pypkg()[0].getvalue()
    for garbage in (b"not a signature", b"algorithm: rsa2048\nsignature: AAAA\n"):
        with pytest.raises(HubError) as raised:
            PluginArchiveService(tmp_path / "data").verify_and_install(
                io.BytesIO(body),
                package_sha256,
                signature=garbage,
                public_keys=[public_b64],
            )
        assert raised.value.code == "PLUGIN_PACKAGE_SIGNATURE_INVALID"


def test_signature_without_configured_keys_is_ignored_until_enforced(
    tmp_path: Path, keypair: tuple[Path, Path, str]
) -> None:
    """No keys configured = pre-B9 behaviour; require_signed with no keys fails closed."""
    secret, public, _ = keypair
    upload, package_sha256, _, signature = _signed_upload(tmp_path, secret, public)
    service = PluginArchiveService(tmp_path / "data")

    verified = service.verify_and_install(upload, package_sha256, signature=signature)

    assert verified.signature_fingerprint is None
    with pytest.raises(HubError) as raised:
        service.verify_and_install(
            io.BytesIO(upload.getvalue()),
            package_sha256,
            signature=signature,
            require_signed=True,
        )
    assert raised.value.code == "PLUGIN_PACKAGE_SIGNATURE_REQUIRED"


# ---------------------------------------------------------------------------
# Install endpoint (multipart "file" + optional "signature")
# ---------------------------------------------------------------------------


def _build_real_package(tmp_path: Path) -> Path:
    project_root = tmp_path / "plugin"
    source = project_root / "src" / "nc_to_shp_plugin"
    source.mkdir(parents=True)
    (project_root / "plugin.yaml").write_text(_manifest_yaml(), encoding="utf-8")
    (source / "__init__.py").write_text("", encoding="utf-8")
    (source / "main.py").write_text("def run():\n    return None\n", encoding="utf-8")
    runtime_archive = tmp_path / "runtime.tar.zst"
    runtime_archive.write_bytes(b"test runtime")
    project = PluginProject.load(project_root)
    build = create_build_manifest(
        project,
        "conda-pack",
        "amd64",
        runtime_archive,
        docker_digest=None,
        source_date_epoch=0,
    )
    return create_plugin_package(project, build, runtime_archive, tmp_path / "dist", 0)


def _make_client(
    tmp_path: Path, *, signature: PluginSignatureSettings | None = None
) -> TestClient:
    plugins_node = (
        {
            "signature": {
                "public_keys": signature.public_keys,
                "require_signed": signature.require_signed,
            }
        }
        if signature is not None
        else {}
    )
    settings = HubSettings(
        deployment=DeploymentSettings(mode="offline"),
        storage=StorageSettings(root=tmp_path / "data"),
        database=DatabaseSettings(url=f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"),
        uploads=UploadSettings(max_size_bytes=1024 * 1024),
        runner=RunnerSettings(shared_token="runner-test-secret", poll_interval_seconds=1),
        auth=AuthSettings(mode="off"),
        plugins=plugins_node,
    )
    return TestClient(create_app(settings))


def test_install_endpoint_accepts_package_with_valid_signature(
    tmp_path: Path, keypair: tuple[Path, Path, str]
) -> None:
    secret, public, _ = keypair
    package = _build_real_package(tmp_path)
    signature_path, _ = sign_package(package, secret)
    public_b64 = _public_b64(public)
    client = _make_client(
        tmp_path,
        signature=PluginSignatureSettings(public_keys=[public_b64], require_signed=True),
    )

    with client:
        response = client.post(
            "/api/v1/plugins/install",
            files=[
                ("file", (package.name, package.read_bytes(), "application/octet-stream")),
                ("signature", (package.name + ".sig", signature_path.read_bytes(), "text/plain")),
            ],
        )

    assert response.status_code == 202, response.text
    assert response.json()["status"] == "INSTALLING"


def test_install_endpoint_rejects_package_signed_by_untrusted_key(
    tmp_path: Path, keypair: tuple[Path, Path, str]
) -> None:
    secret, public, _ = keypair
    package = _build_real_package(tmp_path)
    other_package = tmp_path / "other.pypkg"
    other_package.write_bytes(b"a different package")
    other_signature, _ = sign_package(other_package, secret)
    public_b64 = _public_b64(public)
    client = _make_client(
        tmp_path, signature=PluginSignatureSettings(public_keys=[public_b64])
    )

    with client:
        response = client.post(
            "/api/v1/plugins/install",
            files=[
                ("file", (package.name, package.read_bytes(), "application/octet-stream")),
                (
                    "signature",
                    (package.name + ".sig", other_signature.read_bytes(), "text/plain"),
                ),
            ],
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PLUGIN_PACKAGE_SIGNATURE_INVALID"


def test_install_endpoint_requires_signature_when_enforced(tmp_path: Path) -> None:
    _, public, _ = write_keypair(tmp_path / "hub-plugin-key")
    public_b64 = _public_b64(public)
    package = _build_real_package(tmp_path)
    client = _make_client(
        tmp_path,
        signature=PluginSignatureSettings(public_keys=[public_b64], require_signed=True),
    )

    with client:
        response = client.post(
            "/api/v1/plugins/install",
            files={"file": (package.name, package.read_bytes(), "application/octet-stream")},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PLUGIN_PACKAGE_SIGNATURE_REQUIRED"


def test_install_endpoint_without_keys_behaves_exactly_as_before_b9(
    tmp_path: Path, keypair: tuple[Path, Path, str]
) -> None:
    """Regression: no plugins.signature node, signature field sent anyway -> accepted."""
    secret, _, _ = keypair
    package = _build_real_package(tmp_path)
    signature_path, _ = sign_package(package, secret)
    client = _make_client(tmp_path)

    with client:
        response = client.post(
            "/api/v1/plugins/install",
            files=[
                ("file", (package.name, package.read_bytes(), "application/octet-stream")),
                ("signature", ("pkg.sig", signature_path.read_bytes(), "text/plain")),
            ],
        )

    assert response.status_code == 202, response.text
    assert response.json()["package_sha256"] == hashlib.sha256(package.read_bytes()).hexdigest()
    with client.app.state.session_factory() as session:
        entry = session.query(AuditLogRecord).filter_by(action="plugin.install").one()
        assert entry.detail.get("signature_fingerprint") is None


def test_install_endpoint_audits_the_verifying_fingerprint(
    tmp_path: Path, keypair: tuple[Path, Path, str]
) -> None:
    secret, public, fingerprint = keypair
    package = _build_real_package(tmp_path)
    signature_path, _ = sign_package(package, secret)
    client = _make_client(
        tmp_path, signature=PluginSignatureSettings(public_keys=[_public_b64(public)])
    )

    with client:
        response = client.post(
            "/api/v1/plugins/install",
            files=[
                ("file", (package.name, package.read_bytes(), "application/octet-stream")),
                ("signature", ("pkg.sig", signature_path.read_bytes(), "text/plain")),
            ],
        )
        assert response.status_code == 202, response.text
        with client.app.state.session_factory() as session:
            entry = session.query(AuditLogRecord).filter_by(action="plugin.install").one()
            assert entry.detail.get("signature_fingerprint") == fingerprint


def _manifest_yaml() -> str:
    return """\
spec_version: "1.0"
plugin:
  id: nc_to_shp
  name: NC to Shapefile
  version: "1.0.0"
sdk:
  version: "1.0"
runtime:
  type: process
  python:
    version: "3.12"
entrypoint:
  module: nc_to_shp_plugin.main
  function: run
parameters: []
inputs: []
outputs: []
execution:
  timeout: 30
  concurrency: 1
environment_variables:
  required: []
healthcheck:
  enabled: true
  type: import
"""
