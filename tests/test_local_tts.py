import base64
import wave
from io import BytesIO

from src.local_tts import synthesize_speech, wav_base64_from_pcm_chunks


def test_wav_base64_from_pcm_chunks_returns_valid_wav():
    pcm = (b"\x00\x00\xff\x7f" * 20)

    encoded = wav_base64_from_pcm_chunks([pcm], sample_rate=24000)
    data = base64.b64decode(encoded)

    with wave.open(BytesIO(data), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 24000
        assert wav_file.readframes(40) == pcm


def test_synthesize_speech_none_engine_returns_no_audio():
    result = synthesize_speech("Hello", "en", engine="none", fallback_engine="none")

    assert result["audio_base64"] is None
    assert result["audio_mime"] is None
    assert result["tts_engine"] == "none"


def test_synthesize_speech_falls_back_when_primary_fails(monkeypatch):
    def fake_primary(*args, **kwargs):
        return {
            "audio_base64": None,
            "audio_mime": None,
            "tts_engine": "kokoro",
            "tts_error": "not available",
        }

    def fake_fallback(*args, **kwargs):
        return {
            "audio_base64": "abc",
            "audio_mime": "audio/mpeg",
            "tts_engine": "gtts",
            "tts_error": None,
        }

    monkeypatch.setattr("src.local_tts.synthesize_kokoro", fake_primary)
    monkeypatch.setattr("src.local_tts.synthesize_gtts", fake_fallback)

    result = synthesize_speech("Hello", "en", engine="kokoro", fallback_engine="gtts")

    assert result["audio_base64"] == "abc"
    assert result["audio_mime"] == "audio/mpeg"
    assert result["tts_engine"] == "gtts"
