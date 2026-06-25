"""
Advanced Intent Classification Module using Semantic Search
Supports complex Urdu and English queries with context understanding
"""
import json
import logging
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import re
import pickle
import os

try:
    from sentence_transformers import SentenceTransformer
    import faiss
    ADVANCED_MODE = True
except ImportError:
    ADVANCED_MODE = False
    logging.warning("Advanced dependencies not available. Install: pip install sentence-transformers faiss-cpu")

logger = logging.getLogger(__name__)


class IntentClassifier:
    """Advanced intent classifier using semantic search with fallback to keyword matching"""
    
    def __init__(
        self,
        model_name: str = "paraphrase-multilingual-MiniLM-L12-v2",
        intents_path: Optional[str] = None,
        use_cache: bool = True,
    ):
        """
        Initialize Intent Classifier
        
        Args:
            model_name: Sentence transformer model for semantic search
            intents_path: Path to intents JSON file
            use_cache: Whether to cache embeddings
        """
        self.model_name = model_name
        self.use_cache = use_cache
        self.intents_data = None
        self.intent_keywords = {}  # Fallback keyword mapping
        
        # Advanced components
        self.model = None
        self.index = None
        self.pattern_embeddings = None
        self.pattern_to_intent = []
        self.cache_dir = Path(os.environ.get("VOX_CACHE_DIR", "runtime_cache"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("HF_HOME", str(self.cache_dir / "huggingface"))
        os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", str(self.cache_dir / "sentence_transformers"))
        
        if intents_path:
            self.load_intents(intents_path)
    
    def load_model(self):
        """Load sentence transformer model for semantic search"""
        if not ADVANCED_MODE:
            logger.info("Using fallback keyword-based classification")
            return
            
        try:
            logger.info(f"Loading model: {self.model_name}")
            self.model = SentenceTransformer(self.model_name)
            logger.info("Semantic search model loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load model: {e}")
            logger.info("Falling back to keyword-based classification")
    
    def load_intents(self, intents_path: str):
        """Load intents from JSON file with path validation."""
        # Security: resolve path and ensure it doesn't escape project directory
        resolved = os.path.realpath(os.path.abspath(intents_path))
        project_root = os.path.realpath(os.path.abspath(
            os.environ.get("VOX_ROOT", os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        ))
        if not resolved.startswith(project_root):
            raise ValueError(f"Path traversal blocked: {intents_path}")
        try:
            with open(resolved, 'r', encoding='utf-8') as f:
                self.intents_data = json.load(f)
            logger.info(f"Loaded {len(self.intents_data['intents'])} intents")
            self._build_keyword_map()
        except Exception as e:
            logger.error(f"Failed to load intents: {e}")
            raise
    
    def _build_keyword_map(self):
        """Build keyword mapping from patterns"""
        self.intent_keywords = {}
        
        for intent in self.intents_data['intents']:
            keywords = set()
            for pattern in intent['patterns']:
                # Extract keywords from patterns (both English and Urdu)
                words = re.findall(r'[\w\u0600-\u06FF]+', pattern.lower(), re.UNICODE)
                keywords.update(words)
                # Also add the full pattern as a keyword for better matching
                keywords.add(pattern.lower().strip())
            
            self.intent_keywords[intent['tag']] = {
                'keywords': keywords,
                'patterns': [p.lower() for p in intent['patterns']],
                'responses_urdu': intent.get('responses_urdu', []),
                'responses_english': intent.get('responses_english', []),
            }
    
    def build_index(self):
        """Build FAISS index for semantic search"""
        if self.intents_data is None:
            raise ValueError("Intents data not loaded. Call load_intents() first.")
            
        if not ADVANCED_MODE or self.model is None:
            logger.info("Keyword-based classifier ready")
            return
            
        cache_file = self.cache_dir / f"embeddings_{hash(str(self.intents_data))}.pkl"
        
        if self.use_cache and cache_file.exists():
            logger.info("Loading cached embeddings")
            # Security: verify cache file is owned by current user before loading
            try:
                stat = cache_file.stat()
                current_uid = os.getuid()
                if stat.st_uid != current_uid:
                    logger.warning("Cache file owner mismatch — rebuilding index")
                    cache_file.unlink()
                else:
                    with open(cache_file, 'rb') as f:
                        cache_data = pickle.load(f)
                        self.pattern_embeddings = cache_data['embeddings']
                        self.pattern_to_intent = cache_data['pattern_to_intent']
            except AttributeError:
                # Windows: os.getuid() not available, skip owner check
                logger.info("Skipping cache owner check (Windows)")
                with open(cache_file, 'rb') as f:
                    cache_data = pickle.load(f)
                    self.pattern_embeddings = cache_data['embeddings']
                    self.pattern_to_intent = cache_data['pattern_to_intent']
        else:
            logger.info("Building semantic index...")
            patterns = []
            self.pattern_to_intent = []
            
            for intent in self.intents_data['intents']:
                for pattern in intent['patterns']:
                    patterns.append(pattern)
                    self.pattern_to_intent.append({
                        'tag': intent['tag'],
                        'responses_urdu': intent.get('responses_urdu', []),
                        'responses_english': intent.get('responses_english', [])
                    })
            
            # Generate embeddings
            self.pattern_embeddings = self.model.encode(patterns, convert_to_numpy=True)
            
            # Cache embeddings
            if self.use_cache:
                with open(cache_file, 'wb') as f:
                    pickle.dump({
                        'embeddings': self.pattern_embeddings,
                        'pattern_to_intent': self.pattern_to_intent
                    }, f)
        
        # Build FAISS index
        dimension = self.pattern_embeddings.shape[1]
        self.index = faiss.IndexFlatIP(dimension)  # Inner product for cosine similarity
        
        # Normalize embeddings for cosine similarity
        faiss.normalize_L2(self.pattern_embeddings)
        self.index.add(self.pattern_embeddings)
        
        logger.info(f"Semantic index built with {len(self.pattern_to_intent)} patterns")
    
    def classify(
        self,
        query: str,
        top_k: int = 3,
    ) -> List[Dict]:
        """
        Classify user query using semantic search with keyword fallback
        
        Args:
            query: User's query text
            top_k: Number of top matches to return
            
        Returns:
            List of dictionaries with intent info and confidence scores
        """
        if self.intents_data is None:
            raise ValueError("Intents data not loaded. Please initialize with intents_path.")
        
        # Try semantic search first
        if ADVANCED_MODE and self.model is not None and self.index is not None:
            try:
                return self._semantic_classify(query, top_k)
            except Exception as e:
                logger.error(f"Semantic classification failed: {e}")
                logger.info("Falling back to keyword matching")
        
        # Fallback to enhanced keyword matching
        return self._keyword_classify(query, top_k)
    
    def _semantic_classify(self, query: str, top_k: int) -> List[Dict]:
        """Semantic classification using sentence transformers"""
        # Encode query
        query_embedding = self.model.encode([query], convert_to_numpy=True)
        faiss.normalize_L2(query_embedding)
        
        # Search similar patterns
        similarities, indices = self.index.search(query_embedding, min(top_k * 2, len(self.pattern_to_intent)))
        
        # Group by intent and calculate max confidence
        intent_scores = {}
        for sim, idx in zip(similarities[0], indices[0]):
            if idx >= len(self.pattern_to_intent):
                continue
                
            intent_info = self.pattern_to_intent[idx]
            intent_tag = intent_info['tag']
            
            # Convert cosine similarity to confidence (0-1)
            confidence = max(0, min(1, (sim + 1) / 2))  # Normalize from [-1,1] to [0,1]
            
            if intent_tag not in intent_scores or confidence > intent_scores[intent_tag]['confidence']:
                intent_scores[intent_tag] = {
                    'tag': intent_tag,
                    'confidence': confidence,
                    'responses_urdu': intent_info['responses_urdu'],
                    'responses_english': intent_info['responses_english']
                }
        
        # Sort by confidence and return top_k
        results = list(intent_scores.values())
        results.sort(key=lambda x: x['confidence'], reverse=True)
        return results[:top_k]
    
    def _keyword_classify(self, query: str, top_k: int) -> List[Dict]:
        """Enhanced keyword-based classification"""
        try:
            query_lower = query.lower().strip()
            query_words = set(re.findall(r'[\w\u0600-\u06FF]+', query_lower, re.UNICODE))
            
            results = []
            for intent_tag, intent_data in self.intent_keywords.items():
                max_confidence = 0.0
                
                # Check for exact pattern match first
                for pattern in intent_data['patterns']:
                    if pattern == query_lower or (len(pattern.split()) > 1 and pattern in query_lower):
                        max_confidence = max(max_confidence, 0.9)
                        break
                
                # Enhanced keyword overlap scoring
                if max_confidence < 0.9:
                    overlap = len(query_words.intersection(intent_data['keywords']))
                    if overlap > 0:
                        # Multi-factor confidence calculation
                        overlap_ratio = overlap / len(query_words) if len(query_words) > 0 else 0
                        keyword_density = overlap / len(intent_data['keywords']) if len(intent_data['keywords']) > 0 else 0
                        length_factor = min(0.3, len(query.split()) * 0.05)
                        
                        confidence = min(0.85, (overlap_ratio * 0.6 + keyword_density * 0.3 + length_factor))
                        max_confidence = max(max_confidence, confidence)
                
                if max_confidence > 0:
                    results.append({
                        'tag': intent_tag,
                        'confidence': max_confidence,
                        'responses_urdu': intent_data['responses_urdu'],
                        'responses_english': intent_data['responses_english'],
                    })
            
            results.sort(key=lambda x: x['confidence'], reverse=True)
            return results[:top_k]
            
        except Exception as e:
            logger.error(f"Keyword classification failed: {e}")
            return []
    
    def get_response(
        self,
        query: str,
        language: str = "ur",
        context: Optional[Dict] = None,
    ) -> Tuple[str, str, float]:
        """
        Get intelligent response for a query with context awareness
        
        Args:
            query: User's query
            language: Response language (ur or en)
            context: Previous conversation context
            
        Returns:
            Tuple of (response_text, intent_tag, confidence)
        """
        results = self.classify(query, top_k=3)
        
        if not results:
            # Intelligent fallback responses
            fallback_responses = {
                "ur": [
                    "معذرت، میں آپ کا سوال مکمل طور پر سمجھ نہیں سکا۔ کیا آپ اسے دوسرے انداز میں پوچھ سکتے ہیں؟",
                    "براہ کرم اپنا سوال واضح کریں تاکہ میں بہتر مدد کر سکوں۔",
                    "میں آپ کو داخلے، فیس، پروگرامز، اور یونیورسٹی کی معلومات فراہم کر سکتا ہوں۔"
                ],
                "en": [
                    "I didn't fully understand your question. Could you rephrase it?",
                    "Please clarify your question so I can help you better.",
                    "I can help you with admissions, fees, programs, and university information."
                ]
            }
            response = fallback_responses[language][0] if language in fallback_responses else fallback_responses["ur"][0]
            return response, "unknown", 0.0
        
        top_result = results[0]
        confidence = top_result['confidence']
        intent_tag = top_result['tag']
        
        # Multi-intent handling for complex queries
        if len(results) > 1 and results[1]['confidence'] > 0.6:
            response = self._handle_multi_intent_query(results, language, query)
            if response:
                return response, f"{intent_tag}+{results[1]['tag']}", confidence
        
        # Select response based on language with smart fallbacks
        response = self._get_localized_response(top_result, language)
        
        # Context-aware response enhancement
        if context and 'previous_intent' in context:
            response = self._enhance_with_context(response, context, intent_tag, language)
        
        return response, intent_tag, confidence
    
    def _handle_multi_intent_query(self, results: List[Dict], language: str, query: str) -> Optional[str]:
        """Handle queries that match multiple intents"""
        primary = results[0]
        secondary = results[1]
        
        # Common multi-intent combinations
        multi_intent_responses = {
            ("admission_info", "fee_structure"): {
                "ur": "داخلے کے لیے آپ کو آن لائن اپلائی کرنا ہوگا۔ فیس پروگرام کے حساب سے مختلف ہے۔ کیا آپ کسی خاص پروگرام کے بارے میں جاننا چاہتے ہیں؟",
                "en": "For admission, you need to apply online. Fees vary by program. Would you like to know about a specific program?"
            },
            ("programs_list", "fee_structure"): {
                "ur": "ہمارے پاس مختلف پروگرامز ہیں جن کی فیس الگ الگ ہے۔ کیا آپ کسی خاص شعبے میں دلچسپی رکھتے ہیں؟",
                "en": "We have various programs with different fee structures. Are you interested in a specific field?"
            },
            ("eligibility", "admission_info"): {
                "ur": "داخلے کے لیے کم از کم 50% نمبر درکار ہیں۔ آپ آن لائن اپلائی کر سکتے ہیں۔",
                "en": "Minimum 50% marks are required for admission. You can apply online."
            }
        }
        
        key = (primary['tag'], secondary['tag'])
        if key in multi_intent_responses:
            return multi_intent_responses[key].get(language)
        
        return None
    
    def _get_localized_response(self, result: Dict, language: str) -> str:
        """Get response in requested language with smart fallbacks"""
        if language == "ur" and result['responses_urdu']:
            return result['responses_urdu'][0]
        elif language == "en" and result['responses_english']:
            return result['responses_english'][0]
        elif result['responses_urdu']:  # Fallback to Urdu
            return result['responses_urdu'][0]
        elif result['responses_english']:  # Fallback to English
            return result['responses_english'][0]
        else:
            return "معذرت، جواب دستیاب نہیں ہے۔" if language == "ur" else "Sorry, response not available."
    
    def _enhance_with_context(self, response: str, context: Dict, current_intent: str, language: str) -> str:
        """Enhance response based on conversation context"""
        previous_intent = context.get('previous_intent')
        
        # Context-aware enhancements
        if previous_intent == "greeting" and current_intent != "greeting":
            if language == "ur":
                response = f"جی ہاں، {response}"
            else:
                response = f"Sure, {response}"
        
        return response
