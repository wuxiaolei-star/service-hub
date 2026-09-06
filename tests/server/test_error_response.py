import pytest
from hub_server.errors import ApiError, ErrorResponse
from pydantic import ValidationError


def test_error_response_rejects_success_true() -> None:
    """Failure payloads cannot be serialized as successful responses."""
    with pytest.raises(ValidationError):
        ErrorResponse(
            success=True,
            error=ApiError(code="UNEXPECTED_ERROR", message="Unexpected failure"),
        )
