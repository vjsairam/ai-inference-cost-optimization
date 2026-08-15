"""Shared enumerations for provider-neutral contracts."""

from enum import StrEnum


class DataClass(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class QualityTier(StrEnum):
    ECONOMY = "economy"
    BALANCED = "balanced"
    PREMIUM = "premium"


class ErrorClass(StrEnum):
    INVALID_REQUEST = "invalid_request"
    AUTH = "auth"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    PROVIDER_5XX = "provider_5xx"
    POLICY_DENIED = "policy_denied"
    STREAM_STARTED_FAILURE = "stream_started_failure"
    MALFORMED_RESPONSE = "malformed_response"


class UsageSource(StrEnum):
    PROVIDER_REPORTED = "provider_reported"
    TOKENIZER_ESTIMATED = "tokenizer_estimated"
    UNAVAILABLE = "unavailable"


class MessageRole(StrEnum):
    SYSTEM = "system"
    DEVELOPER = "developer"
    USER = "user"
    MODEL = "model"
    TOOL = "tool"
