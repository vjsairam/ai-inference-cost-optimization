# Four-minute local walkthrough

This walkthrough uses only local mock behavior. It demonstrates contracts and evidence plumbing,
not cloud performance or savings. Run `make bootstrap` before the session.

## Before the clock

Run each evidence command once so the walkthrough is not dependent on package installation or a
live network:

```bash
export PATH="$HOME/.local/bin:$PATH"
make local-smoke
make fault-evidence
make benchmark-local SCENARIO=benchmark/scenarios/hybrid-local.yaml
```

Keep the printed hybrid run ID in `RUN_ID`, then regenerate the report:

```bash
export RUN_ID='<printed run ID>'
make report RUN_ID="$RUN_ID"
```

## 0:00-0:30 — decision

Open the repository README. State the question: when does a managed API, private one-GPU vLLM
service, or hybrid policy minimize `cost_per_correct_task` while meeting a declared SLO? Point out
that only local mock evidence exists and cloud conclusions are pending.

## 0:30-1:10 — local stack and architecture

Show `media/figure1_architecture.png`, then show the command used to start the two local services:

```bash
make local-up
```

Explain that it starts the gateway and deterministic fault service with an ephemeral key. The
same gateway surface fronts managed, private, and hybrid routes. Do not leave this blocking
command running while repeating the automated smoke on the default ports; stop it with Ctrl-C.

## 1:10-1:40 — smoke

Show the completed output from:

```bash
make local-smoke
```

Walk through the checks it reports: unauthenticated refusal, non-streaming and streaming success,
restricted routing through the private adapter, and the Prometheus request counter. Note whether
the command used loopback HTTP or its in-process HTTP fallback.

## 1:40-2:20 — injected faults

Show the newest local fault artifact:

```bash
find results/local -path '*-fault/fault-evidence.json' -print | sort | tail -n 1
make fault-evidence
```

Summarize the verified behaviors without converting timings into performance claims: normalized
429, 5xx, timeout, malformed response, fallback, and no replay after streaming content begins.

## 2:20-3:10 — hybrid benchmark report

Show the frozen scenario and regenerate the already-created report:

```bash
sed -n '1,220p' benchmark/scenarios/hybrid-local.yaml
make report RUN_ID="$RUN_ID"
```

Open these files under `results/local/$RUN_ID/`:

```text
manifest.yaml
summary.json
quality.json
cost.json
comparison.csv
```

Point to the request mix, route mix, SLO evaluation, and separately labeled View A and View B
fields. State that the values are deterministic local mock plumbing evidence.

## 3:10-3:40 — dashboards

List the dashboard sources:

```bash
find observability/grafana/dashboards -maxdepth 1 -name '*.json' -print | sort
```

Show the SLO, routing/failure, GPU efficiency, and economics dashboard definitions. GPU panels are
wired for DCGM but have no local GPU evidence. The cloud runbook uses the same definitions after
deployment.

## 3:40-4:00 — boundary and next run

Close on the evidence table in the README. The next claim-bearing step is the guarded M6 T0/T1
cloud comparison followed by M7 hybrid and failure runs. The runbook requires raw evidence export,
publication gates, immediate destroy, and independent destroy verification. Cost and duration
remain pending first M6 run.
