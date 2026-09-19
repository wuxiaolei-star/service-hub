"""Plugin registry resolution and dependency graph tests."""

from __future__ import annotations

import pytest
from hub_server.kernel.protocol import BaseFeaturePlugin
from hub_server.kernel.registry import (
    PluginCycleError,
    PluginDependencyError,
    resolve_plugins,
)


def _plugin(name: str, depends_on: tuple[str, ...] = (), enabled: bool = True) -> BaseFeaturePlugin:
    p = BaseFeaturePlugin()
    p.name = name
    p.depends_on = depends_on
    p.default_enabled = enabled
    return p


def test_all_default_enabled_when_none() -> None:
    catalog = {"a": _plugin("a"), "b": _plugin("b", enabled=False), "c": _plugin("c")}
    result = resolve_plugins(catalog, None)
    names = [p.name for p in result]
    assert "a" in names and "c" in names and "b" not in names


def test_explicit_enabled_list() -> None:
    catalog = {"a": _plugin("a"), "b": _plugin("b"), "c": _plugin("c", enabled=False)}
    result = resolve_plugins(catalog, ["a", "c"])
    names = [p.name for p in result]
    assert "a" in names and "c" in names and "b" not in names


def test_unknown_plugin_rejected() -> None:
    catalog = {"a": _plugin("a")}
    with pytest.raises(PluginDependencyError, match="未知插件"):
        resolve_plugins(catalog, ["ghost"])


def test_hard_dependency_must_be_explicitly_enabled() -> None:
    """files depends_on auth; enabling files without auth → rejected."""
    catalog = {
        "auth": _plugin("auth"),
        "files": _plugin("files", depends_on=("auth",)),
    }
    with pytest.raises(PluginDependencyError, match="auth"):
        resolve_plugins(catalog, ["files"])


def test_hard_dependency_satisfied_when_explicitly_enabled() -> None:
    catalog = {
        "auth": _plugin("auth"),
        "files": _plugin("files", depends_on=("auth",)),
    }
    result = resolve_plugins(catalog, ["auth", "files"])
    names = [p.name for p in result]
    assert "auth" in names and "files" in names


def test_transitive_dependency_must_be_explicitly_enabled() -> None:
    catalog = {
        "auth": _plugin("auth"),
        "files": _plugin("files", depends_on=("auth",)),
        "jobs": _plugin("jobs", depends_on=("files",)),
    }
    with pytest.raises(PluginDependencyError, match="files"):
        resolve_plugins(catalog, ["jobs"])


def test_transitive_dependency_fully_explicit() -> None:
    catalog = {
        "auth": _plugin("auth"),
        "files": _plugin("files", depends_on=("auth",)),
        "jobs": _plugin("jobs", depends_on=("files",)),
    }
    result = resolve_plugins(catalog, ["auth", "files", "jobs"])
    names = [p.name for p in result]
    assert "auth" in names and "files" in names and "jobs" in names


def test_cycle_detected() -> None:
    catalog = {
        "a": _plugin("a", depends_on=("b",)),
        "b": _plugin("b", depends_on=("a",)),
    }
    with pytest.raises(PluginCycleError):
        resolve_plugins(catalog, ["a", "b"])


def test_topology_order_respects_dependencies() -> None:
    catalog = {
        "auth": _plugin("auth"),
        "files": _plugin("files", depends_on=("auth",)),
        "jobs": _plugin("jobs", depends_on=("auth", "files")),
    }
    result = resolve_plugins(catalog, ["auth", "files", "jobs"])
    names = [p.name for p in result]
    assert names.index("auth") < names.index("files") < names.index("jobs")
