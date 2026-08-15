# Cloud observability provisioning

The M5 stack uses Prometheus Operator `ServiceMonitor` resources consistently for the gateway,
vLLM, and DCGM exporter. The kube-prometheus-stack values allow monitors and rules from the
`gateway-system`, `model-serving`, and `monitoring` namespaces.

The four dashboards in `observability/grafana/dashboards/` are provisioned by `scripts/deploy.sh`.
It builds the `monitoring/inference-lab-dashboards` ConfigMap from those JSON files and applies the
`grafana_dashboard=1` label consumed by the Grafana sidecar. To refresh dashboards after an edit,
run `make deploy ENV=aws-lab` with the required deployment environment variables; the ConfigMap
update is idempotent.

The alert rules in `observability/alerts/cloud.rules.yaml` are applied after the Prometheus
Operator is ready. The M3 local-only alert file remains independent of the cloud rules.
