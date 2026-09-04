from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from python_hub_contracts import load_plugin_manifest

FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_yaml_duplicate_mapping_keys_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.yaml"
    path.write_text("spec_version: '1.0'\nspec_version: '1.0'\n", encoding="utf-8")

    with pytest.raises(yaml.constructor.ConstructorError, match="duplicate mapping key"):
        load_plugin_manifest(path)


@pytest.mark.parametrize(
    "document",
    [
        "spec_version: &version '1.0'\n",
        "spec_version: &version '1.0'\ncopy: *version\n",
    ],
)
def test_yaml_anchors_and_aliases_are_rejected(document: str, tmp_path: Path) -> None:
    path = tmp_path / "aliased.yaml"
    path.write_text(document, encoding="utf-8")

    with pytest.raises(ValueError, match="anchors and aliases"):
        load_plugin_manifest(path)


def test_yaml_root_must_be_a_mapping(tmp_path: Path) -> None:
    path = tmp_path / "sequence.yaml"
    path.write_text("- one\n- two\n", encoding="utf-8")

    with pytest.raises(ValueError, match="mapping"):
        load_plugin_manifest(path)


def test_yaml_manifest_has_a_one_mib_default_limit(tmp_path: Path) -> None:
    path = tmp_path / "oversized.yaml"
    path.write_bytes(b"x" * (1024 * 1024 + 1))

    with pytest.raises(ValueError, match="1 MiB"):
        load_plugin_manifest(path)


def test_yaml_validation_errors_are_propagated(tmp_path: Path) -> None:
    path = tmp_path / "invalid.yaml"
    valid_document = (FIXTURES / "valid-plugin.yaml").read_text(encoding="utf-8")
    path.write_text(
        valid_document.replace('spec_version: "1.0"', 'spec_version: "2.0"', 1),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_plugin_manifest(path)
