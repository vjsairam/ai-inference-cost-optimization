.PHONY: bootstrap lint test test-contract test-integration \
	local-up local-smoke tf-plan cloud-up deploy smoke benchmark report \
	cloud-down verify-destroy

export UV_CACHE_DIR ?= /tmp/prospera-uv-cache

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
	@echo "M0 compatibility target: running unit and contract tests until integration tests exist in M1."
	uv run pytest tests/unit tests/contract

local-up local-smoke:
	@echo "$@ not available until M1 (see TECHNICAL_SPEC.md §18)"
	@exit 2

benchmark report:
	@echo "$@ not available until M2 (see TECHNICAL_SPEC.md §18)"
	@exit 2

tf-plan cloud-up deploy smoke cloud-down verify-destroy:
	@echo "$@ not available until M4 (see TECHNICAL_SPEC.md §18)"
	@exit 2
