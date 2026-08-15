import pytest

from prospera_gateway.models import ErrorClass, normalize_http_error, normalized_error


@pytest.mark.parametrize(
    ("status", "expected_class", "retry", "fallback"),
    [
        (400, ErrorClass.INVALID_REQUEST, False, False),
        (401, ErrorClass.AUTH, False, False),
        (403, ErrorClass.AUTH, False, False),
        (404, ErrorClass.INVALID_REQUEST, False, False),
        (408, ErrorClass.TIMEOUT, True, True),
        (429, ErrorClass.RATE_LIMITED, True, True),
        (500, ErrorClass.PROVIDER_5XX, True, True),
        (503, ErrorClass.PROVIDER_5XX, True, True),
    ],
)
def test_http_error_mapping(
    status: int,
    expected_class: ErrorClass,
    retry: bool,
    fallback: bool,
) -> None:
    error = normalize_http_error(status, "failure")

    assert error.error_class is expected_class
    assert error.retry_eligible is retry
    assert error.fallback_eligible is fallback


@pytest.mark.parametrize(
    "error_class",
    [
        ErrorClass.POLICY_DENIED,
        ErrorClass.STREAM_STARTED_FAILURE,
        ErrorClass.MALFORMED_RESPONSE,
    ],
)
def test_non_retryable_non_http_errors(error_class: ErrorClass) -> None:
    error = normalized_error(error_class, "failure")
    assert error.retry_eligible is False
    assert error.fallback_eligible is False


def test_retry_after_is_preserved_for_rate_limit() -> None:
    error = normalize_http_error(429, "slow down", retry_after_seconds=2.5)
    assert error.retry_after_seconds == 2.5


def test_non_error_http_status_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        normalize_http_error(200, "not an error")
