"""Safe loading for plugin YAML manifests."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import yaml  # type: ignore[import-untyped]

from .plugin_manifest import PluginManifest

DEFAULT_MAX_MANIFEST_SIZE_BYTES = 1024 * 1024


class _UniqueKeySafeLoader(yaml.SafeLoader):  # type: ignore[misc]
    """SafeLoader variant that rejects duplicate mapping keys."""

    def construct_mapping(
        self, node: yaml.MappingNode, deep: bool = False
    ) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in seen
                seen.add(key)
            except TypeError as error:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found an unhashable mapping key",
                    key_node.start_mark,
                ) from error
            if duplicate:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate mapping key {key!r}",
                    key_node.start_mark,
                )
        return cast(dict[Any, Any], super().construct_mapping(node, deep=deep))


def _reject_anchors_and_aliases(document: str) -> None:
    for token in yaml.scan(document):
        if isinstance(token, (yaml.tokens.AnchorToken, yaml.tokens.AliasToken)):
            raise ValueError("YAML anchors and aliases are not allowed")


def load_plugin_manifest(
    path: Path, *, max_size_bytes: int = DEFAULT_MAX_MANIFEST_SIZE_BYTES
) -> PluginManifest:
    """Read and validate a UTF-8 plugin manifest with bounded YAML features."""
    if max_size_bytes <= 0:
        raise ValueError("maximum plugin manifest size must be positive")
    with path.open("rb") as manifest_file:
        content = manifest_file.read(max_size_bytes + 1)
    if len(content) > max_size_bytes:
        raise ValueError("plugin manifest exceeds the 1 MiB size limit")

    document = content.decode("utf-8")
    _reject_anchors_and_aliases(document)
    loaded: object = yaml.load(document, Loader=_UniqueKeySafeLoader)
    if not isinstance(loaded, Mapping):
        raise ValueError("plugin manifest root must be a mapping")
    return PluginManifest.model_validate(loaded)
