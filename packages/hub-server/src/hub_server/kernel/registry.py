"""Plugin discovery, dependency resolution, and lifecycle management."""

from __future__ import annotations

import logging

from hub_server.kernel.protocol import BaseFeaturePlugin

_LOGGER = logging.getLogger(__name__)


class PluginDependencyError(Exception):
    """Raised when the enabled plugin set has unresolvable hard dependencies."""

    def __init__(
        self, message: str | list[tuple[str, str]]
    ) -> None:
        if isinstance(message, list):
            lines = [
                f"  {dependent} requires {required}"
                for dependent, required in message
            ]
            message = "启用插件集合缺少必需的依赖:\n" + "\n".join(lines)
        super().__init__(message)


class PluginCycleError(Exception):
    """Raised when the plugin dependency graph contains a cycle."""


def resolve_plugins(
    catalog: dict[str, BaseFeaturePlugin],
    enabled: list[str] | None,
) -> list[BaseFeaturePlugin]:
    """Resolve the enabled plugin set to a topologically sorted list.

    ``enabled=None`` means "all plugins with default_enabled=True".  Hard
    dependencies must already be in the enabled set — the kernel does NOT
    auto-expand them (strict, matching settings.py behaviour).  Cycles cause
    :class:`PluginCycleError`.
    """
    if enabled is None:
        chosen = {name for name, p in catalog.items() if p.default_enabled}
    else:
        chosen = set(enabled)

    unknown = chosen - catalog.keys()
    if unknown:
        raise PluginDependencyError(f"未知插件: {sorted(unknown)}")

    # Validate hard dependency closure: every direct dependency of an enabled
    # plugin must itself be enabled.  No silent auto-expansion.
    missing_edges: list[tuple[str, str]] = []
    for name in chosen:
        for dep in catalog[name].depends_on:
            if dep not in chosen:
                missing_edges.append((name, dep))
    if missing_edges:
        raise PluginDependencyError(
            f"缺少必需的依赖: {missing_edges}"
        )

    # Topological sort (Kahn, stable by name).
    in_degree: dict[str, int] = {name: 0 for name in chosen}
    adj: dict[str, list[str]] = {name: [] for name in chosen}
    for name in chosen:
        for dep in catalog[name].depends_on:
            if dep in chosen:
                adj[dep].append(name)
                in_degree[name] += 1

    queue = sorted(name for name, deg in in_degree.items() if deg == 0)
    order: list[str] = []
    while queue:
        name = queue.pop(0)
        order.append(name)
        for dependent in sorted(adj[name]):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    if len(order) != len(chosen):
        remaining = sorted(chosen - set(order))
        raise PluginCycleError(f"插件依赖存在环: {remaining}")

    return [catalog[name] for name in order]
