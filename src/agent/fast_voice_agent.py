"""
Minimal Fast Voice Agent - Optimized for Speed
"""
import logging
import time
from pathlib import Path
from typing import Optional, Tuple
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

class AgentAction(Enum):
    RESPOND = "respond"
    TRANSFER = "transfer"

@dataclass
class AgentResponse:
    text: str
    audio_path: Optional[str]
    intent: str
    confidence: float
    action: AgentAction
    language: str
    processing_time: float = 0.0
    suggestions: list = None
    metadata: dict = None
    
    def __post_init__(self):
        if self.suggestions is None:
            self.suggestions = []
        if self.metadata is None:
            self.metadata = {}

class FastVoiceAgent:
    """Ultra-fast voice agent with minimal dependencies"""
    
    def __init__(self, intents_path: str, programs_path: Optional[str] = None):
        self.intents_path = intents_path
        self.programs_path = programs_path
        
        # Simple responses without complex NLP
        self.responses = {
            "greeting": {
                "ur": "وعلیکم السلام! میں ایئر یونیورسٹی ملتان کا ورچوئل اسسٹنٹ ہوں۔ آپ کا کیا سوال ہے؟",
                "en": "Hello! I am the virtual assistant of Air University Multan. How can I help you?"
            },
            "admission": {
                "ur": "داخلے کے لیے آپ کو آن لائن اپلائی کرنا ہوگا۔ کیا آپ کسی خاص پروگرام کے بارے میں جاننا چاہتے ہیں؟",
                "en": "For admission, you need to apply online. Would you like to know about a specific program?"
            },
            "fee": {
                "ur": "فیس پروگرام کے حساب سے مختلف ہے۔ کمپیوٹر سائنس کی فیس 8500 روپے فی کریڈٹ آور ہے۔",
                "en": "Fees vary by program. Computer Science is PKR 8,500 per credit hour."
            },
            "programs": {
                "ur": "ہمارے پاس کمپیوٹر سائنس، آرٹیفیشل انٹیلیجنس، ڈیٹا سائنس، اور BBA پروگرامز ہیں۔",
                "en": "We offer Computer Science, Artificial Intelligence, Data Science, and BBA programs."
            },
            "default": {
                "ur": "معذرت، میں آپ کا سوال سمجھ نہیں سکا۔ براہ کرم دوبارہ کوشش کریں۔",
                "en": "Sorry, I didn't understand your question. Please try again."
            }
        }
        
        self.metrics = {'total_queries': 0, 'successful_responses': 0, 'average_confidence': 0.0, 'average_processing_time': 0.0}
        self.active_sessions = {}
        
        logger.info("Fast Voice Agent initialized")
    
    def detect_language(self, text: str) -> str:
        """Fast language detection"""
        urdu_chars = sum(1 for c in text if '\u0600' <= c <= '\u06FF')
        total_chars = len([c for c in text if c.isalpha()])
        return "ur" if (total_chars > 0 and urdu_chars / total_chars > 0.3) else "en"
    
    def classify_intent(self, text: str) -> Tuple[str, float]:
        """Fast intent classification using keywords"""
        text_lower = text.lower()
        
        # Greeting keywords
        if any(word in text_lower for word in ['hello', 'hi', 'سلام', 'اسلام', 'assalam']):
            return "greeting", 0.9
        
        # Admission keywords
        if any(word in text_lower for word in ['admission', 'داخلہ', 'apply', 'dakhla']):
            return "admission", 0.8
        
        # Fee keywords
        if any(word in text_lower for word in ['fee', 'فیس', 'cost', 'tuition']):
            return "fee", 0.8
        
        # Programs keywords
        if any(word in text_lower for word in ['program', 'course', 'پروگرام', 'degree']):
            return "programs", 0.8
        
        return "default", 0.3
    
    def transcribe_audio(self, audio_path: str) -> Tuple[str, str, float]:
        """Mock transcription - replace with actual STT if needed"""
        # For demo purposes, return a greeting
        return "hello", "en", 0.9
    
    def synthesize_speech(self, text: str, language: str) -> Optional[str]:
        """Mock TTS - replace with actual TTS if needed"""
        # Skip TTS for speed
        return None
    
    def process_text(self, text: str, language: str = None, session_id: str = "default") -> AgentResponse:
        """Fast text processing"""
        start_time = time.time()
        
        # Auto-detect language
        if language is None:
            language = self.detect_language(text)
        
        # Classify intent
        intent, confidence = self.classify_intent(text)
        
        # Get response
        if intent in self.responses:
            response_text = self.responses[intent][language]
        else:
            response_text = self.responses["default"][language]
        
        # Generate audio (skip for speed)
        audio_path = None
        
        processing_time = time.time() - start_time
        
        return AgentResponse(
            text=response_text,
            audio_path=audio_path,
            intent=intent,
            confidence=confidence,
            action=AgentAction.RESPOND,
            language=language,
            processing_time=processing_time,
            suggestions=[],
            metadata={}
        )
    
    def process_audio(self, audio_path: str, session_id: str = "default") -> AgentResponse:
        """Fast audio processing"""
        try:
            # Mock transcription for speed
            text, detected_lang, stt_confidence = self.transcribe_audio(audio_path)
            
            # Process text
            response = self.process_text(text, language=detected_lang, session_id=session_id)
            
            # Add metadata
            response.metadata.update({
                'stt_confidence': stt_confidence,
                'transcribed_text': text,
                'detected_language': detected_lang
            })
            
            return response
            
        except Exception as e:
            logger.error(f"Audio processing failed: {e}")
            return AgentResponse(
                text="آڈیو پروسیسنگ میں خرابی ہوئی۔",
                audio_path=None,
                intent="error",
                confidence=0.0,
                action=AgentAction.TRANSFER,
                language="ur",
                processing_time=0.0,
                suggestions=[],
                metadata={'error': str(e)}
            )
    
    def get_metrics(self):
        """Get metrics"""
        return self.metrics
    
    def reset_session(self, session_id: str = "default"):
        """Reset session"""
        if session_id in self.active_sessions:
            del self.active_sessions[session_id]