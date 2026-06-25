"""
Multi-Intent Query Handler for Complex Queries
"""
import re
from typing import List, Dict, Tuple

class MultiIntentHandler:
    """Handles complex queries with multiple intents"""
    
    def __init__(self):
        self.intent_combinations = {
            ("admission", "fee", "scholarship"): {
                "ur": """🎓 کمپیوٹر سائنس میں داخلے کی مکمل معلومات:

📋 داخلے کا طریقہ:
• آن لائن اپلیکیشن جمع کریں
• انٹرمیڈیٹ میں 50%+ نمبر (ترجیحاً ICS/Pre-Eng)
• داخلہ ٹیسٹ (اختیاری)

💰 فیس کی تفصیلات:
• 8,500 روپے فی کریڈٹ آور
• پہلے سمسٹر کی تقریبی فیس: 1,20,000 روپے
• سالانہ تقریبی فیس: 2,40,000 روپے

🏆 سکالرشپ کی سہولات:
• میرٹ بیسڈ: 25-75% فیس میں کمی (85%+ نمبر)
• نیڈ بیسڈ: 20-40% فیس میں کمی
• HEC اور حکومتی سکالرشپ دستیاب
• قسط کی سہولت بھی موجود ہے

تفصیلات کے لیے ایڈمیشن آفس سے رابطہ کریں: +92-61-9213456""",
                "en": """🎓 Complete Computer Science Admission Information:

📋 Admission Process:
• Submit online application
• 50%+ marks in Intermediate (preferably ICS/Pre-Eng)
• Entry test (optional)

💰 Fee Details:
• PKR 8,500 per credit hour
• First semester approximate fee: PKR 1,20,000
• Annual approximate fee: PKR 2,40,000

🏆 Scholarship Facilities:
• Merit-based: 25-75% fee reduction (85%+ marks)
• Need-based: 20-40% fee reduction
• HEC and government scholarships available
• Installment facility also available

Contact admission office for details: +92-61-9213456"""
            },
            ("programs", "fee"): {
                "ur": """📚 پروگرامز اور فیس کی تفصیلات:

🎓 دستیاب پروگرامز:
• BS Computer Science - 8,500 روپے فی کریڈٹ
• BS Artificial Intelligence - 9,000 روپے فی کریڈٹ
• BS Data Science - 9,000 روپے فی کریڈٹ
• BS Cyber Security - 8,500 روپے فی کریڈٹ
• BBA - 7,500 روپے فی کریڈٹ
• MS Programs - 12,000 روپے فی کریڈٹ

کیا آپ کسی خاص پروگرام کی تفصیلات چاہتے ہیں؟""",
                "en": """📚 Programs and Fee Details:

🎓 Available Programs:
• BS Computer Science - PKR 8,500 per credit
• BS Artificial Intelligence - PKR 9,000 per credit
• BS Data Science - PKR 9,000 per credit
• BS Cyber Security - PKR 8,500 per credit
• BBA - PKR 7,500 per credit
• MS Programs - PKR 12,000 per credit

Would you like details about a specific program?"""
            }
        }
    
    def detect_multiple_intents(self, query: str) -> List[str]:
        """Detect multiple intents in a query"""
        query_lower = query.lower()
        detected_intents = []
        
        # Intent keywords mapping
        intent_keywords = {
            "admission": ["داخلہ", "admission", "apply", "dakhla", "داخلے"],
            "fee": ["فیس", "fee", "cost", "tuition", "خرچ"],
            "scholarship": ["سکالرشپ", "scholarship", "fee concession", "مالی مدد"],
            "programs": ["پروگرام", "program", "course", "degree", "کورس"],
            "eligibility": ["اہلیت", "eligibility", "requirements", "شرائط"]
        }
        
        for intent, keywords in intent_keywords.items():
            if any(keyword in query_lower for keyword in keywords):
                detected_intents.append(intent)
        
        return detected_intents
    
    def handle_multi_intent_query(self, query: str, language: str) -> Tuple[str, str, float]:
        """Handle multi-intent queries"""
        intents = self.detect_multiple_intents(query)
        
        if len(intents) >= 2:
            # Sort intents for consistent matching
            intents_tuple = tuple(sorted(intents))
            
            # Check for exact combinations
            for combo, responses in self.intent_combinations.items():
                if all(intent in intents for intent in combo):
                    response = responses.get(language, responses.get("ur", ""))
                    combined_intent = "+".join(combo)
                    return response, combined_intent, 0.9
            
            # Fallback for other combinations
            if "admission" in intents and "fee" in intents:
                if language == "ur":
                    return "داخلے کے لیے آن لائن اپلائی کریں۔ فیس پروگرام کے حساب سے مختلف ہے۔", "admission+fee", 0.8
                else:
                    return "Apply online for admission. Fees vary by program.", "admission+fee", 0.8
        
        return None, None, 0.0