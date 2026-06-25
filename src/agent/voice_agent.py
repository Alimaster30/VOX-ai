"""
Core Voice Agent Module
Integrates STT, Intent Classification, Response Generation, and TTS
"""
import logging
from pathlib import Path
from typing import Dict, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class AgentAction(Enum):
    """Possible agent actions"""
    RESPOND = "respond"
    CLARIFY = "clarify"
    TRANSFER = "transfer"


@dataclass
class AgentResponse:
    """Response from the voice agent"""
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


class VoiceAgent:
    """Main Voice Agent that handles the complete pipeline"""
    
    def __init__(
        self,
        intents_path: str,
        programs_path: Optional[str] = None,
        stt_model_size: str = "base",
        stt_device: str = "cpu",
        confidence_high: float = 0.3,  # Lower for better responses
        confidence_medium: float = 0.1,
    ):
        """
        Initialize Voice Agent
        
        Args:
            intents_path: Path to intents JSON file
            programs_path: Path to programs Excel file
            stt_model_size: Whisper model size
            stt_device: Device for STT (cuda/cpu)
            confidence_high: High confidence threshold
            confidence_medium: Medium confidence threshold
        """
        self.intents_path = intents_path
        self.programs_path = programs_path
        self.confidence_high = confidence_high
        self.confidence_medium = confidence_medium
        
        # Initialize components (lazy loading)
        self._stt = None
        self._intent_classifier = None
        self._tts = None
        self._programs_db = None
        
        # Session management
        self.active_sessions = {}
        
        # Performance metrics
        self.metrics = {
            'total_queries': 0,
            'successful_responses': 0,
            'average_confidence': 0.0,
            'average_processing_time': 0.0
        }
        
        self.stt_config = {
            "model_size": stt_model_size,
            "device": stt_device,
        }
        
        logger.info("Voice Agent initialized")
    
    @property
    def stt(self):
        """Lazy load STT module"""
        if self._stt is None:
            from src.stt.simple_stt import SimpleSTT
            self._stt = SimpleSTT(
                model_size=self.stt_config["model_size"],
                device=self.stt_config["device"],
            )
        return self._stt
    
    @property
    def intent_classifier(self):
        """Lazy load Intent Classifier with pre-downloaded models"""
        if self._intent_classifier is None:
            from src.intent.classifier import IntentClassifier
            self._intent_classifier = IntentClassifier(
                model_name="paraphrase-multilingual-MiniLM-L12-v2",
                intents_path=self.intents_path
            )
            self._intent_classifier.load_model()
            self._intent_classifier.build_index()
            logger.info("Using advanced semantic classifier with pre-downloaded models")
        return self._intent_classifier
    
    @property
    def tts(self):
        """Lazy load TTS module"""
        if self._tts is None:
            from src.tts.edge_tts import EdgeTTS
            self._tts = EdgeTTS()
        return self._tts
    
    @property
    def programs_db(self):
        """Lazy load Programs Database"""
        if self._programs_db is None and self.programs_path:
            from src.database.programs_db import ProgramsDatabase
            self._programs_db = ProgramsDatabase(self.programs_path)
        return self._programs_db
    
    def transcribe_audio(self, audio_path: str) -> Tuple[str, str, float]:
        """
        Transcribe audio to text
        
        Returns:
            Tuple of (text, language, confidence)
        """
        return self.stt.transcribe(audio_path)
    
    def detect_language(self, text: str) -> str:
        """
        Simple language detection based on character analysis
        
        Args:
            text: Input text
            
        Returns:
            Language code ('ur' for Urdu, 'en' for English)
        """
        # Count Urdu/Arabic characters
        urdu_chars = sum(1 for c in text if '\u0600' <= c <= '\u06FF')
        total_chars = len([c for c in text if c.isalpha()])
        
        if total_chars == 0:
            return "ur"  # Default to Urdu
        
        # If more than 30% are Urdu characters, classify as Urdu
        urdu_ratio = urdu_chars / total_chars
        return "ur" if urdu_ratio > 0.3 else "en"
    
    def process_text(self, text: str, language: str = None, session_id: str = "default") -> AgentResponse:
        """
        Process text query and generate response
        
        Args:
            text: User's query text
            language: Response language (auto-detected if None)
            session_id: Session identifier
            
        Returns:
            AgentResponse with text, audio, and metadata
        """
        import time
        start_time = time.time()
        
        # Auto-detect language if not provided
        if language is None:
            language = self.detect_language(text)
        
        # Classify intent with intelligent handler
        from src.intent.intelligent_handler import IntelligentQueryHandler
        intelligent_handler = IntelligentQueryHandler()
        
        # Try intelligent handler first
        intelligent_response, intelligent_intent, intelligent_confidence = intelligent_handler.generate_adaptive_response(text, language)
        
        # Use intelligent response if confidence is good
        if intelligent_confidence > 0.4:
            response_text = intelligent_response
            intent = intelligent_intent
            confidence = intelligent_confidence
        else:
            # Fallback to original classifier
            response_text, intent, confidence = self.intent_classifier.get_response(
                text, language=language
            )
        
        # Remove old multi-intent handler (replaced by intelligent handler)
        # Check for multi-intent queries first
        # from src.intent.multi_intent_handler import MultiIntentHandler
        # multi_handler = MultiIntentHandler()
        # multi_response, multi_intent, multi_confidence = multi_handler.handle_multi_intent_query(text, language)
        
        # if multi_response and multi_confidence > confidence:
        #     response_text = multi_response
        #     intent = multi_intent
        #     confidence = multi_confidence
        
        # Determine action based on confidence
        if confidence >= self.confidence_high:
            action = AgentAction.RESPOND
        elif confidence >= self.confidence_medium:
            action = AgentAction.RESPOND
        else:
            action = AgentAction.TRANSFER
            response_text = "میں آپ کو متعلقہ شعبے سے منسلک کر رہا ہوں۔" if language == "ur" else "Transferring you to the relevant department."
        
        # Check for program-specific queries
        if intent in ["fee_structure", "eligibility", "programs_list"] and self.programs_db:
            enhanced_response = self._enhance_with_program_data(text, intent, language)
            if enhanced_response:
                response_text = enhanced_response
        
        # Ensure response_text is a string
        if not isinstance(response_text, str) or not response_text.strip():
            response_text = "معذرت، میں آپ کا سوال سمجھ نہیں سکا۔" if language == "ur" else "Sorry, I didn't understand your question."
        
        # Generate audio
        voice = "ur_female" if language == "ur" else "en_female"
        try:
            audio_path = self.tts.synthesize(response_text, voice=voice)
        except Exception as e:
            logger.error(f"TTS synthesis failed: {e}")
            audio_path = None
        
        processing_time = time.time() - start_time
        
        return AgentResponse(
            text=response_text,
            audio_path=audio_path,
            intent=intent,
            confidence=confidence,
            action=action,
            language=language,
            processing_time=processing_time,
            suggestions=[],
            metadata={}
        )
    
    def _enhance_with_program_data(self, query: str, intent: str, language: str) -> Optional[str]:
        """Enhanced program data integration with better matching"""
        if not self.programs_db:
            return None
            
        try:
            # Enhanced keyword matching for program names
            programs = self.programs_db.get_all_programs()
            
            # Direct program name matching
            for program in programs:
                prog_name = program.get('Program_Name', '').lower()
                if prog_name and (prog_name in query.lower() or any(word in prog_name for word in query.lower().split())):
                    if language == "ur":
                        return self.programs_db.format_program_info_urdu(program)
                    return self.programs_db.format_program_info_english(program)
            
            # Field-based matching
            query_lower = query.lower()
            if any(word in query_lower for word in ['computer', 'cs', 'کمپیوٹر']):
                cs_programs = [p for p in programs if 'computer' in p.get('Program_Name', '').lower()]
                if cs_programs:
                    program = cs_programs[0]
                    if language == "ur":
                        return self.programs_db.format_program_info_urdu(program)
                    return self.programs_db.format_program_info_english(program)
            
            return None
        except Exception as e:
            logger.error(f"Error enhancing with program data: {e}")
            return None
    
    def get_metrics(self) -> Dict:
        """Get current performance metrics"""
        return self.metrics.copy()
    
    def reset_session(self, session_id: str = "default"):
        """Reset conversation session"""
        if session_id in self.active_sessions:
            del self.active_sessions[session_id]
            logger.info(f"Session {session_id} reset")
    
    def process_audio(self, audio_path: str, session_id: str = "default") -> AgentResponse:
        """
        Enhanced audio processing pipeline with session management
        
        Args:
            audio_path: Path to input audio file
            session_id: Session identifier for context management
            
        Returns:
            Enhanced AgentResponse
        """
        try:
            # Transcribe with enhanced error handling
            text, detected_lang, stt_confidence = self.transcribe_audio(audio_path)
            logger.info(f"Transcribed: '{text}' | Lang: {detected_lang} | STT Confidence: {stt_confidence:.2f}")
            
            # Map detected language
            language = "ur" if detected_lang == "ur" else "en"
            
            # Process with session context
            response = self.process_text(text, language=language, session_id=session_id)
            
            # Add STT metadata
            response.metadata.update({
                'stt_confidence': stt_confidence,
                'transcribed_text': text,
                'detected_language': detected_lang
            })
            
            return response
            
        except Exception as e:
            logger.error(f"Audio processing failed: {e}")
            # Return error response
            error_text = "آڈیو پروسیسنگ میں خرابی ہوئی۔ براہ کرم دوبارہ کوشش کریں۔"
            return AgentResponse(
                text=error_text,
                audio_path=None,
                intent="error",
                confidence=0.0,
                action=AgentAction.TRANSFER,
                language="ur",
                processing_time=0.0,
                suggestions=["دوبارہ کوشش کریں", "ٹیکسٹ میں سوال لکھیں"],
                metadata={'error': str(e)}
            )

