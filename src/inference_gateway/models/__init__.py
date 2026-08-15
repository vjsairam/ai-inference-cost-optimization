"""Public provider-neutral domain model surface."""

from inference_gateway.models.domain import (
    CanonicalChatRequest,
    CanonicalContentPart,
    CanonicalMessage,
    Money,
    NormalizedUsage,
    ProviderCapabilities,
    ProviderChunk,
    ProviderHealth,
    ProviderResult,
    RequestContext,
    RequestMetadata,
)
from inference_gateway.models.enums import (
    DataClass,
    ErrorClass,
    MessageRole,
    QualityTier,
    UsageSource,
)
from inference_gateway.models.errors import (
    AttemptOutcome,
    NormalizedError,
    ProviderError,
    normalize_http_error,
    normalized_error,
)

__all__ = [
    "AttemptOutcome",
    "CanonicalChatRequest",
    "CanonicalContentPart",
    "CanonicalMessage",
    "DataClass",
    "ErrorClass",
    "MessageRole",
    "Money",
    "NormalizedError",
    "NormalizedUsage",
    "ProviderCapabilities",
    "ProviderChunk",
    "ProviderError",
    "ProviderHealth",
    "ProviderResult",
    "QualityTier",
    "RequestContext",
    "RequestMetadata",
    "UsageSource",
    "normalize_http_error",
    "normalized_error",
]
