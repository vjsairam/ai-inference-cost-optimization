"""Provider adapter contract."""

from collections.abc import AsyncIterator
from typing import Protocol

from inference_gateway.models import (
    CanonicalChatRequest,
    Money,
    NormalizedUsage,
    ProviderCapabilities,
    ProviderChunk,
    ProviderHealth,
    ProviderResult,
    RequestContext,
)


class ProviderAdapter(Protocol):
    name: str
    capabilities: ProviderCapabilities

    async def chat(
        self,
        request: CanonicalChatRequest,
        ctx: RequestContext,
    ) -> ProviderResult: ...

    def stream(
        self,
        request: CanonicalChatRequest,
        ctx: RequestContext,
    ) -> AsyncIterator[ProviderChunk]: ...

    async def health(self) -> ProviderHealth: ...

    def price(self, usage: NormalizedUsage, model: str) -> Money | None: ...
