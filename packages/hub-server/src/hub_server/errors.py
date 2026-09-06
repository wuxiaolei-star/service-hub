"""Shared API error schemas for future Hub routes."""

from typing import Any, Literal

from pydantic import BaseModel


class ApiError(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    success: Literal[False] = False
    error: ApiError


class HubError(Exception):
    """A stable, client-safe error raised by Hub service code."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


class UploadTooLargeError(HubError):
    """An upload stream exceeded the configured maximum size."""

    def __init__(self, *, max_size_bytes: int) -> None:
        super().__init__(
            code="UPLOAD_TOO_LARGE",
            message="上传文件超过大小限制",
            status_code=413,
            details={"max_size_bytes": max_size_bytes},
        )
