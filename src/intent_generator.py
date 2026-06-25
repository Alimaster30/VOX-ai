import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List

from src.dataset_manager import now_iso, org_root


DRAFT_NAME = "generated_intents_draft.json"


def draft_path(profile: Dict[str, Any]) -> Path:
    root = org_root(profile)
    root.mkdir(parents=True, exist_ok=True)
    return root / DRAFT_NAME


def compact_context(manifest: Dict[str, Any], max_chunks: int = 16, max_chars: int = 12000) -> str:
    parts = []
    used = 0
    for chunk in manifest.get("chunks", [])[:max_chunks]:
        text = (chunk.get("text") or "").strip()
        if not text:
            continue
        source = chunk.get("source", "unknown source")
        block = f"Source: {source}\n{text}"
        if used + len(block) > max_chars:
            remaining = max_chars - used
            if remaining <= 200:
                break
            block = block[:remaining]
        parts.append(block)
        used += len(block)
        if used >= max_chars:
            break
    return "\n\n---\n\n".join(parts)


def sanitize_tag(text: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", text.lower()).strip("_")
    return text or "general_information"


def fallback_intents_from_chunks(profile: Dict[str, Any], manifest: Dict[str, Any]) -> Dict[str, Any]:
    intents = []
    seen_sources = set()
    for chunk in manifest.get("chunks", []):
        source = chunk.get("source") or "organization_info"
        if source in seen_sources:
            continue
        seen_sources.add(source)
        tag = sanitize_tag(Path(source).stem)
        text = (chunk.get("text") or "").strip()
        preview = text[:500]
        intents.append({
            "tag": tag,
            "patterns": [
                f"Tell me about {Path(source).stem}",
                f"What is in {Path(source).stem}?",
                f"Information about {Path(source).stem}",
                f"{Path(source).stem} details",
            ],
            "responses_urdu": [],
            "responses_english": [
                preview or f"Information is available in {source}."
            ],
            "source_chunks": [chunk.get("chunk_id")],
            "generation_method": "fallback",
        })
        if len(intents) >= 8:
            break
    return {
        "org_id": profile.get("org_id", "default"),
        "organization_name": profile.get("organization_name", ""),
        "generated_at": now_iso(),
        "status": "draft",
        "generation_method": "fallback",
        "intents": intents,
    }


def extract_json_object(text: str) -> Dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(cleaned[start:end + 1])


def normalize_generated_intents(raw: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, Any]:
    intents = raw.get("intents", [])
    normalized = []
    for item in intents:
        tag = sanitize_tag(str(item.get("tag") or item.get("name") or "general_information"))
        patterns = [str(p).strip() for p in item.get("patterns", []) if str(p).strip()]
        responses_urdu = [str(r).strip() for r in item.get("responses_urdu", []) if str(r).strip()]
        responses_english = [str(r).strip() for r in item.get("responses_english", []) if str(r).strip()]
        if not patterns:
            patterns = [tag.replace("_", " ")]
        if not responses_urdu and not responses_english:
            responses_english = [f"Please refer to {profile.get('organization_name', 'the organization')} documentation for this information."]
        normalized.append({
            "tag": tag,
            "patterns": patterns[:20],
            "responses_urdu": responses_urdu[:3],
            "responses_english": responses_english[:3],
            "source_chunks": item.get("source_chunks", []),
            "generation_method": "qwen",
        })
    return {
        "org_id": profile.get("org_id", "default"),
        "organization_name": profile.get("organization_name", ""),
        "generated_at": now_iso(),
        "status": "draft",
        "generation_method": "qwen",
        "intents": normalized,
    }


def build_generation_prompt(profile: Dict[str, Any], context: str, max_intents: int) -> str:
    return f"""
You are generating draft intents for VOX, a configurable organization assistant.

Organization: {profile.get("organization_name", "Unknown organization")}
Domain: {profile.get("domain", "general")}

Use ONLY the provided organization context. Create up to {max_intents} useful intents.
Each intent must be answerable from the context.

Return ONLY valid JSON in this exact shape:
{{
  "intents": [
    {{
      "tag": "short_snake_case_name",
      "patterns": ["user question 1", "user question 2", "roman/alternate wording if useful"],
      "responses_urdu": ["Urdu answer if appropriate, otherwise empty"],
      "responses_english": ["English answer grounded in the context"],
      "source_chunks": ["optional chunk ids if known"]
    }}
  ]
}}

Organization context:
{context}
""".strip()


def generate_intent_draft(profile: Dict[str, Any], manifest: Dict[str, Any], max_intents: int = 12) -> Dict[str, Any]:
    context = compact_context(manifest)
    if not context:
        draft = fallback_intents_from_chunks(profile, manifest)
        save_intent_draft(profile, draft)
        return draft

    try:
        from langchain_ollama import OllamaLLM

        llm = OllamaLLM(model=profile.get("llm_model", "qwen3.2:3b"))
        raw_text = llm.invoke(build_generation_prompt(profile, context, max_intents))
        raw_json = extract_json_object(raw_text)
        draft = normalize_generated_intents(raw_json, profile)
        if not draft["intents"]:
            draft = fallback_intents_from_chunks(profile, manifest)
    except Exception as exc:
        draft = fallback_intents_from_chunks(profile, manifest)
        draft["generation_error"] = str(exc)

    save_intent_draft(profile, draft)
    return draft


def save_intent_draft(profile: Dict[str, Any], draft: Dict[str, Any]) -> None:
    path = draft_path(profile)
    with path.open("w", encoding="utf-8") as f:
        json.dump(draft, f, ensure_ascii=False, indent=2)


def load_intent_draft(profile: Dict[str, Any]) -> Dict[str, Any] | None:
    path = draft_path(profile)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def active_intents_path(profile: Dict[str, Any]) -> Path:
    return Path(profile["intents_path"])


def load_active_intents(profile: Dict[str, Any]) -> Dict[str, Any]:
    path = active_intents_path(profile)
    if not path.exists():
        return {"intents": []}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if "intents" not in data or not isinstance(data["intents"], list):
        raise ValueError(f"Invalid intents file: {path}")
    return data


def normalize_intent_for_publish(intent: Dict[str, Any]) -> Dict[str, Any]:
    tag = sanitize_tag(str(intent.get("tag") or intent.get("name") or "general_information"))
    patterns = unique_strings(intent.get("patterns", []))
    responses_urdu = unique_strings(intent.get("responses_urdu", []))
    responses_english = unique_strings(intent.get("responses_english", []))

    if not patterns:
        patterns = [tag.replace("_", " ")]
    if not responses_urdu and not responses_english:
        responses_english = ["I found this topic in the organization dataset, but it needs a reviewed response."]

    return {
        "tag": tag,
        "patterns": patterns,
        "responses_urdu": responses_urdu,
        "responses_english": responses_english,
    }


def unique_strings(values: List[Any]) -> List[str]:
    seen = set()
    result = []
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def merge_intent_lists(
    active_intents: List[Dict[str, Any]],
    draft_intents: List[Dict[str, Any]],
    replace_existing: bool = False,
) -> List[Dict[str, Any]]:
    merged = [normalize_intent_for_publish(intent) for intent in active_intents]
    tag_to_index = {intent["tag"]: index for index, intent in enumerate(merged)}

    for draft_intent in draft_intents:
        normalized = normalize_intent_for_publish(draft_intent)
        existing_index = tag_to_index.get(normalized["tag"])

        if existing_index is None:
            tag_to_index[normalized["tag"]] = len(merged)
            merged.append(normalized)
            continue

        if replace_existing:
            merged[existing_index] = normalized
            continue

        existing = merged[existing_index]
        existing["patterns"] = unique_strings(existing.get("patterns", []) + normalized.get("patterns", []))
        existing["responses_urdu"] = unique_strings(existing.get("responses_urdu", []) + normalized.get("responses_urdu", []))
        existing["responses_english"] = unique_strings(existing.get("responses_english", []) + normalized.get("responses_english", []))

    return merged


def publish_intent_draft(profile: Dict[str, Any], mode: str = "merge") -> Dict[str, Any]:
    if mode not in {"merge", "replace"}:
        raise ValueError("mode must be either 'merge' or 'replace'")

    draft = load_intent_draft(profile)
    if draft is None:
        raise FileNotFoundError("No generated intent draft found.")

    active = load_active_intents(profile)
    active_path = active_intents_path(profile)
    active_path.parent.mkdir(parents=True, exist_ok=True)

    backup_path = None
    if active_path.exists():
        backup_path = active_path.with_name(f"{active_path.stem}_backup_{now_iso().replace(':', '-').replace('.', '-')}{active_path.suffix}")
        shutil.copyfile(active_path, backup_path)

    published_intents = merge_intent_lists(
        active.get("intents", []),
        draft.get("intents", []),
        replace_existing=mode == "replace",
    )
    published = {"intents": published_intents}

    with active_path.open("w", encoding="utf-8") as f:
        json.dump(published, f, ensure_ascii=False, indent=2)

    return {
        "status": "published",
        "mode": mode,
        "active_intents_before": len(active.get("intents", [])),
        "draft_intents": len(draft.get("intents", [])),
        "active_intents_after": len(published_intents),
        "published_at": now_iso(),
        "active_intents_path": str(active_path),
        "backup_path": str(backup_path) if backup_path else None,
    }
