"""
Speech-to-Text Module
Uses SpeechRecognition with Google API (works on Windows without DLL issues)
"""
import logging
from pathlib import Path
from typing import Optional, Tuple
import numpy as np

logger = logging.getLogger(__name__)


class WhisperSTT:
    """Speech-to-Text using Google Speech Recognition API"""

    def __init__(
        self,
        model_size: str = "base",
        device: str = "cpu",
        compute_type: str = "int8",
    ):
        """
        Initialize STT

        Args:
            model_size: Not used (kept for compatibility)
            device: Not used (kept for compatibility)
            compute_type: Not used (kept for compatibility)
        """
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.recognizer = None

    def load_model(self):
        """Initialize the speech recognizer"""
        try:
            import speech_recognition as sr
            self.recognizer = sr.Recognizer()
            logger.info("Google Speech Recognition initialized")
        except ImportError as e:
            logger.error(f"speech_recognition not installed: {e}")
            raise
    
    def transcribe(
        self,
        audio_path: str,
        language: Optional[str] = None,
    ) -> Tuple[str, str, float]:
        """
        Transcribe audio file to text using Google Speech Recognition

        Args:
            audio_path: Path to audio file
            language: Language code (ur, en, or None for auto-detect)

        Returns:
            Tuple of (transcribed_text, detected_language, confidence)
        """
        import speech_recognition as sr

        if self.recognizer is None:
            self.load_model()

        # Map language codes to Google API format
        lang_map = {
            "ur": "ur-PK",
            "en": "en-US",
            None: "ur-PK",  # Default to Urdu
        }
        lang_code = lang_map.get(language, language or "ur-PK")

        try:
            with sr.AudioFile(audio_path) as source:
                audio = self.recognizer.record(source)

            # Try primary language
            try:
                text = self.recognizer.recognize_google(audio, language=lang_code)
                detected_lang = "ur" if "ur" in lang_code.lower() else "en"
                logger.info(f"Transcribed: {text[:50] if len(text) > 50 else text}...")
                return text, detected_lang, 0.85
            except sr.UnknownValueError:
                # Try English if Urdu fails
                if "ur" in lang_code.lower():
                    try:
                        text = self.recognizer.recognize_google(audio, language="en-US")
                        logger.info(f"Transcribed (EN): {text[:50] if len(text) > 50 else text}...")
                        return text, "en", 0.75
                    except sr.UnknownValueError:
                        pass
                logger.warning("Could not understand audio")
                return "", "unknown", 0.0

        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            raise

    def transcribe_audio_data(
        self,
        audio_data: np.ndarray,
        sample_rate: int = 16000,
        language: Optional[str] = None,
    ) -> Tuple[str, str, float]:
        """
        Transcribe audio data (numpy array) to text
        """
        import tempfile
        import soundfile as sf

        # Save to temporary WAV file
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            sf.write(f.name, audio_data, sample_rate)
            return self.transcribe(f.name, language=language)

