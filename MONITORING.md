# VOX Monitoring

VOX exposes Prometheus-style metrics at `/api/metrics`.
Use a read-only admin token for monitoring.

## 1. Create A Read-Only Token

Start VOX, then create a monitoring token with the root admin token:

```bash
curl -X POST http://localhost:5000/api/admin/tokens \
  -H "X-VOX-Admin-Token: <root-admin-token>" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"Prometheus\",\"scopes\":[\"read\"],\"expires_in_days\":365}"
```

Save only the returned `token` value into:

```text
deploy/monitoring/secrets/vox_metrics_token
```

Do not add a trailing space. A final newline is fine.

## 2. Confirm Metrics Work

Both headers are supported:

```bash
curl http://localhost:5000/api/metrics -H "X-VOX-Admin-Token: <monitoring-token>"
```

```bash
curl http://localhost:5000/api/metrics -H "Authorization: Bearer <monitoring-token>"
```

Prometheus uses the Bearer form.

## 3. Start Prometheus And Grafana

From the project root:

```bash
docker compose -f deploy/monitoring/docker-compose.yml up -d
```

Open:

- Prometheus: `http://localhost:9090`
- Grafana: `http://localhost:3000`

Default Grafana credentials from the template:

```text
admin / change-me-before-production
```

Change the password before exposing Grafana to anyone else.

## 4. Adjust The Target

The default Docker target is:

```text
host.docker.internal:5000
```

This works when VOX runs on the Docker host.
If Prometheus runs directly on the same Linux server without Docker, use:

```yaml
targets:
  - 127.0.0.1:5000
```

## 5. Built-In Alerts

Alert rules are in:

```text
deploy/monitoring/prometheus/vox-alerts.yml
```

Included alerts:

- models not ready
- model loading error
- high 5xx error rate
- high rate-limit rate
- high average latency
- many open handoffs

These alerts appear in Prometheus. To send messages to email, Slack, or another channel, add Alertmanager later.

## 6. Grafana Dashboard

Grafana automatically loads:

```text
deploy/monitoring/grafana/dashboards/vox-overview.json
```

The dashboard shows:

- model readiness
- average latency
- active calls
- open handoffs
- cached answers
- requests by status
- STT/query/session activity
