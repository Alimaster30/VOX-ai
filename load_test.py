import argparse
import json
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode


ENDPOINTS = {"health", "status", "session", "voice"}


def percentile(values, pct):
    if not values:
        return None
    ordered = sorted(values)
    index = round((len(ordered) - 1) * (pct / 100))
    return ordered[int(index)]


def endpoint_url(base_url, endpoint, call_id):
    base = base_url.rstrip("/")
    if endpoint == "health":
        return f"{base}/api/health"
    if endpoint == "status":
        return f"{base}/api/status?{urlencode({'call_id': call_id})}"
    if endpoint == "session":
        return f"{base}/api/session?{urlencode({'call_id': call_id})}"
    if endpoint == "voice":
        return f"{base}/api/voice?{urlencode({'call_id': call_id})}"
    raise ValueError(f"Unsupported endpoint: {endpoint}")


def parse_json_body(body):
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return None


def request_once(
    base_url,
    endpoint,
    call_id,
    request_index,
    timeout,
    admin_token=None,
    org_id=None,
    audio_bytes=None,
):
    url = endpoint_url(base_url, endpoint, call_id)
    headers = {
        "User-Agent": "vox-load-test/1.0",
        "X-VOX-Call-ID": call_id,
    }
    if admin_token:
        headers["X-VOX-Admin-Token"] = admin_token
    if org_id:
        headers["X-VOX-Org-ID"] = org_id

    data = None
    method = "GET"
    if endpoint == "voice":
        data = audio_bytes or b""
        method = "POST"
        headers["Content-Type"] = "audio/wav"

    started = time.perf_counter()
    try:
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            status_code = response.getcode()
            parsed = parse_json_body(body)
            error = None
    except urllib.error.HTTPError as exc:
        body = exc.read()
        status_code = exc.code
        parsed = parse_json_body(body)
        error = parsed.get("error") if isinstance(parsed, dict) else str(exc)
    except Exception as exc:
        status_code = 0
        parsed = None
        error = str(exc)

    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    return {
        "call_id": call_id,
        "request_index": request_index,
        "endpoint": endpoint,
        "status_code": status_code,
        "elapsed_ms": elapsed_ms,
        "error": error,
        "json": parsed,
    }


def summarize_results(results):
    latencies = [result["elapsed_ms"] for result in results if result.get("status_code")]
    status_counts = {}
    layer_counts = {}
    errors = []
    cache_hits = 0

    for result in results:
        status = str(result.get("status_code", 0))
        status_counts[status] = status_counts.get(status, 0) + 1

        payload = result.get("json") if isinstance(result.get("json"), dict) else {}
        if payload.get("cached") is True:
            cache_hits += 1
        if payload.get("layer") is not None:
            layer = str(payload["layer"])
            layer_counts[layer] = layer_counts.get(layer, 0) + 1
        if result.get("error"):
            errors.append({
                "call_id": result.get("call_id"),
                "status_code": result.get("status_code"),
                "error": result.get("error"),
            })

    ok_count = sum(1 for result in results if 200 <= int(result.get("status_code", 0)) < 300)
    total = len(results)
    avg_ms = round(sum(latencies) / len(latencies), 2) if latencies else None

    return {
        "total_requests": total,
        "ok_count": ok_count,
        "error_count": total - ok_count,
        "status_counts": status_counts,
        "latency_ms": {
            "min": min(latencies) if latencies else None,
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
            "max": max(latencies) if latencies else None,
            "avg": avg_ms,
        },
        "cache_hits": cache_hits,
        "rate_limited_count": status_counts.get("429", 0),
        "model_unavailable_count": status_counts.get("503", 0),
        "layer_counts": layer_counts,
        "error_samples": errors[:10],
    }


def load_audio(path):
    if not path:
        return None
    audio_path = Path(path)
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")
    return audio_path.read_bytes()


def run_load_test(args):
    audio_bytes = load_audio(args.audio_file)
    if args.endpoint == "voice" and not audio_bytes:
        raise ValueError("--audio-file is required when --endpoint voice is used")

    started_at = datetime.now(timezone.utc).isoformat()
    futures = []
    with ThreadPoolExecutor(max_workers=args.callers) as executor:
        for caller_index in range(args.callers):
            call_id = f"{args.call_id_prefix}-{caller_index + 1}"
            for request_index in range(args.requests_per_caller):
                futures.append(executor.submit(
                    request_once,
                    args.base_url,
                    args.endpoint,
                    call_id,
                    request_index + 1,
                    args.timeout,
                    args.admin_token,
                    args.org_id,
                    audio_bytes,
                ))

        results = [future.result() for future in as_completed(futures)]

    finished_at = datetime.now(timezone.utc).isoformat()
    summary = summarize_results(results)
    summary.update({
        "base_url": args.base_url,
        "endpoint": args.endpoint,
        "org_id": args.org_id,
        "callers": args.callers,
        "requests_per_caller": args.requests_per_caller,
        "started_at": started_at,
        "finished_at": finished_at,
    })
    return {"summary": summary, "results": sorted(results, key=lambda item: (item["call_id"], item["request_index"]))}


def parse_args():
    parser = argparse.ArgumentParser(description="Run a lightweight VOX HTTP load test.")
    parser.add_argument("--base-url", default="http://127.0.0.1:5000", help="VOX server base URL.")
    parser.add_argument("--endpoint", choices=sorted(ENDPOINTS), default="session", help="Endpoint to test.")
    parser.add_argument("--callers", type=int, default=10, help="Number of concurrent caller sessions.")
    parser.add_argument("--requests-per-caller", type=int, default=5, help="Requests to send per caller.")
    parser.add_argument("--call-id-prefix", default="load-caller", help="Prefix used for generated call IDs.")
    parser.add_argument("--org-id", help="Optional organization id sent as X-VOX-Org-ID.")
    parser.add_argument("--audio-file", help="WAV file to send when testing /api/voice.")
    parser.add_argument("--admin-token", default=os.getenv("VOX_ADMIN_TOKEN"), help="Optional admin token header.")
    parser.add_argument("--timeout", type=float, default=30.0, help="Per-request timeout in seconds.")
    parser.add_argument("--output", help="Optional JSON output path for full results.")
    return parser.parse_args()


def validate_args(args):
    if args.callers < 1:
        raise ValueError("--callers must be at least 1")
    if args.requests_per_caller < 1:
        raise ValueError("--requests-per-caller must be at least 1")
    if args.timeout <= 0:
        raise ValueError("--timeout must be greater than 0")


def main():
    args = parse_args()
    validate_args(args)
    report = run_load_test(args)

    print(json.dumps(report["summary"], indent=2))
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nFull results written to {output_path}")


if __name__ == "__main__":
    main()
