.PHONY: bootstrap tools-check lint test test-contract test-integration \
	local-up local-smoke fault-evidence benchmark-local tf-plan cloud-up deploy smoke benchmark report \
	cloud-down verify-destroy

export UV_CACHE_DIR ?= /tmp/gateway-uv-cache

bootstrap:
	uv sync --no-install-project
	uv sync --no-build-isolation

tools-check:
	./scripts/bootstrap.sh

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
	./scripts/local-up.sh

local-smoke:
	./scripts/local-smoke.sh

fault-evidence:
	uv run python -m inference_gateway.fault_evidence

benchmark-local:
	@test -n "$(SCENARIO)" || (echo "SCENARIO is required" && exit 2)
	uv run python -m inference_gateway.benchmark run --scenario "$(SCENARIO)" --base-url local-mock://in-process

report:
	@test -n "$(RUN_ID)" || (echo "RUN_ID is required" && exit 2)
	uv run python -m inference_gateway.benchmark report --run-dir "results/local/$(RUN_ID)"

benchmark:
	@echo "benchmark is gated until M4 (see TECHNICAL_SPEC.md §18)"
	@exit 2

tf-plan:
	@test "$(ENV)" = "aws-lab" || (echo "ENV=aws-lab is required" && exit 2)
	./scripts/cloud-up.sh --plan-only

cloud-up:
	@test "$(ENV)" = "aws-lab" || (echo "ENV=aws-lab is required" && exit 2)
	./scripts/cloud-up.sh $(CONFIRM)

deploy:
	@test "$(ENV)" = "aws-lab" || (echo "ENV=aws-lab is required" && exit 2)
	./scripts/deploy.sh

smoke:
	@test "$(ENV)" = "aws-lab" || (echo "ENV=aws-lab is required" && exit 2)
	./scripts/smoke.sh

cloud-down:
	@test "$(ENV)" = "aws-lab" || (echo "ENV=aws-lab is required" && exit 2)
	./scripts/cloud-down.sh

verify-destroy:
	@test "$(ENV)" = "aws-lab" || (echo "ENV=aws-lab is required" && exit 2)
	./scripts/verify-destroy.sh
