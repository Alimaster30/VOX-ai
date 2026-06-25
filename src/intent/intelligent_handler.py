"""
Intelligent Adaptive Query Handler
Maps complex queries consistently and adapts to user intent
"""
import re
from typing import List, Dict, Tuple, Optional

class IntelligentQueryHandler:
    """Handles complex queries with intelligent mapping and adaptation"""
    
    def __init__(self):
        self.intent_patterns = {
            "greeting": {
                "keywords": ["سلام", "ہیلو", "hello", "hi", "assalam", "aoa", "السلام"],
                "phrases": ["السلام علیکم", "assalam o alaikum", "good morning", "good evening", "hello kya", "madad chahiye"]
            },
            "goodbye": {
                "keywords": ["حافظ", "الوداع", "bye", "goodbye", "allah hafiz", "khuda hafiz"],
                "phrases": ["اللہ حافظ", "خدا حافظ", "allah hafiz", "khuda hafiz", "see you", "take care"]
            },
            "admission_process": {
                "keywords": ["داخلہ", "داخلے", "admission", "apply", "dakhla", "اپلائی", "داخل", "dakhlay", "lena", "ksy", "kaise", "kese"],
                "phrases": ["داخلہ لینا", "admission lena", "apply karna", "داخل ہونا", "dakhla kaise", "admission ksy lyn", "آن لائن اپلائی", "online apply"]
            },
            "fee_structure": {
                "keywords": ["فیس", "fee", "cost", "tuition", "خرچ", "قیمت", "رقم"],
                "phrases": ["فیس کتنی", "fee kitni", "cost kya hai", "خرچ کتنا", "سمسٹر فیس", "semester fee", "پہلے سمسٹر کی فیس"]
            },
            "scholarship": {
                "keywords": ["سکالرشپ", "scholarship", "مالی مدد", "چھوٹ", "غریب", "مالی مشکلات", "poor", "afford", "وظیفہ", "wazifa", "تنخواہ", "قسط", "installment"],
                "phrases": ["سکالرشپ ملتی", "scholarship milti", "fee maafi", "مالی مدد", "غریب ہیں", "financial help", "fee concession", "قسطوں میں", "تنخواہ کم", "مدد مل سکتی"]
            },
            "programs_list": {
                "keywords": ["پروگرام", "program", "course", "degree", "کورس", "ڈگری"],
                "phrases": ["کون سے پروگرام", "what programs", "konse courses", "کون کون سے", "کیا کیا ڈگریاں", "available degrees", "degrees available"]
            },
            "eligibility": {
                "keywords": ["اہلیت", "eligibility", "requirements", "شرائط", "ضروریات", "نمبر", "marks", "percent"],
                "phrases": ["کیا اہلیت", "eligibility kya", "requirements kya", "کتنے نمبر", "marks kitne", "kitne percent"]
            },
            "computer_science": {
                "keywords": ["کمپیوٹر", "computer", "bscs", "BSCS"],
                "phrases": ["کمپیوٹر سائنس", "computer science", "bs computer", "bscs ki", "BSCS ki", "bscs ka", "BSCS ka", "کمپیوٹر سائنس میں"]
            },
            "contact_info": {
                "keywords": ["رابطہ", "contact", "نمبر", "number", "phone", "فون", "موبائل", "mobile", "واٹس ایپ", "whatsapp", "ای میل", "email"],
                "phrases": ["رابطہ نمبر", "contact number", "phone number", "موبائل نمبر", "فون نمبر", "واٹس ایپ نمبر", "آفس کا نمبر", "نمبر چاہیے", "کس سے رابطہ"]
            },
            "rules_regulations": {
                "keywords": ["قوانین", "rules", "regulations", "ضوابط", "policy", "قانون", "نقل", "cheating", "misconduct", "سزا", "پابندی", "پابندیاں", "بندش", "ممنوع", "pabandi", "pabandiyaan", "pabandiyan", "restriction", "restrictions"],
                "phrases": ["کیا قوانین", "university rules", "regulations kya", "نقل کرنے", "cheating ka", "misconduct rules", "کیا پابندیاں", "کیا کیا پابندیاں", "پابندیاں ہیں", "kya pabandiyaan", "kya kya pabandiyaan", "kya pabandi hain", "what restrictions", "what are the restrictions"]
            },
            "attendance": {
                "keywords": ["حاضری", "attendance", "غیر حاضری", "absent", "موجودگی"],
                "phrases": ["حاضری کتنی", "attendance policy", "غیر حاضری", "kitni attendance"]
            },
            "exams": {
                "keywords": ["امتحان", "exam", "پیپر", "نتیجہ", "result", "paper"],
                "phrases": ["امتحان کب", "exam schedule", "result kab", "نتیجہ کب"]
            },
            "hostel": {
                "keywords": ["ہاسٹل", "hostel", "accommodation", "رہائش", "کمرہ", "رہنا", "ٹھہرنا"],
                "phrases": ["ہاسٹل کی سہولت", "hostel facility", "hostel hai", "لڑکیوں کا ہاسٹل", "لڑکوں کا ہاسٹل", "کہاں رہیں گے", "باہر سے آنے والے", "رہائش کی سہولت"]
            },
            "transport": {
                "keywords": ["ٹرانسپورٹ", "transport", "bus", "بس", "آمد و رفت", "سواری"],
                "phrases": ["ٹرانسپورٹ کی سہولت", "bus service", "transport facility", "آنے جانے", "گھر سے یونیورسٹی", "کیسے جائیں", "یونیورسٹی کیسے پہنچیں"]
            },
            "campus_info": {
                "keywords": ["کیمپس", "campus", "پتہ", "address", "location", "واقع"],
                "phrases": ["کیمپس کہاں", "campus location", "university address", "کیمپس میں کیا", "یونیورسٹی میں کیا کیا سہولات", "campus mein kya"]
            },
            "duration": {
                "keywords": ["سال", "year", "مدت", "duration", "عرصہ", "سمسٹر", "semester", "saal"],
                "phrases": ["کتنے سال", "how many years", "program duration", "kitne saal", "کب تک", "کتنے سمسٹر", "how many semesters", "kitne saal ka", "saal ka hai", "kitne semester", "BS kitne saal", "kitne saal mein"]
            },
        }

        self.single_responses = {
            "greeting": {
                "ur": "وعلیکم السلام! میں ایئر یونیورسٹی ملتان کیمپس کا ورچوئل اسسٹنٹ ہوں۔ آپ کا کیا سوال ہے؟",
                "en": "Wa Alaikum Assalam! I am the virtual assistant of Air University Multan Campus. How can I help you?"
            },
            "goodbye": {
                "ur": "آپ کا شکریہ! اللہ حافظ! اگر مزید معلومات چاہیے تو کبھی بھی رابطہ کریں۔",
                "en": "Thank you! Goodbye! Feel free to contact us anytime for more information."
            },
            "admission_process": {
                "ur": "داخلے کے لیے ویب سائٹ www.au.edu.pk پر آن لائن اپلائی کریں۔ ضروری دستاویزات میں تعلیمی سرٹیفکیٹ، CNIC اور تصاویر شامل ہیں۔ کیا آپ کسی خاص پروگرام کے بارے میں جاننا چاہتے ہیں؟",
                "en": "Apply online at www.au.edu.pk. Required documents include educational certificates, CNIC, and photos. Would you like to know about a specific program?"
            },
            "fee_structure": {
                "ur": "فیس پروگرام کے حساب سے مختلف ہے۔ BS Computer Science کی فیس 8500 روپے فی کریڈٹ آور ہے۔ کیا آپ کسی خاص پروگرام کی فیس جاننا چاہتے ہیں؟",
                "en": "Fees vary by program. BS Computer Science is PKR 8,500 per credit hour. Would you like the fee for a specific program?"
            },
            "scholarship": {
                "ur": "جی ہاں، میرٹ بیسڈ اور نیڈ بیسڈ سکالرشپس دستیاب ہیں۔ 85 فیصد سے زیادہ نمبروں پر 25 سے 75 فیصد فیس میں کمی ملتی ہے۔ HEC سکالرشپ بھی دستیاب ہے۔",
                "en": "Yes, merit-based and need-based scholarships are available. 85%+ marks get 25-75% fee reduction. HEC scholarships also available."
            },
            "programs_list": {
                "ur": "ہمارے پاس BS Computer Science، BS AI، BS Data Science، BS Cyber Security، BBA، MS اور PhD پروگرامز ہیں۔",
                "en": "We offer BS Computer Science, BS AI, BS Data Science, BS Cyber Security, BBA, MS and PhD programs."
            },
            "eligibility": {
                "ur": "انڈرگریجویٹ کے لیے انٹرمیڈیٹ میں کم از کم 50 فیصد نمبر درکار ہیں۔ ماسٹرز کے لیے 16 سال کی تعلیم اور GAT ضروری ہے۔",
                "en": "Undergraduate requires minimum 50% in Intermediate. Masters requires 16 years education and GAT."
            },
            "computer_science": {
                "ur": "BS Computer Science 4 سال کا پروگرام ہے۔ فیس 8500 روپے فی کریڈٹ آور ہے۔ اہلیت کے لیے انٹرمیڈیٹ میں 50 فیصد نمبر اور ICS یا Pre-Engineering ترجیحی ہے۔",
                "en": "BS Computer Science is a 4-year program. Fee is PKR 8,500 per credit hour. Eligibility requires 50% in Intermediate, ICS or Pre-Engineering preferred."
            },
            "contact_info": {
                "ur": "ایڈمیشن آفس سے رابطہ کریں۔ فون نمبر 92-61-9213456 پلس، ای میل admissions@au.edu.pk، آفس کا وقت صبح 8 سے شام 5 بجے۔",
                "en": "Contact Admission Office: Phone +92-61-9213456, Email admissions@au.edu.pk, Hours 8 AM to 5 PM."
            },
            "rules_regulations": {
                "ur": "یونیورسٹی کے قوانین میں 75 فیصد حاضری، نظم و ضبط اور تعلیمی دیانتداری شامل ہے۔ نقل یا بدتمیزی پر سخت کارروائی ہو سکتی ہے۔ مکمل قوانین ویب سائٹ www.au.edu.pk پر دستیاب ہیں۔",
                "en": "University rules include 75% attendance, discipline and academic integrity. Cheating or misconduct can result in strict action. Full rules at www.au.edu.pk."
            },
            "attendance": {
                "ur": "حاضری کم از کم 75 فیصد ہونی چاہیے۔ اس سے کم حاضری پر امتحان میں بیٹھنے کی اجازت نہیں ملتی۔",
                "en": "Minimum 75% attendance is required. Below this, students are not allowed to sit in exams."
            },
            "exams": {
                "ur": "امتحانات کا شیڈول یونیورسٹی کی ویب سائٹ پر دستیاب ہوتا ہے۔ مزید معلومات کے لیے ایگزام آفس سے رابطہ کریں۔",
                "en": "Exam schedules are available on the university website. Contact the exam office for more details."
            },
            "hostel": {
                "ur": "جی ہاں، لڑکوں اور لڑکیوں کے لیے الگ ہاسٹل کی سہولت موجود ہے۔ تفصیلات کے لیے ایڈمیشن آفس سے رابطہ کریں۔",
                "en": "Yes, separate hostels for boys and girls are available. Contact admission office for details."
            },
            "transport": {
                "ur": "یونیورسٹی ٹرانسپورٹ سروس دستیاب ہے۔ شہر کے مختلف علاقوں سے بسیں چلتی ہیں۔",
                "en": "University transport service is available with buses from different areas of the city."
            },
            "campus_info": {
                "ur": "ایئر یونیورسٹی ملتان کیمپس ملتان، پنجاب میں واقع ہے۔ کیمپس میں جدید کمپیوٹر لیبز، لائبریری، کیفے ٹیریا، کھیل کی سہولات، وائی فائی، ہاسٹل اور ٹرانسپورٹ سروس موجود ہے۔",
                "en": "Air University Multan Campus is located in Multan, Punjab. Campus has modern computer labs, library, cafeteria, sports facilities, WiFi, hostel and transport service."
            },
            "duration": {
                "ur": "BS پروگرامز 4 سال کے ہیں۔ MS پروگرامز 2 سال کے ہیں۔ PhD پروگرام 3 سے 5 سال کا ہوتا ہے۔",
                "en": "BS programs are 4 years. MS programs are 2 years. PhD is 3 to 5 years."
            },
        }

    def analyze_query(self, query: str) -> Dict[str, float]:
        """Analyze query and return intent scores"""
        query_lower = query.lower()
        intent_scores = {}

        # High-signal disambiguating phrases
        duration_signals = ["kitne saal", "kitne semester", "کتنے سال", "کتنے سمسٹر",
                            "how many years", "how many semesters",
                            "kitna waqt lage", "kitna waqt lag"]
        if any(sig in query_lower for sig in duration_signals):
            return {"duration": 0.85}

        # Greeting fast-path — any greeting keyword/phrase = immediate high confidence
        greeting_signals = ["سلام", "السلام", "assalam", "aoa", "hello", "hi", "ہیلو",
                            "السلام علیکم", "assalam o alaikum", "good morning", "good evening"]
        if any(sig in query_lower for sig in greeting_signals):
            return {"greeting": 0.95}

        # Goodbye fast-path
        goodbye_signals = ["اللہ حافظ", "خدا حافظ", "allah hafiz", "khuda hafiz", "bye", "goodbye", "الوداع"]
        if any(sig in query_lower for sig in goodbye_signals):
            return {"goodbye": 0.95}

        for intent, patterns in self.intent_patterns.items():
            score = 0.0

            keyword_matches = sum(1 for kw in patterns["keywords"] if kw in query_lower)
            if keyword_matches > 0:
                score += min(keyword_matches / len(patterns["keywords"]), 1.0) * 0.8

            phrase_matches = sum(1 for ph in patterns["phrases"] if ph in query_lower)
            if phrase_matches > 0:
                score += min(phrase_matches / len(patterns["phrases"]), 1.0) * 1.2

            if score > 0:
                intent_scores[intent] = score

        return intent_scores

    def generate_adaptive_response(self, query: str, language: str = "ur") -> Tuple[str, str, float]:
        """Generate response based on query analysis"""
        intent_scores = self.analyze_query(query)

        if not intent_scores:
            fallback = {
                "ur": "معذرت، میں آپ کا سوال سمجھ نہیں سکا۔ براہ کرم واضح کریں کہ آپ داخلے، فیس، پروگرامز یا سکالرشپ کے بارے میں جاننا چاہتے ہیں؟",
                "en": "Sorry, I didn't understand. Please clarify if you want to know about admissions, fees, programs, or scholarships?"
            }
            return fallback[language], "unknown", 0.0

        sorted_intents = sorted(intent_scores.items(), key=lambda x: x[1], reverse=True)
        top_intent, top_score = sorted_intents[0]

        # If top intent is clearly dominant, return it alone
        if top_score >= 0.5:
            if top_intent in self.single_responses:
                return self.single_responses[top_intent][language], top_intent, top_score

        # Fallback to top intent even if below threshold
        if top_intent in self.single_responses:
            return self.single_responses[top_intent][language], top_intent, top_score

        fallback = {
            "ur": "آپ کے سوال کے لیے مزید معلومات چاہیے۔ ایڈمیشن آفس سے رابطہ کریں: +92-61-9213456",
            "en": "Need more information for your query. Contact admission office: +92-61-9213456"
        }
        return fallback[language], "general", 0.3
