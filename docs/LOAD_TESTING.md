# VOX Load Testing

Use `load_test.py` to simulate many callers against a running VOX server.
It uses only Python's standard library, so there are no extra dependencies.

## 1. Start VOX

Windows:

```bat
run_production.bat
```

Linux:

```bash
./run_production.sh
```

For staging tests, keep production-like settings in `.env`:

```bash
VOX_PRODUCTION=1
VOX_WAITRESS_THREADS=8
VOX_MAX_CONCURRENT_STT=2
VOX_MAX_CONCURRENT_QUERIES=4
VOX_RATE_LIMIT_VOICE=30
```

## 2. Test Session Concurrency

This checks whether the web server, session isolation, persistence, and request handling stay responsive:

```bat
.\.venv\Scripts\python.exe load_test.py --endpoint session --callers 20 --requests-per-caller 10
```

Target a specific organization:

```bat
.\.venv\Scripts\python.exe load_test.py --endpoint session --org-id acme-health --callers 20 --requests-per-caller 10
```

For voice load tests against a non-default organization, load that organization's runtime first:

```bat
curl -X POST http://localhost:5000/api/models/load -H "X-VOX-Admin-Token: <admin-token>" -H "X-VOX-Org-ID: acme-health"
```

Save the full report:

```bat
.\.venv\Scripts\python.exe load_test.py --endpoint session --callers 20 --requests-per-caller 10 --output load_test_results\session_20x10.json
```

## 3. Test Health and Status

Health is the lightest check:

```bat
.\.venv\Scripts\python.exe load_test.py --endpoint health --callers 50 --requests-per-caller 5
```

Status is heavier because it reads runtime, dataset, cache, and model health:

```bat
.\.venv\Scripts\python.exe load_test.py --endpoint status --callers 20 --requests-per-caller 5
```

## 4. Test Voice Calls

Voice testing needs a real WAV file:

```bat
.\.venv\Scripts\python.exe load_test.py --endpoint voice --audio-file samples\hello.wav --callers 5 --requests-per-caller 2 --timeout 120
```

This path exercises speech-to-text, intent/RAG routing, Qwen, answer caching, text-to-speech, session storage, and handoff creation.
Run this only after `/api/health` shows the models are ready.

## 5. Read the Result

The printed summary includes:

- `ok_count`: successful HTTP responses.
- `error_count`: non-2xx responses and connection failures.
- `rate_limited_count`: `429` responses. This means VOX protected itself when capacity was full.
- `model_unavailable_count`: `503` responses. This usually means models were still loading or failed to load.
- `latency_ms.p50` and `latency_ms.p95`: the normal and high-end response time.
- `cache_hits`: responses served from the answer cache.
- `layer_counts`: which answer layer handled voice responses when available.

## 6. Tune Capacity

If web/session latency is high, increase `VOX_WAITRESS_THREADS` or move to stronger hosting.

If voice calls return many `429` responses, raise these only if the machine has enough CPU/GPU capacity:

```bash
VOX_MAX_CONCURRENT_STT=2
VOX_MAX_CONCURRENT_QUERIES=4
VOX_RATE_LIMIT_VOICE=30
```

If voice calls are slow but not rate-limited, the main bottleneck is usually local STT, local embeddings, Qwen, or TTS.
Keep Qwen 3.2:3B, but use caching, smaller chunks, a strong embedding index, and conservative concurrency to keep the service stable.
