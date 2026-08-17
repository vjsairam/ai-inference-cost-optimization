#!/usr/bin/env python3
"""Assemble reviewed benchmark artifacts into the published run layout."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import shutil
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RECORD_SCHEMA = REPOSITORY_ROOT / "results/schema/benchmark-record-v1.schema.json"
REQUIRED_FILES = (
    "manifest.yaml",
    "summary.json",
    "quality.json",
    "cost.json",
    "comparison.csv",
    "records.jsonl",
)
COPIED_FILES = REQUIRED_FILES[:-1]


class PublicationError(ValueError):
    """Raised when a run does not satisfy the publication contract."""


def _load_mapping(path: Path, label: str, *, yaml_format: bool = False) -> dict[str, Any]:
    try:
        if yaml_format:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        else:
            loaded = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    except (OSError, UnicodeError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise PublicationError(f"cannot load {label} from {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise PublicationError(f"{label} must contain a mapping")
    return loaded


def _nested_mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PublicationError(f"{field} must be a mapping")
    return value


def _validate_source(run_dir: Path) -> tuple[dict[str, Any], dict[str, Any], str]:
    if not run_dir.is_dir():
        raise PublicationError(f"source run directory does not exist: {run_dir}")
    missing = [name for name in REQUIRED_FILES if not (run_dir / name).is_file()]
    if missing:
        raise PublicationError(
            f"source run directory is missing required files: {', '.join(missing)}"
        )

    manifest = _load_mapping(run_dir / "manifest.yaml", "manifest", yaml_format=True)
    scenario = _nested_mapping(manifest.get("scenario"), "manifest scenario")
    if scenario.get("publishable") is not True:
        raise PublicationError("scenario.publishable must be true")

    git_details = _nested_mapping(manifest.get("git"), "manifest git")
    sha = git_details.get("sha")
    if not isinstance(sha, str) or not sha.strip() or sha.strip().lower() == "unknown":
        raise PublicationError("manifest git.sha must be known")

    harness = _nested_mapping(manifest.get("harness"), "manifest harness")
    location = harness.get("location")
    if not isinstance(location, str) or not location.strip():
        raise PublicationError("manifest harness.location must be recorded")
    if location.strip().lower() == "local-mock":
        raise PublicationError("manifest harness.location must not be local-mock")

    run_id = manifest.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise PublicationError("manifest run_id must be a nonempty string")
    if Path(run_id).name != run_id or run_id in {".", ".."}:
        raise PublicationError("manifest run_id must be a single safe path component")

    summary = _load_mapping(run_dir / "summary.json", "summary")
    if summary.get("run_id") != run_id:
        raise PublicationError("summary run_id must match manifest run_id")
    return manifest, summary, run_id


def _record_details(records_path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    record_count = 0
    with records_path.open("rb") as records:
        for line in records:
            digest.update(line)
            if line.strip():
                record_count += 1
    return digest.hexdigest(), record_count


def _record_schema_version() -> str:
    schema = _load_mapping(RECORD_SCHEMA, "benchmark record schema")
    version = schema.get("version") or schema.get("schema_version")
    return str(version) if version is not None else RECORD_SCHEMA.name


def _quoted_yaml(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def _raw_reference(run_id: str, digest: str, record_count: int) -> str:
    location = f"repository archive under results/raw/{run_id}/ (operator retained)"
    access_note = "Raw records are retained by the operator and are available on request."
    return "\n".join(
        (
            f"records_file: {_quoted_yaml('records.jsonl')}",
            f"sha256: {_quoted_yaml(digest)}",
            f"record_count: {record_count}",
            f"record_schema_version: {_quoted_yaml(_record_schema_version())}",
            f"storage_location: {_quoted_yaml(location)}",
            f"access_note: {_quoted_yaml(access_note)}",
            "",
        )
    )


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise PublicationError("metric values must be decimal numbers or null")
    try:
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PublicationError(f"invalid decimal metric value: {value!r}") from exc
    if not decimal_value.is_finite():
        raise PublicationError(f"metric value must be finite: {value!r}")
    return decimal_value


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _truncated_decimal(value: Decimal, limit: int = 14) -> str:
    rendered = _decimal_text(value)
    return rendered if len(rendered) <= limit else f"{rendered[:limit]}..."


def _sum_exact(values: Sequence[Decimal]) -> Decimal:
    precision = max(50, sum(len(value.as_tuple().digits) for value in values) + 4)
    with localcontext() as context:
        context.prec = precision
        return sum(values, Decimal(0))


def _observed_paths(summary: Mapping[str, Any]) -> tuple[bool, bool]:
    cost = _nested_mapping(summary.get("cost"), "summary cost")
    paths = cost.get("observed_path_applicability")
    if not isinstance(paths, Mapping):
        return False, False
    return paths.get("managed") is True, paths.get("private") is True


def _repeat_cost(row: Mapping[str, Any], observed: tuple[bool, bool]) -> Decimal | None:
    if "view_a_cost_per_correct_task_usd" in row:
        return _decimal(row.get("view_a_cost_per_correct_task_usd"))

    managed = _decimal(row.get("view_a_managed_cost_per_correct_task_usd"))
    private = _decimal(row.get("view_a_private_cost_per_correct_task_usd"))
    managed_observed, private_observed = observed
    if managed_observed and private_observed:
        present = [value for value in (managed, private) if value is not None]
        return _sum_exact(present) if present else None
    if managed_observed:
        return managed
    if private_observed:
        return private
    present = [value for value in (managed, private) if value is not None]
    return _sum_exact(present) if present else None


def _overall_cost(summary: Mapping[str, Any]) -> Decimal | None:
    cost = _nested_mapping(summary.get("cost"), "summary cost")
    managed_observed, private_observed = _observed_paths(summary)
    if managed_observed != private_observed:
        views = _nested_mapping(cost.get("views"), "summary cost.views")
        view_a = _nested_mapping(views.get("view_a"), "summary cost.views.view_a")
        route = "managed" if managed_observed else "private"
        route_cost = _nested_mapping(view_a.get(route), f"summary View A {route} cost")
        return _decimal(route_cost.get("cost_per_correct_task_usd"))
    combined = _nested_mapping(
        cost.get("hybrid_combined_view_a"), "summary cost.hybrid_combined_view_a"
    )
    return _decimal(combined.get("cost_per_correct_task_usd"))


def _repeat_rows(summary: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    repeats = _nested_mapping(summary.get("repeats"), "summary repeats")
    values = repeats.get("values")
    if not isinstance(values, list):
        raise PublicationError("summary repeats.values must be a list")
    if not all(isinstance(row, Mapping) for row in values):
        raise PublicationError("summary repeats.values entries must be mappings")
    return values


def _bar_chart(
    title: str,
    subtitle: str,
    series: Sequence[tuple[str, Decimal | None]],
    maximum: Decimal,
    value_label: Callable[[Decimal], str],
) -> str:
    left = 58
    top = 48
    plot_width = 522
    plot_height = 174
    baseline = top + plot_height
    count = max(len(series), 1)
    slot = plot_width / count
    bar_width = min(66.0, slot * 0.55)
    elements = [
        (
            '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="300" '
            'viewBox="0 0 600 300" role="img">'
        ),
        f"  <title>{html.escape(title)}</title>",
        '  <rect width="600" height="300" fill="#ffffff"/>',
        '  <g font-family="sans-serif" fill="#17212b">',
        (
            '    <text x="300" y="24" text-anchor="middle" font-size="17" '
            f'font-weight="600">{html.escape(title)}</text>'
        ),
        (
            '    <text x="300" y="41" text-anchor="middle" font-size="10">'
            f"{html.escape(subtitle)}</text>"
        ),
        f'    <line x1="{left}" y1="{top}" x2="{left}" y2="{baseline}" stroke="#52606d"/>',
        f'    <line x1="{left}" y1="{baseline}" x2="580" y2="{baseline}" stroke="#52606d"/>',
    ]
    for fraction in (Decimal(0), Decimal("0.5"), Decimal(1)):
        y = baseline - float(fraction) * plot_height
        tick = _sum_exact((maximum * fraction,))
        elements.extend(
            (
                f'    <line x1="54" y1="{y:.2f}" x2="58" y2="{y:.2f}" stroke="#52606d"/>',
                (
                    f'    <text x="50" y="{y + 3:.2f}" text-anchor="end" font-size="9">'
                    f"{html.escape(value_label(tick))}</text>"
                ),
            )
        )

    for index, (label, value) in enumerate(series):
        center = left + slot * (index + 0.5)
        elements.append(
            f'    <text x="{center:.2f}" y="242" text-anchor="middle" font-size="10">'
            f"{html.escape(label)}</text>"
        )
        if value is None:
            elements.append(
                f'    <text x="{center:.2f}" y="144" text-anchor="middle" font-size="10" '
                'fill="#6b7280">not computed</text>'
            )
            continue
        bounded = min(max(value, Decimal(0)), maximum)
        bar_height = float(bounded / maximum) * plot_height if maximum else 0.0
        rendered_height = max(bar_height, 1.0)
        y = baseline - rendered_height
        full_value = _decimal_text(value)
        elements.extend(
            (
                (
                    f'    <rect x="{center - bar_width / 2:.2f}" y="{y:.2f}" '
                    f'width="{bar_width:.2f}" height="{rendered_height:.2f}" fill="#2563a6">'
                ),
                f"      <title>{html.escape(full_value)}</title>",
                "    </rect>",
                (
                    f'    <text x="{center:.2f}" y="{max(y - 7, 53):.2f}" '
                    'text-anchor="middle" font-size="10">'
                    f"{html.escape(value_label(value))}</text>"
                ),
            )
        )
    elements.extend(("  </g>", "</svg>", ""))
    return "\n".join(elements)


def _quality_chart(summary: Mapping[str, Any]) -> str:
    rows = _repeat_rows(summary)
    series = [
        (f"Repeat {row.get('repeat_index', index)}", _decimal(row.get("quality_rate")))
        for index, row in enumerate(rows, start=1)
    ]
    quality = _nested_mapping(summary.get("quality"), "summary quality")
    series.append(("Overall", _decimal(quality.get("quality_rate"))))
    return _bar_chart(
        "Quality rate by repeat",
        "Overall is shown after the measured repeats",
        series,
        Decimal(1),
        lambda value: f"{value:.4f}",
    )


def _cost_chart(summary: Mapping[str, Any]) -> str:
    observed = _observed_paths(summary)
    series = [
        (f"Repeat {row.get('repeat_index', index)}", _repeat_cost(row, observed))
        for index, row in enumerate(_repeat_rows(summary), start=1)
    ]
    present = [value for _, value in series if value is not None and value >= 0]
    maximum = max(present, default=Decimal(1))
    if maximum == 0:
        maximum = Decimal(1)
    return _bar_chart(
        "View A cost per correct task",
        "USD per repeat; labels are truncated without rounding",
        series,
        maximum,
        lambda value: f"${_truncated_decimal(value)}",
    )


def _placement(summary: Mapping[str, Any]) -> str:
    harness = _nested_mapping(summary.get("harness"), "summary harness")
    parts = []
    for label, field in (
        ("location", "location"),
        ("node group", "node_group"),
        ("availability zone", "availability_zone"),
    ):
        value = harness.get(field)
        if value is not None and str(value).strip():
            parts.append(f"{label} {value}")
    return "; ".join(parts) if parts else "not recorded"


def _required_summary_value(summary: Mapping[str, Any], field: str) -> object:
    value = summary.get(field)
    if value is None:
        raise PublicationError(f"summary {field} must be recorded")
    return value


def _readme(summary: Mapping[str, Any]) -> str:
    run_id = str(_required_summary_value(summary, "run_id"))
    treatment = str(_required_summary_value(summary, "treatment"))
    workload = str(_required_summary_value(summary, "workload"))
    sample_size = _required_summary_value(summary, "sample_size")
    quality = _nested_mapping(summary.get("quality"), "summary quality")
    quality_rate = _decimal(quality.get("quality_rate"))
    slo = _nested_mapping(summary.get("slo"), "summary slo")
    slo_eligible = slo.get("slo_eligible")
    if not isinstance(slo_eligible, bool):
        raise PublicationError("summary slo.slo_eligible must be a boolean")
    limitations = summary.get("limitations")
    if not isinstance(limitations, list) or not all(isinstance(item, str) for item in limitations):
        raise PublicationError("summary limitations must be a list of strings")

    overall_cost = _overall_cost(summary)
    quality_text = _decimal_text(quality_rate) if quality_rate is not None else "not computed"
    cost_text = _decimal_text(overall_cost) if overall_cost is not None else "not computed"
    limitation_text = "\n\n".join(limitations) if limitations else "None recorded."
    return "\n".join(
        (
            f"# Published run {run_id}",
            "",
            f"- Treatment: {treatment}",
            f"- Workload: {workload}",
            f"- Sample size: {sample_size}",
            f"- Quality rate: {quality_text}",
            f"- SLO eligibility: {str(slo_eligible).lower()}",
            f"- View A cost per correct task: {cost_text}",
            f"- Placement: {_placement(summary)}",
            "",
            "## Interpretation",
            "",
            "Written by the operator during review.",
            "",
            "## Limitations",
            "",
            limitation_text,
            "",
        )
    )


def publish_run(run_dir: Path, output_root: Path, comparison: Path | None = None) -> Path:
    """Validate and assemble one run, returning its published destination."""
    _, summary, run_id = _validate_source(run_dir)
    if comparison is not None:
        if not comparison.is_file():
            raise PublicationError(f"comparison file does not exist: {comparison}")
        _load_mapping(comparison, "comparison")

    destination = output_root / run_id
    if destination.exists():
        raise PublicationError(f"destination already exists: {destination}")

    digest, record_count = _record_details(run_dir / "records.jsonl")
    raw_reference = _raw_reference(run_id, digest, record_count)
    readme = _readme(summary)
    quality_svg = _quality_chart(summary)
    cost_svg = _cost_chart(summary)

    output_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".publish-", dir=output_root) as temporary:
        staging = Path(temporary) / run_id
        charts = staging / "charts"
        charts.mkdir(parents=True)
        for name in COPIED_FILES:
            shutil.copyfile(run_dir / name, staging / name)
        if comparison is not None:
            shutil.copyfile(comparison, staging / "comparison.json")
        (staging / "raw-reference.yaml").write_text(raw_reference, encoding="utf-8")
        (staging / "README.md").write_text(readme, encoding="utf-8")
        (charts / "quality.svg").write_text(quality_svg, encoding="utf-8")
        (charts / "cost.svg").write_text(cost_svg, encoding="utf-8")
        if destination.exists():
            raise PublicationError(f"destination already exists: {destination}")
        staging.rename(destination)
    return destination


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Assemble a reviewed published run directory.")
    parser.add_argument("run_dir", type=Path, help="completed source run directory")
    parser.add_argument(
        "--output", type=Path, required=True, help="parent directory for published runs"
    )
    parser.add_argument("--comparison", type=Path, help="optional comparison JSON to copy")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        destination = publish_run(args.run_dir, args.output, args.comparison)
    except PublicationError as exc:
        print(f"publication refused: {exc}", file=sys.stderr)
        return 1
    print(f"Created: {destination}")
    print("Files:")
    for path in sorted(item for item in destination.rglob("*") if item.is_file()):
        print(f"  {path.relative_to(destination)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
