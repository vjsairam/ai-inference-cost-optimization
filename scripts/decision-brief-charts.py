#!/usr/bin/env python3
"""Render the decision brief charts from the published runs it describes."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PUBLISHED = REPOSITORY_ROOT / "results/published"
COST_CONFIG = REPOSITORY_ROOT / "config/cost.example.yaml"
OUTPUT = REPOSITORY_ROOT / "docs/decision-brief"

# The brief is written about these runs. Update the list and the brief together.
BRIEF_RUNS = (
    "20260817T154919Z-cbe2c957-t0-managed-baseline",
    "20260817T160103Z-cbe2c957-t0-managed-baseline",
    "20260817T153645Z-cbe2c957-t1-private-baseline",
    "20260817T153715Z-cbe2c957-t1-private-baseline",
    "20260817T161636Z-cbe2c957-t3-hybrid",
)
GRID_RUN = "20260817T153645Z-cbe2c957-t1-private-baseline"

MANAGED = "#b45309"
PRIVATE = "#2563a6"
HYBRID = "#15803d"
INK = "#17212b"
AXIS = "#52606d"
GRID = "#e5e7eb"

TREATMENTS = {
    "t0": ("Managed", MANAGED),
    "t1": ("Private vLLM", PRIVATE),
    "t3": ("Hybrid", HYBRID),
}
WORKLOAD_LABELS = {"classification": "classification", "structured-extraction": "extraction"}
PRIVATE_TIERS = (("high", ""), ("typical", "6 4"), ("low", "2 3"))


def _load_json(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _cost_per_correct(summary: Mapping[str, Any]) -> float:
    """The View A cost per correct task that each run README reports."""
    cost = summary["cost"]
    prefix = summary["treatment"][:2]
    if prefix == "t0":
        return float(cost["views"]["view_a"]["managed"]["cost_per_correct_task_usd"])
    if prefix == "t1":
        return float(cost["views"]["view_a"]["private"]["cost_per_correct_task_usd"])
    return float(cost["hybrid_combined_view_a"]["cost_per_correct_task_usd"])


def measured_points(published: Path) -> list[dict[str, Any]]:
    points = []
    for run_id in BRIEF_RUNS:
        summary = _load_json(published / run_id / "summary.json")
        points.append(
            {
                "treatment": summary["treatment"][:2],
                "workload": summary["workload"],
                "quality": float(summary["quality"]["quality_rate"]),
                "cost": _cost_per_correct(summary),
                "quality_floor": float(summary["slo"]["checks"]["quality_rate"]["target"]),
            }
        )
    return sorted(points, key=lambda point: (point["workload"], point["treatment"]))


def private_steps(
    capacity: int, replica_cost: float, low: int, high: int
) -> list[tuple[int, float]]:
    """Monthly private cost when each replica carries at most `capacity` requests."""
    path = [(low, math.ceil(low / capacity) * replica_cost)]
    boundary = (low // capacity + 1) * capacity
    while boundary < high:
        replicas = boundary // capacity
        path.extend(
            ((boundary, replicas * replica_cost), (boundary, (replicas + 1) * replica_cost))
        )
        boundary += capacity
    path.append((high, math.ceil(high / capacity) * replica_cost))
    return path


def monthly_series(published: Path, cost_config: Path = COST_CONFIG) -> list[dict[str, Any]]:
    """Modelled View A monthly totals from the scenario grid of GRID_RUN."""
    cost = _load_json(published / GRID_RUN / "cost.json")
    if hashlib.sha256(cost_config.read_bytes()).hexdigest() != cost["config_sha256"]:
        raise SystemExit(f"{cost_config} no longer matches the cost config {GRID_RUN} used")
    tiers = yaml.safe_load(cost_config.read_text(encoding="utf-8"))["scenario_grid"][
        "utilization_tiers"
    ]
    rows = [
        row
        for row in cost["scenario_grid"]["rows"]
        if row["view"] == "view_a"
        and row["quality_sensitivity_path"] == "measured"
        and row["quality_delta_points"] == 0
    ]

    def grid(key: str, **match: str) -> list[tuple[int, float]]:
        selected = {
            int(row["monthly_requests"]): float(row[key])
            for row in rows
            if all(row[field] == value for field, value in match.items())
        }
        return sorted(selected.items())

    series = []
    for profile, dash in (("short", ""), ("medium", "6 4")):
        points = grid("managed_total_usd", token_profile=profile)
        series.append(
            {
                "label": f"Managed, {profile} prompts",
                "color": MANAGED,
                "dash": dash,
                "points": points,
                "path": points,
            }
        )
    for tier, dash in PRIVATE_TIERS:
        points = grid("private_total_usd", token_profile="short", utilization_tier=tier)
        replicas = {
            int(row["monthly_requests"]): int(row["replicas"])
            for row in rows
            if row["utilization_tier"] == tier and row["token_profile"] == "short"
        }
        capacity = int(tiers[tier]["requests_per_replica_month"])
        low_volume, low_cost = points[0]
        replica_cost = low_cost / replicas[low_volume]
        for volume, total in points:
            if not math.isclose(math.ceil(volume / capacity) * replica_cost, total):
                raise SystemExit(f"{tier} replica capacity disagrees with the grid at {volume}")
        path = private_steps(capacity, replica_cost, points[0][0], points[-1][0])
        series.append(
            {
                "label": f"Private, {tier} utilization",
                "color": PRIVATE,
                "dash": dash,
                "points": points,
                "path": path,
            }
        )
    return series


def _log_position(value: float, low: float, high: float, start: float, length: float) -> float:
    return start + (math.log10(value) - math.log10(low)) / (math.log10(high) - math.log10(low)) * (
        length
    )


def _header(width: int, height: int, title: str, subtitle: str) -> list[str]:
    return [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img">'
        ),
        f"  <title>{html.escape(title)}</title>",
        f'  <rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'  <g font-family="sans-serif" fill="{INK}">',
        (
            f'    <text x="{width / 2:.0f}" y="24" text-anchor="middle" font-size="17" '
            f'font-weight="600">{html.escape(title)}</text>'
        ),
        (
            f'    <text x="{width / 2:.0f}" y="41" text-anchor="middle" font-size="10">'
            f"{html.escape(subtitle)}</text>"
        ),
    ]


def _money(value: float) -> str:
    if value >= 1:
        return f"${value:,.0f}"
    decimals = 2 - math.floor(math.log10(value))
    return f"${value:.{decimals}f}".rstrip("0")


def quality_cost_svg(points: Sequence[Mapping[str, Any]]) -> str:
    width, height = 680, 380
    left, top, plot_width, plot_height = 80, 60, 400, 260
    bottom = top + plot_height
    cost_low, cost_high = 0.00001, 0.01
    quality_low, quality_high = 0.3, 1.0

    def x_of(quality: float) -> float:
        return left + (quality - quality_low) / (quality_high - quality_low) * plot_width

    def y_of(cost: float) -> float:
        return bottom - _log_position(cost, cost_low, cost_high, 0, plot_height)

    elements = _header(
        width,
        height,
        "Measured quality and cost per correct task",
        "View A, 900 requests per arm, 2026-08-17; dashed lines are the baseline quality floors",
    )
    for cost in (0.00001, 0.0001, 0.001, 0.01):
        y = y_of(cost)
        elements.extend(
            (
                f'    <line x1="{left}" y1="{y:.2f}" x2="{left + plot_width}" y2="{y:.2f}" '
                f'stroke="{GRID}"/>',
                f'    <text x="{left - 6}" y="{y + 3:.2f}" text-anchor="end" font-size="9">'
                f"{_money(cost)}</text>",
            )
        )
    for quality in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0):
        x = x_of(quality)
        elements.append(
            f'    <text x="{x:.2f}" y="{bottom + 14}" text-anchor="middle" font-size="9">'
            f"{quality:.0%}</text>"
        )
    elements.extend(
        (
            f'    <line x1="{left}" y1="{top}" x2="{left}" y2="{bottom}" stroke="{AXIS}"/>',
            f'    <line x1="{left}" y1="{bottom}" x2="{left + plot_width}" y2="{bottom}" '
            f'stroke="{AXIS}"/>',
            f'    <text x="{left + plot_width / 2}" y="{bottom + 32}" text-anchor="middle" '
            'font-size="10">Share of tasks answered correctly</text>',
            f'    <text x="18" y="{top + plot_height / 2}" text-anchor="middle" font-size="10" '
            f'transform="rotate(-90 18 {top + plot_height / 2})">'
            "Cost per correct task (log scale)</text>",
        )
    )
    # Hybrid runs judge each traffic cell against its own floor, so only baselines draw one.
    baselines = [point for point in points if point["treatment"] != "t3"]
    floors = sorted({(point["workload"], point["quality_floor"]) for point in baselines})
    for index, (workload, floor) in enumerate(floors):
        x = x_of(floor)
        elements.extend(
            (
                f'    <line x1="{x:.2f}" y1="{top}" x2="{x:.2f}" y2="{bottom}" stroke="{AXIS}" '
                'stroke-dasharray="3 3"/>',
                f'    <text x="{x - 4:.2f}" y="{bottom - 6 - index * 12}" text-anchor="end" '
                f'font-size="9" fill="{AXIS}">'
                f"{WORKLOAD_LABELS[workload]} floor {floor:.0%}</text>",
            )
        )
    for point in points:
        name, color = TREATMENTS[point["treatment"]]
        x, y = x_of(point["quality"]), y_of(point["cost"])
        fill = color
        label = f"{name}, {WORKLOAD_LABELS[point['workload']]}"
        detail = f"{point['quality']:.1%} at {_money(point['cost'])}"
        if point["workload"] == "classification":
            marker = (
                f'    <circle cx="{x:.2f}" cy="{y:.2f}" r="6" fill="{fill}" stroke="{color}" '
                'stroke-width="2">'
            )
            closing = "    </circle>"
        else:
            marker = (
                f'    <rect x="{x - 6:.2f}" y="{y - 6:.2f}" width="12" height="12" '
                f'fill="{fill}" stroke="{color}" stroke-width="2">'
            )
            closing = "    </rect>"
        elements.extend(
            (
                marker,
                f"      <title>{html.escape(label)}: {html.escape(detail)}</title>",
                closing,
            )
        )
        anchor, offset = ("end", -10) if point["quality"] > 0.9 else ("start", 10)
        elements.extend(
            (
                f'    <text x="{x + offset:.2f}" y="{y - 3:.2f}" text-anchor="{anchor}" '
                f'font-size="10" fill="{color}">{html.escape(label)}</text>',
                f'    <text x="{x + offset:.2f}" y="{y + 9:.2f}" text-anchor="{anchor}" '
                f'font-size="9">{html.escape(detail)}</text>',
            )
        )
    legend_x = left + plot_width + 28
    elements.extend(
        (
            f'    <circle cx="{legend_x + 6}" cy="{top + 6}" r="5" fill="{AXIS}"/>',
            f'    <text x="{legend_x + 18}" y="{top + 9}" font-size="10">Classification</text>',
            f'    <rect x="{legend_x + 1}" y="{top + 19}" width="10" height="10" fill="{AXIS}"/>',
            f'    <text x="{legend_x + 18}" y="{top + 28}" font-size="10">Extraction</text>',
        )
    )
    for index, (name, color) in enumerate(TREATMENTS.values()):
        y = top + 52 + index * 18
        elements.extend(
            (
                f'    <rect x="{legend_x}" y="{y - 8}" width="12" height="12" fill="{color}"/>',
                f'    <text x="{legend_x + 18}" y="{y + 2}" font-size="10">{name}</text>',
            )
        )
    elements.extend(("  </g>", "</svg>", ""))
    return "\n".join(elements)


def monthly_cost_svg(series: Sequence[Mapping[str, Any]]) -> str:
    width, height = 680, 380
    left, top, plot_width, plot_height = 80, 60, 400, 260
    bottom = top + plot_height
    requests_low, requests_high = 10_000, 1_000_000
    cost_low, cost_high = 10.0, 20_000.0

    def x_of(requests: float) -> float:
        return _log_position(requests, requests_low, requests_high, left, plot_width)

    def y_of(cost: float) -> float:
        return bottom - _log_position(cost, cost_low, cost_high, 0, plot_height)

    elements = _header(
        width,
        height,
        "Modelled monthly cost by request volume",
        "View A monthly totals, one NVIDIA L4 per private replica, August 2026 prices",
    )
    for cost in (10, 100, 1_000, 10_000):
        y = y_of(cost)
        elements.extend(
            (
                f'    <line x1="{left}" y1="{y:.2f}" x2="{left + plot_width}" y2="{y:.2f}" '
                f'stroke="{GRID}"/>',
                f'    <text x="{left - 6}" y="{y + 3:.2f}" text-anchor="end" font-size="9">'
                f"{_money(cost)}</text>",
            )
        )
    ticks = sorted({requests for line in series for requests, _ in line["points"]})
    for requests in ticks:
        x = x_of(requests)
        label = f"{requests // 1000:,}k" if requests < 1_000_000 else f"{requests // 1_000_000}M"
        elements.extend(
            (
                f'    <line x1="{x:.2f}" y1="{top}" x2="{x:.2f}" y2="{bottom}" stroke="{GRID}"/>',
                f'    <text x="{x:.2f}" y="{bottom + 14}" text-anchor="middle" font-size="9">'
                f"{label}</text>",
            )
        )
    elements.extend(
        (
            f'    <line x1="{left}" y1="{top}" x2="{left}" y2="{bottom}" stroke="{AXIS}"/>',
            f'    <line x1="{left}" y1="{bottom}" x2="{left + plot_width}" y2="{bottom}" '
            f'stroke="{AXIS}"/>',
            f'    <text x="{left + plot_width / 2}" y="{bottom + 32}" text-anchor="middle" '
            'font-size="10">Requests per month (log scale)</text>',
            f'    <text x="18" y="{top + plot_height / 2}" text-anchor="middle" font-size="10" '
            f'transform="rotate(-90 18 {top + plot_height / 2})">'
            "USD per month (log scale)</text>",
        )
    )
    for line in series:
        coordinates = " ".join(
            f"{x_of(requests):.2f},{y_of(cost):.2f}" for requests, cost in line["path"]
        )
        dash = f' stroke-dasharray="{line["dash"]}"' if line["dash"] else ""
        elements.append(
            f'    <polyline points="{coordinates}" fill="none" stroke="{line["color"]}" '
            f'stroke-width="2"{dash}/>'
        )
        for requests, cost in line["points"]:
            elements.extend(
                (
                    f'    <circle cx="{x_of(requests):.2f}" cy="{y_of(cost):.2f}" r="3" '
                    f'fill="{line["color"]}">',
                    f"      <title>{html.escape(line['label'])}: {requests:,} requests, "
                    f"{_money(cost)}</title>",
                    "    </circle>",
                )
            )
    legend_x = left + plot_width + 20
    for index, line in enumerate(series):
        y = top + 6 + index * 18
        dash = f' stroke-dasharray="{line["dash"]}"' if line["dash"] else ""
        elements.extend(
            (
                f'    <line x1="{legend_x}" y1="{y}" x2="{legend_x + 24}" y2="{y}" '
                f'stroke="{line["color"]}" stroke-width="2"{dash}/>',
                f'    <text x="{legend_x + 30}" y="{y + 3}" font-size="10">'
                f"{html.escape(line['label'])}</text>",
            )
        )
    elements.extend(("  </g>", "</svg>", ""))
    return "\n".join(elements)


def render(published: Path) -> dict[str, str]:
    return {
        "quality-cost.svg": quality_cost_svg(measured_points(published)),
        "monthly-cost.svg": monthly_cost_svg(monthly_series(published)),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--published", type=Path, default=PUBLISHED)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    for name, svg in render(args.published).items():
        (args.output / name).write_text(svg, encoding="utf-8")
        print(args.output / name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
