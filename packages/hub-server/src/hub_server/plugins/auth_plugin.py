"""Auth feature plugin: login, sessions, user management, API keys."""

from __future__ import annotations

from hub_server.kernel.protocol import BaseFeaturePlugin
from hub_server.models import ApiKeyRecord, LoginAttemptRecord, SessionRecord, UserRecord
from hub_server.routers.auth import router as auth_router
from hub_server.routers.users import key_router as api_keys_router
from hub_server.routers.users import router as users_router


class AuthPlugin(BaseFeaturePlugin):
    name = "auth"
    depends_on = ()
    default_enabled = True
    models = (UserRecord, SessionRecord, ApiKeyRecord, LoginAttemptRecord)
    routers = (auth_router, users_router, api_keys_router)
