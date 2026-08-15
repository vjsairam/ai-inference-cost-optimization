"""M3 fault evidence through real adapters and the wire-level fault service."""

from __future__ import annotations

import json
from pathlib import Path

from inference_gateway.fault_evidence import generate_fault_evidence

ROOT = Path(__file__).resolve().parents[2]


async def test_fault_evidence_covers_fallback_deadline_and_no_replay(tmp_path: Path) -> None:
    run_dir, payload = await generate_fault_evidence(ROOT, results_root=tmp_path)
    written = json.loads((run_dir / "fault-evidence.json").read_text(encoding="utf-8"))
    assert written == payload
    assert payload["headline"] == {
        "faults_injected": 6,
        "expectations_passed": 6,
        "fallback_faults": [
            "rate_limited_429",
            "server_500",
            "timeout",
            "in_band_error",
        ],
        "non_replayed_stream_failures": 1,
    }
    scenarios = {case["fault"]: case for case in payload["scenarios"]}
    assert scenarios["timeout"]["observed"]["elapsed_ms"] < 600
    assert scenarios["malformed_json"]["observed"]["error_code"] == "malformed_response"
    stream_failure = scenarios["stream_fail_after_first_chunk"]
    assert stream_failure["observed"]["partial_content"] == "fault mock"
    assert stream_failure["observed"]["upstream_scenario_counts"] == {
        "stream_fail_after_first_chunk": 1
    }
    assert all(case["metric_deltas"] for case in scenarios.values())
