"""Public provider-neutral domain model surface."""

from prospera_gateway.models.domain import (
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
from prospera_gateway.models.enums import (
    DataClass,
    ErrorClass,
    MessageRole,
    QualityTier,
    UsageSource,
)
from prospera_gateway.models.errors import (
    NormalizedError,
    ProviderError,
    normalize_http_error,
    normalized_error,
)

__all__ = [
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
