"""
Simple Working Speech-to-Text Module
"""
import logging
from pathlib import Path
from typing import Optional, Tuple
import tempfile
import os

logger = logging.getLogger(__name__)

class SimpleSTT:
    """Simple STT using SpeechRecognition with proper audio handling"""

    def __init__(self, model_size: str = "tiny", device: str = "cpu"):
        self.model_size = model_size
        self.device = device
        self.recognizer = None

    def load_model(self):
        """Initialize the speech recognizer"""
        try:
            import speech_recognition as sr
            self.recognizer = sr.Recognizer()
            # Adjust for ambient noise
            self.recognizer.energy_threshold = 300
            self.recognizer.dynamic_energy_threshold = True
            logger.info("Speech Recognition initialized")
        except ImportError as e:
            logger.error(f"speech_recognition not installed: {e}")
            raise

    def convert_to_wav(self, audio_path: str) -> str:
        """Convert audio file to WAV format"""
        try:
            from src.audio_runtime import configure_pydub_runtime
            configure_pydub_runtime()
            from pydub import AudioSegment
            
            # Load audio file
            audio = AudioSegment.from_file(audio_path)
            
            # Convert to WAV with proper settings
            audio = audio.set_frame_rate(16000).set_channels(1)
            
            # Save to temporary WAV file
            temp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            audio.export(temp_wav.name, format="wav")
            temp_wav.close()
            
            return temp_wav.name
            
        except Exception as e:
            logger.error(f"Audio conversion failed: {e}")
            # Return original path if conversion fails
            return audio_path

    def transcribe(self, audio_path: str, language: Optional[str] = None) -> Tuple[str, str, float]:
        """
        Transcribe audio file to text
        
        Args:
            audio_path: Path to audio file
            language: Language code (ur, en, or None for auto-detect)
            
        Returns:
            Tuple of (transcribed_text, detected_language, confidence)
        """
        import speech_recognition as sr
        
        if self.recognizer is None:
            self.load_model()

        # Convert audio to proper format
        wav_path = self.convert_to_wav(audio_path)
        
        try:
            # Load audio file
            with sr.AudioFile(wav_path) as source:
                # Adjust for ambient noise
                self.recognizer.adjust_for_ambient_noise(source, duration=0.5)
                # Record the audio
                audio = self.recognizer.record(source)

            # Try Urdu first
            try:
                text = self.recognizer.recognize_google(audio, language="ur-PK")
                if text.strip():
                    logger.info(f"Transcribed (Urdu): {text}")
                    return text, "ur", 0.85
            except (sr.UnknownValueError, sr.RequestError):
                pass

            # Try English
            try:
                text = self.recognizer.recognize_google(audio, language="en-US")
                if text.strip():
                    logger.info(f"Transcribed (English): {text}")
                    return text, "en", 0.80
            except (sr.UnknownValueError, sr.RequestError):
                pass

            # If both fail, return empty string so caller can handle it
            logger.warning("Could not transcribe audio")
            return "", "unknown", 0.0

        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            return "", "unknown", 0.0
            
        finally:
            # Clean up temporary file
            if wav_path != audio_path and os.path.exists(wav_path):
                try:
                    os.unlink(wav_path)
                except:
                    pass
