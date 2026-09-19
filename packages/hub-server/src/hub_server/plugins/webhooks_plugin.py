"""Webhooks feature plugin: job callback delivery, inspection, and replay."""

from __future__ import annotations

from hub_server.kernel.protocol import BaseFeaturePlugin, SchedulerTaskSpec
from hub_server.models import JobCallback, WebhookDelivery
from hub_server.routers.webhooks import router as webhooks_router
from hub_server.services.webhooks import deliver_due

deliver_callbacks_task = SchedulerTaskSpec(
    name="deliver_callbacks",
    run=deliver_due,
    processes=("scheduler",),
)


class WebhooksPlugin(BaseFeaturePlugin):
    """Wrap the webhook callback domain as one swappable FeaturePlugin.

    ``depends_on = ("jobs",)`` encodes the callback FK: every JobCallback row
    points at a Job and delivery only fires once that Job reaches a terminal
    status, so the jobs domain must come up first.
    """

    name = "webhooks"
    depends_on = ("jobs",)
    models = (JobCallback, WebhookDelivery)
    default_enabled = True
    routers = (webhooks_router,)
    scheduler_tasks = (deliver_callbacks_task,)
