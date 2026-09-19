"""Metrics feature plugin: Prometheus exposition built from live DB counts."""

from __future__ import annotations

from hub_server.kernel.protocol import BaseFeaturePlugin


class MetricsPlugin(BaseFeaturePlugin):
    """Declare kernel ownership of the metrics domain.

    No own router yet: the ``GET /system/metrics`` endpoint stays mounted by
    ``routers.system`` (which also serves the version and backup endpoints),
    and the exposition is rendered by ``services.metrics`` from rows owned by
    several other domains (jobs, files, users, audit, plugin builds), so no
    models are claimed here either. This declaration only records which
    FeaturePlugin the metrics surface belongs to.
    """

    name = "metrics"
