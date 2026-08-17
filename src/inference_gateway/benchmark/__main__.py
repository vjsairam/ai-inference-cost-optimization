"""Command-line entrypoint for benchmark runs and local reports."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

import httpx

from inference_gateway.api import create_app
from inference_gateway.benchmark.comparison import build_comparison, write_comparison_markdown
from inference_gateway.benchmark.datasets import load_dataset
from inference_gateway.benchmark.harness import BenchmarkHarness
from inference_gateway.benchmark.local import local_adapters
from inference_gateway.benchmark.models import load_scenario
from inference_gateway.benchmark.report import build_report
from inference_gateway.config import load_gateway_config
from inference_gateway.security import ApiKeyEntry, AuthConfig, hash_api_key


def _repository_root() -> Path:
    return Path.cwd().resolve()


def _resolve_path(value: str) -> Path:
    return Path(value).resolve()


async def _run(args: argparse.Namespace) -> None:
    root = _repository_root()
    scenario_path = _resolve_path(args.scenario)
    scenario = load_scenario(scenario_path)
    client: httpx.AsyncClient | None = None
    api_key = args.api_key or os.environ.get("GATEWAY_API_KEY", "")
    if args.base_url == "local-mock://in-process":
        api_key = "local-benchmark-key"
        dataset = load_dataset(scenario.dataset, root=root)
        config = load_gateway_config(
            root / scenario.pricing_config,
            root / scenario.timeout_config,
        )
        auth = AuthConfig(keys=[ApiKeyEntry(sha256=hash_api_key(api_key), team="local-benchmark")])
        app = create_app(config, auth, adapters=local_adapters(dataset))
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://gateway.local",
        )
    elif not api_key:
        raise SystemExit("--api-key or GATEWAY_API_KEY is required for an HTTP benchmark")
    try:
        harness = BenchmarkHarness(
            repository_root=root,
            scenario_path=scenario_path,
            base_url=args.base_url,
            api_key=api_key,
            client=client,
            allow_dirty=args.allow_dirty,
        )
        run_dir = await harness.run(scenario)
        summary = build_report(run_dir, root)
    finally:
        if client is not None:
            await client.aclose()
    view_a = summary["cost"]["views"]["view_a"]
    if scenario.provider_mode == "hybrid":
        selected_cost = summary["cost"]["hybrid_combined_view_a"]
    else:
        selected = "managed" if scenario.provider_mode == "managed" else "private"
        selected_cost = view_a[selected]
    headline = {
        "run_id": summary["run_id"],
        "run_dir": str(run_dir),
        "requests": summary["requests"],
        "correct_rate": summary["quality"]["quality_rate"],
        "routing_mix": summary["routing_mix"],
        "view_a_cost_per_correct_task_usd": selected_cost.cost_per_correct_task_usd,
    }
    print(json.dumps(headline, default=str, sort_keys=True))


def _report(args: argparse.Namespace) -> None:
    root = _repository_root()
    run_dir = Path(args.run_dir).resolve()
    summary = build_report(run_dir, root)
    print(
        json.dumps(
            {
                "run_id": summary["run_id"],
                "requests": summary["requests"],
                "slo_eligible": summary["slo"]["slo_eligible"],
            },
            sort_keys=True,
        )
    )


def _compare(args: argparse.Namespace) -> None:
    root = _repository_root()
    run_dir_a = Path(args.run_dir_a).resolve()
    run_dir_b = Path(args.run_dir_b).resolve()
    comparison = build_comparison(run_dir_a, run_dir_b, root)
    output_dir = run_dir_a.parent
    write_comparison_markdown(output_dir / "comparison.md", comparison)
    build_report(run_dir_a, root)
    if run_dir_b.parent == output_dir:
        build_report(run_dir_b, root)
    print(
        json.dumps(
            {
                "claimability": comparison["claimability"]["status"],
                "comparison_json": str(output_dir / "comparison.json"),
                "comparison_markdown": str(output_dir / "comparison.md"),
            },
            sort_keys=True,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m inference_gateway.benchmark")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--scenario", required=True)
    run.add_argument("--base-url", required=True)
    run.add_argument("--api-key")
    run.add_argument("--allow-dirty", action="store_true")
    report = commands.add_parser("report")
    report.add_argument("--run-dir", required=True)
    compare = commands.add_parser("compare")
    compare.add_argument("run_dir_a")
    compare.add_argument("run_dir_b")
    args = parser.parse_args()
    if args.command == "run":
        asyncio.run(_run(args))
    elif args.command == "report":
        _report(args)
    else:
        _compare(args)


if __name__ == "__main__":
    main()
