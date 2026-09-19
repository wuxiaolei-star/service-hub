"""Quotas feature plugin: storage and concurrency limit enforcement."""

from __future__ import annotations

from hub_server.kernel.protocol import BaseFeaturePlugin
from hub_server.settings import QuotasSettings


class QuotasPlugin(BaseFeaturePlugin):
    """Declare kernel ownership of the quota enforcement domain.

    No own router: quota checks run inside the files/jobs/users endpoints
    (upload caps, job-creation limits, usage summaries) through
    ``services.quotas.QuotaService``. Global limits come from the ``quotas``
    settings section — ``settings_model`` records that this plugin owns that
    section — while per-user overrides live as columns on ``UserRecord``, so
    no models are claimed here either.
    """

    name = "quotas"
    settings_model = QuotasSettings
