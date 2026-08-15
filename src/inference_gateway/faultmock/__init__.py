"""Deterministic wire-level upstream fault service."""

from inference_gateway.faultmock.app import FaultMockConfig, FaultScenario, create_faultmock_app

__all__ = ["FaultMockConfig", "FaultScenario", "create_faultmock_app"]
