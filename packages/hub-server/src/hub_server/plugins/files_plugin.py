"""Files feature plugin: upload, download, and FileRecord lifecycle."""

from __future__ import annotations

from hub_server.kernel.protocol import BaseFeaturePlugin
from hub_server.models import FileRecord
from hub_server.routers.files import router as files_router


class FilesPlugin(BaseFeaturePlugin):
    """Wrap the files domain as one swappable FeaturePlugin.

    ``depends_on = ("auth",)`` encodes the ownership FK: every FileRecord row
    points at its uploading User (``owner_user_id -> users.id``), so the auth
    domain must come up first.
    """

    name = "files"
    depends_on = ("auth",)
    models = (FileRecord,)
    default_enabled = True
    routers = (files_router,)
