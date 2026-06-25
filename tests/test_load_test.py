from load_test import endpoint_url, percentile, request_once, summarize_results


def test_percentile_returns_expected_values():
    values = [50, 10, 30, 20, 40]

    assert percentile(values, 0) == 10
    assert percentile(values, 50) == 30
    assert percentile(values, 95) == 50
    assert percentile([], 95) is None


def test_endpoint_url_builds_call_id_targets():
    assert endpoint_url("http://localhost:5000/", "health", "call 1") == "http://localhost:5000/api/health"
    assert endpoint_url("http://localhost:5000", "session", "call 1") == (
        "http://localhost:5000/api/session?call_id=call+1"
    )
    assert endpoint_url("http://localhost:5000", "status", "call:1") == (
        "http://localhost:5000/api/status?call_id=call%3A1"
    )
    assert endpoint_url("http://localhost:5000", "voice", "call:1") == (
        "http://localhost:5000/api/voice?call_id=call%3A1"
    )


def test_summarize_results_counts_latency_and_capacity_signals():
    results = [
        {
            "call_id": "load-1",
            "status_code": 200,
            "elapsed_ms": 10,
            "error": None,
            "json": {"cached": True, "layer": "cache"},
        },
        {
            "call_id": "load-2",
            "status_code": 429,
            "elapsed_ms": 20,
            "error": "capacity",
            "json": {"error": "capacity"},
        },
        {
            "call_id": "load-3",
            "status_code": 503,
            "elapsed_ms": 30,
            "error": "models",
            "json": {"error": "models"},
        },
    ]

    summary = summarize_results(results)

    assert summary["total_requests"] == 3
    assert summary["ok_count"] == 1
    assert summary["error_count"] == 2
    assert summary["status_counts"] == {"200": 1, "429": 1, "503": 1}
    assert summary["latency_ms"]["p50"] == 20
    assert summary["cache_hits"] == 1
    assert summary["rate_limited_count"] == 1
    assert summary["model_unavailable_count"] == 1
    assert summary["layer_counts"] == {"cache": 1}
    assert len(summary["error_samples"]) == 2


def test_request_once_sends_org_header(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return b'{"ok": true}'

        def getcode(self):
            return 200

    def fake_urlopen(request, timeout):
        captured["headers"] = dict(request.header_items())
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = request_once(
        "http://localhost:5000",
        "session",
        "caller-1",
        1,
        30,
        org_id="acme-health",
    )

    assert result["status_code"] == 200
    assert captured["headers"]["X-vox-org-id"] == "acme-health"
