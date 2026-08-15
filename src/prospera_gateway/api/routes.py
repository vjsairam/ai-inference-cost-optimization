"""Gateway HTTP endpoints (FR-001, FR-002, FR-005, FR-006, FR-009, FR-010)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import ValidationError

from prospera_gateway.api import schemas
from prospera_gateway.api.app import GatewayState
from prospera_gateway.models import (
    CanonicalChatRequest,
    DataClass,
    ErrorClass,
    NormalizedUsage,
    ProviderError,
    QualityTier,
    RequestContext,
)
from prospera_gateway.routing import RouteDecision
from prospera_gateway.routing.policy import PolicyDenied, select_route
from prospera_gateway.security import (
    AuthenticationFailed,
    TeamAssertionMismatch,
    authenticate,
)

_access_log = logging.getLogger("prospera_gateway.access")

_ERROR_STATUS: dict[ErrorClass, int] = {
    ErrorClass.INVALID_REQUEST: 502,
    ErrorClass.AUTH: 502,
    ErrorClass.RATE_LIMITED: 429,
    ErrorClass.TIMEOUT: 504,
    ErrorClass.PROVIDER_5XX: 502,
    ErrorClass.POLICY_DENIED: 403,
    ErrorClass.STREAM_STARTED_FAILURE: 502,
    ErrorClass.MALFORMED_RESPONSE: 502,
}


def _state(request: Request) -> GatewayState:
    state: GatewayState = request.app.state.gateway
    return state


def _error_response(
    status: int, message: str, code: str, request_id: str | None = None
) -> JSONResponse:
    headers = {"X-Prospera-Request-Id": request_id} if request_id else None
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "type": "invalid_request_error", "code": code}},
        headers=headers,
    )


def _provider_error_response(error: ProviderError, request_id: str) -> JSONResponse:
    status = _ERROR_STATUS[error.error.error_class]
    headers = {"X-Prospera-Request-Id": request_id}
    if error.error.retry_after_seconds is not None:
        headers["Retry-After"] = str(int(error.error.retry_after_seconds))
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "message": error.error.message,
                "type": "provider_error",
                "code": error.error.error_class.value,
            }
        },
        headers=headers,
    )


def _log_request(
    request_id: str,
    team: str,
    workload: str,
    outcome: str,
    provider: str | None,
    rule: str | None,
    latency_seconds: float,
    fallback_count: int = 0,
) -> None:
    """Structured access log: identifiers and labels only, never content (§15.2)."""
    _access_log.info(
        "request",
        extra={
            "request_id": request_id,
            "team": team,
            "workload": workload,
            "outcome": outcome,
            "provider": provider,
            "rule": rule,
            "latency_seconds": round(latency_seconds, 4),
            "fallback_count": fallback_count,
        },
    )


def _record_result_metrics(
    state: GatewayState,
    provider: str,
    model_alias: str,
    team: str,
    usage: NormalizedUsage,
) -> None:
    metrics = state.metrics
    if usage.billed_input_tokens is not None:
        metrics.input_tokens_total.labels(
            provider=provider, model_alias=model_alias, team=team
        ).inc(usage.billed_input_tokens)
    if usage.billed_output_tokens is not None:
        metrics.output_tokens_total.labels(
            provider=provider, model_alias=model_alias, team=team
        ).inc(usage.billed_output_tokens)
    adapter = state.adapters[provider]
    cost = adapter.price(usage, model_alias)
    if cost is not None:
        metrics.record_cost(provider, model_alias, team, cost.amount)


def _record_attempt_errors(state: GatewayState, attempts: list[tuple[str, ErrorClass]]) -> None:
    for provider, error_class in attempts:
        state.metrics.provider_errors_total.labels(
            provider=provider, error_class=error_class.value
        ).inc()


def register_routes(app: FastAPI) -> None:
    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready")
    async def ready(request: Request) -> dict[str, object]:
        state = _state(request)
        return {
            "status": "ready",
            "providers": sorted(state.adapters),
        }

    @app.get("/health/providers")
    async def providers_health(request: Request) -> dict[str, object]:
        state = _state(request)
        checks = await asyncio.gather(
            *(adapter.health() for adapter in state.adapters.values()),
            return_exceptions=True,
        )
        report: dict[str, object] = {}
        for name, check in zip(state.adapters, checks, strict=True):
            if isinstance(check, BaseException):
                report[name] = {"healthy": False, "detail": check.__class__.__name__}
            else:
                report[name] = {"healthy": check.healthy, "detail": check.detail}
        return {"providers": report}

    @app.get("/metrics")
    async def metrics(request: Request) -> Response:
        state = _state(request)
        return Response(
            content=generate_latest(state.metrics.registry),
            media_type=CONTENT_TYPE_LATEST,
        )

    @app.post("/v1/chat/completions")
    async def chat_completions(  # noqa: PLR0911 - one exit per normalized failure mode
        request: Request,
        authorization: str | None = Header(default=None),
        x_prospera_team: str | None = Header(default=None),
        x_prospera_workload: str | None = Header(default=None),
        x_prospera_data_class: str | None = Header(default=None),
        x_prospera_quality_tier: str | None = Header(default=None),
        x_prospera_request_id: str | None = Header(default=None),
    ) -> Response:
        state = _state(request)
        started = time.monotonic()
        request_id = (x_prospera_request_id or schemas.new_request_id())[:64]

        bearer = None
        if authorization and authorization.lower().startswith("bearer "):
            bearer = authorization[len("bearer ") :]
        try:
            identity = authenticate(state.auth, bearer, x_prospera_team)
        except AuthenticationFailed:
            return _error_response(401, "invalid or missing API key", "auth_failed", request_id)
        except TeamAssertionMismatch:
            return _error_response(
                403,
                "X-Prospera-Team does not match the authenticated identity",
                "team_mismatch",
                request_id,
            )
        team = identity.team

        workload = x_prospera_workload or "generic"
        if workload not in state.config.routing.workloads:
            return _error_response(422, "unknown workload", "invalid_workload", request_id)
        if x_prospera_data_class is None or x_prospera_quality_tier is None:
            return _error_response(
                422,
                "X-Prospera-Data-Class and X-Prospera-Quality-Tier are required",
                "missing_metadata",
                request_id,
            )
        try:
            data_class = DataClass(x_prospera_data_class)
            quality_tier = QualityTier(x_prospera_quality_tier)
        except ValueError:
            return _error_response(
                422, "invalid data class or quality tier", "invalid_metadata", request_id
            )

        try:
            body = await request.json()
        except ValueError:
            return _error_response(
                400, "request body is not valid JSON", "invalid_json", request_id
            )
        try:
            wire = schemas.ChatCompletionRequest.model_validate(body)
            canonical = schemas.to_canonical(wire, workload, data_class, quality_tier, request_id)
        except (ValidationError, schemas.RequestValidationFailed) as exc:
            return _error_response(400, f"invalid request: {exc}", "invalid_request", request_id)

        try:
            decision = select_route(state.config.routing, data_class, workload, quality_tier)
        except PolicyDenied as exc:
            state.metrics.policy_denied_total.labels(reason="no_permitted_route").inc()
            state.metrics.requests_total.labels(
                provider="none",
                model_alias=wire.model,
                workload=workload,
                team=team,
                outcome="policy_denied",
            ).inc()
            _log_request(
                request_id,
                team,
                workload,
                "policy_denied",
                None,
                None,
                time.monotonic() - started,
            )
            return _error_response(403, exc.reason, "policy_denied", request_id)
        state.metrics.routing_decisions_total.labels(
            route=decision.rule_name, reason="matched"
        ).inc()

        now = datetime.now(UTC)
        ctx = RequestContext(
            request_id=request_id,
            started_at=now,
            deadline_at=now
            + timedelta(seconds=state.config.routing.timeouts.global_request_deadline),
        )
        created_epoch = int(now.timestamp())

        if wire.stream:
            return await _streamed(
                state,
                canonical,
                ctx,
                decision,
                wire.model,
                team,
                workload,
                request_id,
                created_epoch,
                started,
            )
        return await _non_streamed(
            state,
            canonical,
            ctx,
            decision,
            wire.model,
            team,
            workload,
            request_id,
            created_epoch,
            started,
        )


async def _non_streamed(
    state: GatewayState,
    canonical: CanonicalChatRequest,
    ctx: RequestContext,
    decision: RouteDecision,
    model_alias: str,
    team: str,
    workload: str,
    request_id: str,
    created_epoch: int,
    started: float,
) -> Response:
    try:
        outcome = await state.executor.chat(canonical, ctx, decision)
    except ProviderError as error:
        provider = decision.primary
        state.metrics.provider_errors_total.labels(
            provider=provider, error_class=error.error.error_class.value
        ).inc()
        state.metrics.requests_total.labels(
            provider=provider,
            model_alias=model_alias,
            workload=workload,
            team=team,
            outcome=error.error.error_class.value,
        ).inc()
        _log_request(
            request_id,
            team,
            workload,
            error.error.error_class.value,
            provider,
            decision.rule_name,
            time.monotonic() - started,
        )
        return _provider_error_response(error, request_id)

    failed = [
        (attempt.provider, attempt.error_class)
        for attempt in outcome.attempts
        if attempt.error_class is not None
    ]
    _record_attempt_errors(state, failed)
    for index, (from_provider, error_class) in enumerate(failed):
        to_provider = outcome.attempts[index + 1].provider
        state.metrics.fallback_total.labels(
            from_provider=from_provider,
            to_provider=to_provider,
            reason=error_class.value,
        ).inc()
    latency = time.monotonic() - started
    state.metrics.request_latency_seconds.labels(
        provider=outcome.provider, workload=workload
    ).observe(latency)
    state.metrics.requests_total.labels(
        provider=outcome.provider,
        model_alias=model_alias,
        workload=workload,
        team=team,
        outcome="success",
    ).inc()
    _record_result_metrics(state, outcome.provider, model_alias, team, outcome.result.usage)
    _log_request(
        request_id,
        team,
        workload,
        "success",
        outcome.provider,
        decision.rule_name,
        latency,
        outcome.fallback_count,
    )
    payload = schemas.completion_response(request_id, created_epoch, model_alias, outcome.result)
    return JSONResponse(
        content=payload,
        headers={
            "X-Prospera-Request-Id": request_id,
            "X-Prospera-Route": decision.rule_name,
            "X-Prospera-Provider": outcome.provider,
        },
    )


async def _streamed(
    state: GatewayState,
    canonical: CanonicalChatRequest,
    ctx: RequestContext,
    decision: RouteDecision,
    model_alias: str,
    team: str,
    workload: str,
    request_id: str,
    created_epoch: int,
    started: float,
) -> Response:
    async def event_stream() -> AsyncIterator[str]:
        first_content = True
        provider_used: str | None = None
        outcome = "success"
        try:
            async for provider, fallback_index, chunk in state.executor.stream(
                canonical, ctx, decision
            ):
                if provider_used is None:
                    provider_used = provider
                    if fallback_index > 0:
                        state.metrics.fallback_total.labels(
                            from_provider=decision.primary,
                            to_provider=provider,
                            reason="pre_stream_failure",
                        ).inc()
                if chunk.delta and first_content:
                    first_content = False
                    state.metrics.ttft_seconds.labels(provider=provider, workload=workload).observe(
                        time.monotonic() - started
                    )
                delta_text = "".join(part.text or "" for part in chunk.delta)
                if chunk.is_final:
                    payload = schemas.completion_chunk(
                        request_id,
                        created_epoch,
                        model_alias,
                        delta_text or None,
                        chunk.finish_reason or "stop",
                        chunk.usage,
                    )
                    yield f"data: {json.dumps(payload)}\n\n"
                    if chunk.usage is not None:
                        _record_result_metrics(state, provider, model_alias, team, chunk.usage)
                elif delta_text:
                    payload = schemas.completion_chunk(
                        request_id, created_epoch, model_alias, delta_text
                    )
                    yield f"data: {json.dumps(payload)}\n\n"
            yield "data: [DONE]\n\n"
        except ProviderError as error:
            outcome = error.error.error_class.value
            provider_for_metric = provider_used or decision.primary
            state.metrics.provider_errors_total.labels(
                provider=provider_for_metric, error_class=outcome
            ).inc()
            yield (
                "data: "
                + json.dumps(
                    {
                        "error": {
                            "message": error.error.message,
                            "type": "provider_error",
                            "code": outcome,
                        }
                    }
                )
                + "\n\n"
            )
        finally:
            latency = time.monotonic() - started
            final_provider = provider_used or decision.primary
            state.metrics.request_latency_seconds.labels(
                provider=final_provider, workload=workload
            ).observe(latency)
            state.metrics.requests_total.labels(
                provider=final_provider,
                model_alias=model_alias,
                workload=workload,
                team=team,
                outcome=outcome,
            ).inc()
            _log_request(
                request_id,
                team,
                workload,
                outcome,
                final_provider,
                decision.rule_name,
                latency,
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "X-Prospera-Request-Id": request_id,
            "X-Prospera-Route": decision.rule_name,
            "Cache-Control": "no-cache",
        },
    )
