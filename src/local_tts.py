import base64
import io
import logging
import queue
import tempfile
import wave
from pathlib import Path
from threading import Lock
from typing import Any


logger = logging.getLogger(__name__)

KOKORO_SAMPLE_RATE = 24000
KOKORO_CHANNELS = 1
KOKORO_SAMPLE_WIDTH = 2

_kokoro_engines: dict[str, Any] = {}
_kokoro_lock = Lock()


def wav_base64_from_pcm_chunks(chunks: list[bytes], sample_rate: int = KOKORO_SAMPLE_RATE) -> str:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(KOKORO_CHANNELS)
        wav_file.setsampwidth(KOKORO_SAMPLE_WIDTH)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b"".join(chunks))
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def empty_result(engine: str, error: str | None = None) -> dict[str, Any]:
    return {
        "audio_base64": None,
        "audio_mime": None,
        "tts_engine": engine,
        "tts_error": error,
    }


def audio_result(audio_base64: str, mime: str, engine: str) -> dict[str, Any]:
    return {
        "audio_base64": audio_base64,
        "audio_mime": mime,
        "tts_engine": engine,
        "tts_error": None,
    }


def drain_queue(audio_queue) -> list[bytes]:
    chunks = []
    while True:
        try:
            chunks.append(audio_queue.get_nowait())
        except queue.Empty:
            return chunks


def kokoro_voice_for_language(language: str, voice_en: str, voice_ur: str) -> str:
    if language == "ur" and voice_ur:
        return voice_ur
    return voice_en or "af_heart"


def get_kokoro_engine(voice: str):
    with _kokoro_lock:
        engine = _kokoro_engines.get(voice)
        if engine is not None:
            return engine

        from RealtimeTTS import KokoroEngine

        engine = KokoroEngine(voice=voice)
        _kokoro_engines[voice] = engine
        return engine


def synthesize_kokoro(text: str, language: str, voice_en: str, voice_ur: str) -> dict[str, Any]:
    voice = kokoro_voice_for_language(language, voice_en, voice_ur)
    try:
        engine = get_kokoro_engine(voice)
        drain_queue(engine.queue)
        if not engine.synthesize(text):
            return empty_result("kokoro", "Kokoro synthesis failed.")
        chunks = drain_queue(engine.queue)
        if not chunks:
            return empty_result("kokoro", "Kokoro produced no audio.")
        return audio_result(wav_base64_from_pcm_chunks(chunks), "audio/wav", "kokoro")
    except Exception as exc:
        logger.warning("Kokoro TTS unavailable: %s", exc)
        return empty_result("kokoro", str(exc))


def synthesize_gtts(text: str, language: str) -> dict[str, Any]:
    try:
        from gtts import gTTS

        buffer = io.BytesIO()
        gTTS(text=text, lang=language).write_to_fp(buffer)
        return audio_result(base64.b64encode(buffer.getvalue()).decode("ascii"), "audio/mpeg", "gtts")
    except Exception as exc:
        logger.warning("gTTS fallback failed: %s", exc)
        return empty_result("gtts", str(exc))


def synthesize_edge(text: str, language: str) -> dict[str, Any]:
    try:
        from src.tts.edge_tts import EdgeTTS

        voice = "ur_female" if language == "ur" else "en_female"
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            output_path = tmp.name
        try:
            generated = EdgeTTS().synthesize(text, voice=voice, output_path=output_path)
            if not generated or not Path(generated).exists():
                return empty_result("edge", "Edge TTS produced no audio.")
            data = Path(generated).read_bytes()
            return audio_result(base64.b64encode(data).decode("ascii"), "audio/mpeg", "edge")
        finally:
            try:
                Path(output_path).unlink(missing_ok=True)
            except Exception:
                pass
    except Exception as exc:
        logger.warning("Edge TTS fallback failed: %s", exc)
        return empty_result("edge", str(exc))


def synthesize_with_engine(
    engine: str,
    text: str,
    language: str,
    voice_en: str = "af_heart",
    voice_ur: str = "",
) -> dict[str, Any]:
    engine = (engine or "none").strip().lower()
    if engine == "kokoro":
        return synthesize_kokoro(text, language, voice_en, voice_ur)
    if engine == "gtts":
        return synthesize_gtts(text, language)
    if engine == "edge":
        return synthesize_edge(text, language)
    if engine == "none":
        return empty_result("none")
    return empty_result(engine, f"Unsupported TTS engine: {engine}")


def synthesize_speech(
    text: str,
    language: str,
    engine: str = "kokoro",
    fallback_engine: str = "none",
    voice_en: str = "af_heart",
    voice_ur: str = "",
) -> dict[str, Any]:
    if not text:
        return empty_result(engine, "No text to synthesize.")

    primary = synthesize_with_engine(engine, text, language, voice_en=voice_en, voice_ur=voice_ur)
    if primary.get("audio_base64") or not fallback_engine or fallback_engine == "none" or fallback_engine == engine:
        return primary

    fallback = synthesize_with_engine(fallback_engine, text, language, voice_en=voice_en, voice_ur=voice_ur)
    if fallback.get("audio_base64"):
        return fallback
    return primary
