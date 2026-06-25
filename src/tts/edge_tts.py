"""
Text-to-Speech Module using Edge-TTS
Supports Urdu and English voices
"""
import asyncio
import logging
from pathlib import Path
from typing import Optional
import tempfile
import warnings

# Suppress asyncio warnings on Windows
warnings.filterwarnings("ignore", category=RuntimeWarning, module="asyncio")

logger = logging.getLogger(__name__)


class EdgeTTS:
    """Text-to-Speech using Microsoft Edge TTS (free)"""
    
    # Available Urdu and English voices
    VOICES = {
        "ur_female": "ur-PK-UzmaNeural",
        "ur_male": "ur-PK-AsadNeural",
        "en_female": "en-US-JennyNeural",
        "en_male": "en-US-GuyNeural",
        "en_uk_female": "en-GB-SoniaNeural",
    }
    
    def __init__(
        self,
        default_voice: str = "ur_female",
        rate: str = "+0%",
        volume: str = "+0%",
        output_dir: Optional[str] = None,
    ):
        """
        Initialize Edge TTS
        
        Args:
            default_voice: Default voice key (ur_female, ur_male, en_female, en_male)
            rate: Speech rate adjustment (e.g., "+10%", "-10%")
            volume: Volume adjustment (e.g., "+10%", "-10%")
            output_dir: Directory to save audio files
        """
        self.default_voice = self.VOICES.get(default_voice, self.VOICES["ur_female"])
        self.rate = rate
        self.volume = volume
        self.output_dir = Path(output_dir) if output_dir else Path(tempfile.gettempdir())
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    async def synthesize_async(
        self,
        text: str,
        voice: Optional[str] = None,
        output_path: Optional[str] = None,
    ) -> str:
        """
        Synthesize text to speech asynchronously
        
        Args:
            text: Text to synthesize
            voice: Voice key or full voice name
            output_path: Output file path (optional)
            
        Returns:
            Path to generated audio file
        """
        import edge_tts
        
        # Resolve voice
        if voice is None:
            voice_name = self.default_voice
        elif voice in self.VOICES:
            voice_name = self.VOICES[voice]
        else:
            voice_name = voice
        
        # Generate output path if not provided
        if output_path is None:
            import uuid
            output_path = self.output_dir / f"tts_{uuid.uuid4().hex[:8]}.mp3"
        else:
            output_path = Path(output_path)
        
        try:
            communicate = edge_tts.Communicate(
                text,
                voice_name,
                rate=self.rate,
                volume=self.volume,
            )
            await communicate.save(str(output_path))
            
            logger.info(f"TTS generated: {output_path}")
            return str(output_path)
            
        except Exception as e:
            logger.error(f"TTS synthesis failed: {e}")
            raise
    
    def synthesize(
        self,
        text: str,
        voice: Optional[str] = None,
        output_path: Optional[str] = None,
    ) -> str:
        """
        Synthesize text to speech (synchronous wrapper)
        
        Args:
            text: Text to synthesize
            voice: Voice key or full voice name
            output_path: Output file path (optional)
            
        Returns:
            Path to generated audio file
        """
        try:
            # Suppress asyncio error logging temporarily
            asyncio_logger = logging.getLogger('asyncio')
            original_level = asyncio_logger.level
            asyncio_logger.setLevel(logging.CRITICAL)
            
            # Use new event loop to avoid connection issues
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(self.synthesize_async(text, voice, output_path))
                return result
            finally:
                try:
                    loop.close()
                except:
                    pass
                # Restore asyncio logging
                asyncio_logger.setLevel(original_level)
        except Exception as e:
            logger.error(f"TTS synthesis error: {e}")
            # Return a dummy path if TTS fails
            return ""
    
    def synthesize_urdu(self, text: str, output_path: Optional[str] = None) -> str:
        """Synthesize Urdu text"""
        return self.synthesize(text, voice="ur_female", output_path=output_path)
    
    def synthesize_english(self, text: str, output_path: Optional[str] = None) -> str:
        """Synthesize English text"""
        return self.synthesize(text, voice="en_female", output_path=output_path)
    
    @classmethod
    async def list_voices(cls, language_filter: Optional[str] = None) -> list:
        """
        List available voices
        
        Args:
            language_filter: Filter by language code (e.g., "ur", "en")
            
        Returns:
            List of available voices
        """
        import edge_tts
        
        voices = await edge_tts.list_voices()
        
        if language_filter:
            voices = [v for v in voices if v["Locale"].startswith(language_filter)]
        
        return voices
    
    @classmethod
    def list_voices_sync(cls, language_filter: Optional[str] = None) -> list:
        """Synchronous wrapper for list_voices"""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(cls.list_voices(language_filter))
            finally:
                loop.close()
        except Exception as e:
            logger.error(f"Failed to list voices: {e}")
            return []

