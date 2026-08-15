# Local lab

## Start the stack

Run:

```text
make local-up
```

This starts the gateway on `127.0.0.1:8080` and the fault service on
`127.0.0.1:9401`. The command generates an ephemeral gateway key and prints it once. Set
`GATEWAY_PORT`, `FAULTMOCK_PORT`, or `GATEWAY_AUTH_CONFIG` before the command to override the
defaults. Stop the command with Ctrl-C; both processes are terminated and generated auth data is
removed.

The fault service can also run alone:

```text
uv run python -m inference_gateway.faultmock --port 9401
```

Send `X-Fault-Scenario` directly to that service to select `ok`, `rate_limited_429`, `server_500`,
`timeout`, `delayed_ms`, `malformed_json`, `stream_ok`, `stream_fail_after_first_chunk`, or
`in_band_error`. Without a header, `config/local/fault-sequence.yaml` controls the sequence.

## Run the smoke check

```text
make local-smoke
```

The smoke check creates a throwaway auth file, starts both uvicorn processes on loopback, and
verifies authentication failure, non-streaming success, streaming success, restricted routing,
and metrics. Sandboxes that prohibit sockets use the same applications and adapters through HTTP
ASGI transports. The check reports which transport ran, exits non-zero on any failed check, and
does not retain the key.

## Generate fault evidence

```text
make fault-evidence
```

The command injects six faults through the wire service and real provider adapters. It writes
`results/local/<run-id>-fault/fault-evidence.json` with observed status, provider, fallback count,
deadline timing, upstream scenario counts, and gateway metric deltas. The file is local mock
behavior evidence, not performance evidence.

## Run the hybrid report

```text
make benchmark-local SCENARIO=benchmark/scenarios/hybrid-local.yaml
```

The report directory contains the normal manifest, records, summary, quality, and cost files.
`summary.json` includes the mixed policy inputs, private/managed route mix, combined View A cost,
and provider-level cost per correct task.

Prometheus can load `observability/prometheus/prometheus.local.yaml` while the TCP stack is running.
Import the four JSON files under `observability/grafana/dashboards/` into Grafana. GPU panels remain
wired to DCGM series for the later GPU-serving milestone.
