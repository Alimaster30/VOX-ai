from typing import Any, Dict


def check_ollama_health(llm_model: str, embedding_model: str) -> Dict[str, Any]:
    required = [model for model in [llm_model, embedding_model] if model]
    result: Dict[str, Any] = {
        "reachable": False,
        "required_models": required,
        "available_models": [],
        "missing_models": required,
        "error": None,
    }

    try:
        import ollama

        response = ollama.list()
        models = response.get("models", []) if isinstance(response, dict) else getattr(response, "models", [])
        available = []
        for item in models:
            if isinstance(item, dict):
                name = item.get("name") or item.get("model")
            else:
                name = getattr(item, "name", None) or getattr(item, "model", None)
            if name:
                available.append(str(name))

        available_set = {name.split(":")[0] if ":" not in name else name for name in available}
        result["reachable"] = True
        result["available_models"] = available
        result["missing_models"] = [
            model for model in required
            if model not in available_set and model not in available
        ]
        return result
    except Exception as exc:
        result["error"] = str(exc)
        return result


def ollama_is_ready(llm_model: str, embedding_model: str) -> bool:
    health = check_ollama_health(llm_model, embedding_model)
    return bool(health["reachable"] and not health["missing_models"])
