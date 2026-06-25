import smoke_test


def args(**overrides):
    defaults = {
        "base_url": "http://vox.test",
        "admin_token": None,
        "org_id": None,
        "timeout": 1.0,
        "require_models": False,
        "require_metrics": False,
        "require_ollama": False,
        "json": False,
    }
    defaults.update(overrides)
    return type("Args", (), defaults)()


def fake_request_factory(responses):
    def fake_request(base_url, path, timeout, admin_token=None, org_id=None):
        assert base_url == "http://vox.test"
        response = responses[path]
        return smoke_test.HttpResult(
            status_code=response.get("status_code", 200),
            elapsed_ms=1.0,
            json_body=response.get("json_body"),
            text_body=response.get("text_body", ""),
            error=response.get("error"),
        )

    return fake_request


def base_responses():
    return {
        "/api/health": {
            "json_body": {"status": "ok", "models": {"ready": False}, "org_id": "default"},
        },
        "/api/ready": {
            "json_body": {"app_ready": True, "ready": False, "org_id": "default"},
        },
        "/api/status": {
            "json_body": {"org_id": "default", "runtime": {}, "active_sessions": 0},
        },
    }


def test_smoke_test_passes_for_basic_live_app():
    result = smoke_test.run_smoke_test(args(), request_func=fake_request_factory(base_responses()))

    assert result["ok"] is True
    assert [check["name"] for check in result["checks"]] == ["health", "ready", "status"]


def test_smoke_test_can_require_models():
    result = smoke_test.run_smoke_test(
        args(require_models=True),
        request_func=fake_request_factory(base_responses()),
    )

    assert result["ok"] is False
    assert result["checks"][1]["name"] == "ready"
    assert result["checks"][1]["message"] == "models are not ready"


def test_smoke_test_requires_admin_token_for_protected_checks():
    result = smoke_test.run_smoke_test(
        args(require_metrics=True, require_ollama=True),
        request_func=fake_request_factory(base_responses()),
    )

    assert result["ok"] is False
    assert result["checks"][-1]["name"] == "admin_auth"
    assert "admin token is required" in result["checks"][-1]["message"]


def test_smoke_test_checks_metrics_and_ollama_with_token():
    responses = base_responses()
    responses.update(
        {
            "/api/admin/check": {
                "json_body": {"authenticated": True},
            },
            "/api/metrics": {
                "text_body": "vox_app_info 1\n",
            },
            "/api/ollama/health?force=1": {
                "json_body": {"reachable": True, "missing_models": []},
            },
        }
    )

    result = smoke_test.run_smoke_test(
        args(admin_token="secret", require_metrics=True, require_ollama=True),
        request_func=fake_request_factory(responses),
    )

    assert result["ok"] is True
    assert [check["name"] for check in result["checks"]] == [
        "health",
        "ready",
        "status",
        "admin_auth",
        "metrics",
        "ollama",
    ]
