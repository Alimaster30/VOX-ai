"""
Instant Intent Classifier - No ML models, pure keyword matching
"""
import json
import logging
import re
from typing import Tuple

logger = logging.getLogger(__name__)

class InstantClassifier:
    """Instant keyword-based classifier"""
    
    def __init__(self, intents_path: str):
        self.responses = {
            "greeting": {
                "ur": "وعلیکم السلام! میں ایئر یونیورسٹی ملتان کا ورچوئل اسسٹنٹ ہوں۔ آپ کا کیا سوال ہے؟",
                "en": "Wa Alaikum Assalam! I am the virtual assistant of Air University Multan. How can I help you?"
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
        logger.info("Instant classifier ready")
    
    def load_model(self):
        """No model to load"""
        pass
    
    def build_index(self):
        """No index to build"""
        pass
    
    def get_response(self, query: str, language: str = "ur") -> Tuple[str, str, float]:
        """Get instant response based on keywords"""
        query_lower = query.lower()
        
        # Greeting detection
        greeting_words = ['سلام', 'اسلام', 'السلام', 'hello', 'hi', 'assalam']
        if any(word in query_lower for word in greeting_words):
            return self.responses["greeting"][language], "greeting", 0.9
        
        # Admission detection
        admission_words = ['داخلہ', 'admission', 'apply', 'dakhla']
        if any(word in query_lower for word in admission_words):
            return self.responses["admission"][language], "admission", 0.8
        
        # Fee detection
        fee_words = ['فیس', 'fee', 'cost', 'tuition']
        if any(word in query_lower for word in fee_words):
            return self.responses["fee"][language], "fee", 0.8
        
        # Programs detection
        program_words = ['پروگرام', 'program', 'course', 'degree']
        if any(word in query_lower for word in program_words):
            return self.responses["programs"][language], "programs", 0.8
        
        # Default response
        return self.responses["default"][language], "unknown", 0.3