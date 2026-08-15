"""Local gateway entrypoint (make local-up)."""

from __future__ import annotations

import json
import logging
import os

from fastapi import FastAPI

from inference_gateway.api import create_app
from inference_gateway.config import load_gateway_config
from inference_gateway.security import load_auth_config

_LOG_FIELDS = (
    "request_id",
    "team",
    "workload",
    "outcome",
    "provider",
    "rule",
    "latency_seconds",
    "fallback_count",
)


class JsonLogFormatter(logging.Formatter):
    """Structured JSON logs; carries identifiers and labels, never content."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for field in _LOG_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        return json.dumps(payload, sort_keys=True)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


def build_app() -> FastAPI:
    configure_logging()
    providers_path = os.environ.get("GATEWAY_PROVIDERS_CONFIG", "config/providers.yaml")
    routing_path = os.environ.get("GATEWAY_ROUTING_CONFIG", "policy/routing.yaml")
    auth_path = os.environ.get("GATEWAY_AUTH_CONFIG", "config/auth.yaml")
    config = load_gateway_config(providers_path, routing_path)
    auth = load_auth_config(auth_path)
    return create_app(config, auth)
