"""Canonical provider error normalization."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from inference_gateway.models.enums import ErrorClass

_ELIGIBILITY: dict[ErrorClass, tuple[bool, bool]] = {
    ErrorClass.INVALID_REQUEST: (False, False),
    ErrorClass.AUTH: (False, False),
    ErrorClass.RATE_LIMITED: (True, True),
    ErrorClass.TIMEOUT: (True, True),
    ErrorClass.PROVIDER_5XX: (True, True),
    ErrorClass.POLICY_DENIED: (False, False),
    ErrorClass.STREAM_STARTED_FAILURE: (False, False),
    ErrorClass.MALFORMED_RESPONSE: (False, False),
}


class NormalizedError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    error_class: ErrorClass
    message: str = Field(min_length=1)
    http_status: int | None = Field(default=None, ge=100, le=599)
    retry_eligible: bool
    fallback_eligible: bool
    retry_after_seconds: float | None = Field(default=None, ge=0)


def normalized_error(
    error_class: ErrorClass,
    message: str,
    *,
    http_status: int | None = None,
    retry_after_seconds: float | None = None,
) -> NormalizedError:
    retry_eligible, fallback_eligible = _ELIGIBILITY[error_class]
    return NormalizedError(
        error_class=error_class,
        message=message,
        http_status=http_status,
        retry_eligible=retry_eligible,
        fallback_eligible=fallback_eligible,
        retry_after_seconds=retry_after_seconds,
    )


def normalize_http_error(
    status: int,
    message: str,
    *,
    retry_after_seconds: float | None = None,
) -> NormalizedError:
    if status == 408:
        error_class = ErrorClass.TIMEOUT
    elif status == 429:
        error_class = ErrorClass.RATE_LIMITED
    elif status in {401, 403}:
        error_class = ErrorClass.AUTH
    elif 400 <= status < 500:
        error_class = ErrorClass.INVALID_REQUEST
    elif 500 <= status < 600:
        error_class = ErrorClass.PROVIDER_5XX
    else:
        raise ValueError(f"unsupported HTTP error status: {status}")
    return normalized_error(
        error_class,
        message,
        http_status=status,
        retry_after_seconds=retry_after_seconds,
    )


class AttemptOutcome(BaseModel):
    """One provider attempt within a routed request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    error_class: ErrorClass | None = None
    retry_after_seconds: float | None = None


class ProviderError(Exception):
    """Raised by adapters after an upstream failure has been normalized.

    ``attempts`` carries the per-provider attempt history when the error is
    re-raised by the fallback executor, so telemetry can attribute every
    failure to the provider that produced it.
    """

    def __init__(
        self,
        error: NormalizedError,
        attempts: tuple[AttemptOutcome, ...] = (),
    ) -> None:
        super().__init__(error.message)
        self.error = error
        self.attempts = attempts
