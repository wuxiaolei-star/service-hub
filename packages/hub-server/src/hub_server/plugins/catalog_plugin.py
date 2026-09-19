"""Catalog feature plugin: Compute Plugin registry, builds, and environments."""

from __future__ import annotations

from hub_server.kernel.protocol import BaseFeaturePlugin
from hub_server.models import (
    Environment,
    Plugin,
    PluginBuild,
    PluginVersion,
    RunnerOperation,
)
from hub_server.routers.plugins import router as plugins_router
from hub_server.routers.registry import router as registry_router


class CatalogPlugin(BaseFeaturePlugin):
    """Wrap the Compute Plugin catalog domain as one swappable FeaturePlugin.

    This is a *kernel* plugin that owns the management plane of business
    plugins — ``models.Plugin``/``PluginVersion``/``PluginBuild``/``Environment``/
    ``RunnerOperation`` plus the ``routers.plugins`` and ``routers.registry``
    surfaces. It must never be confused with the business plugins (Compute
    Plugins) it registers; the two concepts never mix.

    No ``depends_on`` is declared even though ``jobs`` rows reference
    ``plugin_builds``: the FK points from jobs into this domain (with
    ``ondelete=RESTRICT``), so the dependency runs the other way — the jobs
    domain is the one that requires catalog to come up first.
    """

    name = "catalog"
    models = (Plugin, PluginVersion, PluginBuild, Environment, RunnerOperation)
    routers = (plugins_router, registry_router)
