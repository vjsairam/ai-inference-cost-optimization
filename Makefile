.PHONY: bootstrap lint test test-contract test-integration \
	local-up local-smoke tf-plan cloud-up deploy smoke benchmark report \
	cloud-down verify-destroy

export UV_CACHE_DIR ?= /tmp/gateway-uv-cache

bootstrap:
	uv sync --no-install-project
	uv sync --no-build-isolation

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

test:
	uv run pytest tests/unit

test-contract:
	uv run pytest tests/contract

test-integration:
	uv run pytest tests/integration

local-up:
	uv run uvicorn --factory inference_gateway.main:build_app --host 127.0.0.1 --port 8080

local-smoke:
	@echo "local-smoke not available until M3 (see TECHNICAL_SPEC.md §18)"
	@exit 2

benchmark report:
	@echo "$@ not available until M2 (see TECHNICAL_SPEC.md §18)"
	@exit 2

tf-plan cloud-up deploy smoke cloud-down verify-destroy:
	@echo "$@ not available until M4 (see TECHNICAL_SPEC.md §18)"
	@exit 2
