"""Backup feature plugin: crash-consistent SQLite and data-directory archives."""

from __future__ import annotations

from hub_server.kernel.protocol import BaseFeaturePlugin


class BackupPlugin(BaseFeaturePlugin):
    """Declare kernel ownership of the backup domain.

    The ``POST /system/backup`` endpoint stays mounted by ``routers.system``
    for now; archive creation and ``backup_keep`` rotation live in
    ``services.backup``, tuned by the retention settings rather than a section
    of its own. No routers or models are claimed here — this declaration only
    records which FeaturePlugin the backup surface belongs to.
    """

    name = "backup"
