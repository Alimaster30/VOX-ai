import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from urllib.parse import urljoin


@dataclass
class HttpResult:
    status_code: int
    elapsed_ms: float
    json_body: dict | None = None
    text_body: str = ""
    error: str | None = None


@dataclass
class CheckResult:
    name: str
    ok: bool
    status_code: int
    elapsed_ms: float
    message: str


def parse_json_body(body: bytes) -> dict | None:
    if not body:
        return None
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def build_headers(admin_token: str | None = None, org_id: str | None = None) -> dict[str, str]:
    headers = {
        "User-Agent": "vox-smoke-test/1.0",
        "X-Request-ID": "vox-smoke-test",
    }
    if admin_token:
        headers["X-VOX-Admin-Token"] = admin_token
    if org_id:
        headers["X-VOX-Org-ID"] = org_id
    return headers


def perform_request(
    base_url: str,
    path: str,
    timeout: float,
    admin_token: str | None = None,
    org_id: str | None = None,
) -> HttpResult:
    url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    request = urllib.request.Request(url, headers=build_headers(admin_token=admin_token, org_id=org_id), method="GET")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            text = body.decode("utf-8", errors="replace")
            return HttpResult(
                status_code=response.getcode(),
                elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
                json_body=parse_json_body(body),
                text_body=text,
            )
    except urllib.error.HTTPError as exc:
        body = exc.read()
        text = body.decode("utf-8", errors="replace")
        return HttpResult(
            status_code=exc.code,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
            json_body=parse_json_body(body),
            text_body=text,
            error=str(exc),
        )
    except Exception as exc:
        return HttpResult(
            status_code=0,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
            error=str(exc),
        )


def check_http_json(name: str, result: HttpResult, required_keys: tuple[str, ...] = ()) -> CheckResult:
    if result.status_code < 200 or result.status_code >= 300:
        return CheckResult(name, False, result.status_code, result.elapsed_ms, result.error or "unexpected status")
    if result.json_body is None:
        return CheckResult(name, False, result.status_code, result.elapsed_ms, "response is not JSON")
    missing = [key for key in required_keys if key not in result.json_body]
    if missing:
        return CheckResult(name, False, result.status_code, result.elapsed_ms, f"missing keys: {', '.join(missing)}")
    return CheckResult(name, True, result.status_code, result.elapsed_ms, "ok")


def run_smoke_test(args: argparse.Namespace, request_func=perform_request) -> dict:
    checks: list[CheckResult] = []

    def get(path: str) -> HttpResult:
        return request_func(
            args.base_url,
            path,
            args.timeout,
            admin_token=args.admin_token,
            org_id=args.org_id,
        )

    health = get("/api/health")
    checks.append(check_http_json("health", health, required_keys=("status", "models", "org_id")))

    ready = get("/api/ready")
    ready_check = check_http_json("ready", ready, required_keys=("app_ready", "ready", "org_id"))
    if ready_check.ok and not ready.json_body.get("app_ready"):
        ready_check = CheckResult("ready", False, ready.status_code, ready.elapsed_ms, "app_ready is false")
    if ready_check.ok and args.require_models and not ready.json_body.get("ready"):
        ready_check = CheckResult("ready", False, ready.status_code, ready.elapsed_ms, "models are not ready")
    checks.append(ready_check)

    status = get("/api/status")
    checks.append(check_http_json("status", status, required_keys=("org_id", "runtime", "active_sessions")))

    if args.admin_token:
        admin = get("/api/admin/check")
        checks.append(check_http_json("admin_auth", admin, required_keys=("authenticated",)))
    elif args.require_metrics or args.require_ollama:
        checks.append(CheckResult("admin_auth", False, 0, 0, "admin token is required for metrics or Ollama checks"))

    can_run_protected_checks = bool(args.admin_token)

    if args.require_metrics and can_run_protected_checks:
        metrics = get("/api/metrics")
        ok = 200 <= metrics.status_code < 300 and "vox_app_info" in metrics.text_body
        message = "ok" if ok else metrics.error or "metrics output missing vox_app_info"
        checks.append(CheckResult("metrics", ok, metrics.status_code, metrics.elapsed_ms, message))

    if args.require_ollama and can_run_protected_checks:
        ollama = get("/api/ollama/health?force=1")
        ollama_check = check_http_json("ollama", ollama, required_keys=("reachable",))
        if ollama_check.ok and not ollama.json_body.get("reachable"):
            ollama_check = CheckResult("ollama", False, ollama.status_code, ollama.elapsed_ms, "Ollama is not reachable")
        if ollama_check.ok and ollama.json_body.get("missing_models"):
            ollama_check = CheckResult(
                "ollama",
                False,
                ollama.status_code,
                ollama.elapsed_ms,
                f"missing models: {', '.join(ollama.json_body.get('missing_models', []))}",
            )
        checks.append(ollama_check)

    return {
        "ok": all(check.ok for check in checks),
        "base_url": args.base_url,
        "org_id": args.org_id,
        "checks": [asdict(check) for check in checks],
    }


def print_text_report(result: dict) -> None:
    print(f"VOX smoke test: {'PASS' if result['ok'] else 'FAIL'}")
    print(f"Base URL: {result['base_url']}")
    if result.get("org_id"):
        print(f"Organization: {result['org_id']}")
    for check in result["checks"]:
        marker = "PASS" if check["ok"] else "FAIL"
        print(f"[{marker}] {check['name']} status={check['status_code']} time={check['elapsed_ms']}ms - {check['message']}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run production smoke checks against a live VOX service.")
    parser.add_argument("--base-url", default="http://127.0.0.1:5000", help="VOX base URL.")
    parser.add_argument("--admin-token", default=None, help="Admin/read token for protected checks.")
    parser.add_argument("--org-id", default=None, help="Optional organization id to test via X-VOX-Org-ID.")
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout per check in seconds.")
    parser.add_argument("--require-models", action="store_true", help="Fail unless /api/ready reports models ready.")
    parser.add_argument("--require-metrics", action="store_true", help="Check /api/metrics. Requires --admin-token.")
    parser.add_argument("--require-ollama", action="store_true", help="Check /api/ollama/health. Requires --admin-token.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = run_smoke_test(args)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print_text_report(result)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
