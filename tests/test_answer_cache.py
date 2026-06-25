import importlib
import sys


def reload_persistence(monkeypatch, tmp_path):
    monkeypatch.setenv("VOX_DB_PATH", str(tmp_path / "vox.sqlite3"))
    for name in list(sys.modules):
        if name in {"src.config", "src.persistence"}:
            sys.modules.pop(name, None)
    return importlib.import_module("src.persistence")


def test_answer_cache_round_trip(monkeypatch, tmp_path):
    persistence = reload_persistence(monkeypatch, tmp_path)

    response = {
        "response": "Hello",
        "intent": "greeting",
        "confidence": 0.9,
        "layer": 1,
        "language": "en",
        "layer_ms": 12,
        "total_ms": 20,
        "handoff_recommended": False,
    }
    persistence.store_cached_answer("demo", "en", "Hello?", response, ttl_seconds=60)

    cached = persistence.get_cached_answer("demo", "en", "hello")

    assert cached["response"] == "Hello"
    assert cached["cached"] is True
    assert cached["cache_hit_count"] == 1
    assert persistence.count_answer_cache("demo") == 1


def test_answer_cache_expiry(monkeypatch, tmp_path):
    persistence = reload_persistence(monkeypatch, tmp_path)

    response = {
        "response": "Hello",
        "intent": "greeting",
        "confidence": 0.9,
        "layer": 1,
        "language": "en",
        "handoff_recommended": False,
    }
    persistence.store_cached_answer("demo", "en", "Hello", response, ttl_seconds=1)
    key = persistence.answer_cache_key("demo", "en", "Hello")
    with persistence.connect() as conn:
        conn.execute(
            "UPDATE answer_cache SET expires_at = ? WHERE cache_key = ?",
            ("2000-01-01T00:00:00+00:00", key),
        )

    assert persistence.get_cached_answer("demo", "en", "Hello") is None
    assert persistence.count_answer_cache("demo") == 0
