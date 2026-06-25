#!/usr/bin/env python3
"""
agi_bridge.py
Asterisk AGI Bridge — connects incoming VoIP calls to the VOX pipeline.
Place this file at: /var/lib/asterisk/agi-bin/agi_bridge.py

Flow:
  Caller speaks → Asterisk records audio → AGI sends to Whisper
  → Intent/RAG/LLM pipeline → TTS audio → Asterisk plays back to caller
"""

import sys
import os
import re
import subprocess
import logging

# Project root from environment variable.
PROJECT_ROOT = os.environ.get("VOX_ROOT", "/opt/vox")
sys.path.insert(0, PROJECT_ROOT)

import torch
import whisper
import argostranslate.translate
from gtts import gTTS

from langchain_ollama import OllamaEmbeddings, OllamaLLM
from langchain_chroma import Chroma
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser

from src.intent.intelligent_handler import IntelligentQueryHandler
from src.intent.classifier import IntentClassifier
from src.config import ensure_org_runtime_files, get_handoff_text, load_org_profile

ORG_PROFILE   = load_org_profile()
ensure_org_runtime_files(ORG_PROFILE)

# ── Config ────────────────────────────────────────────────────
CHROMA_DIR    = ORG_PROFILE["chroma_dir"]
INTENTS_PATH  = ORG_PROFILE["intents_path"]
AUDIO_TMP     = os.path.join(PROJECT_ROOT, "audio_tmp")   # private, not /tmp
LLM_MODEL     = os.environ.get("VOX_LLM_MODEL", ORG_PROFILE.get("llm_model", "qwen3.2:3b"))
EMBED_MODEL   = os.environ.get("VOX_EMBED_MODEL", ORG_PROFILE.get("embedding_model", "nomic-embed-text"))
THRESHOLD     = 0.5
MAX_HISTORY   = 6
MAX_TURNS     = 20
MAX_QUERY_LEN = 500   # max characters accepted per query

GREETING_UR   = ORG_PROFILE.get("greetings", {}).get("ur") or ORG_PROFILE.get("greetings", {}).get("en", f"Welcome to {ORG_PROFILE.get('organization_name', 'this organization')}.")
DISCLAIMER_UR = "براہ کرم نوٹ کریں: جوابات مقامی طور پر پروسیس ہوتے ہیں، اس لیے چند سیکنڈ کی تاخیر ہو سکتی ہے۔ آپ کے صبر کا شکریہ۔"

# Create private audio directory — owner only (no world-readable /tmp)
os.makedirs(AUDIO_TMP, exist_ok=True)
os.chmod(AUDIO_TMP, 0o700)

logging.basicConfig(
    filename="/var/log/vox.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)


# ── Security helpers ──────────────────────────────────────────
def safe_path(path: str, base: str) -> str:
    """Resolve path and ensure it stays within base — prevents path traversal."""
    resolved = os.path.realpath(os.path.abspath(path))
    base_resolved = os.path.realpath(os.path.abspath(base))
    if not resolved.startswith(base_resolved + os.sep) and resolved != base_resolved:
        raise ValueError(f"Path traversal blocked: {path}")
    return resolved


def sanitize_query(query: str) -> str:
    """Strip control characters and enforce max length."""
    query = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', query)
    return query[:MAX_QUERY_LEN].strip()


def safe_log(turn: int, lang: str, tag: str, confidence: float):
    """Log only metadata — never log raw caller query text for privacy."""
    logging.info(f"Turn {turn} | lang={lang} | intent={tag} | confidence={confidence:.2f}")


# ── AGI Communication ─────────────────────────────────────────
class AGI:
    """Minimal AGI interface to communicate with Asterisk."""

    def __init__(self):
        self.env = {}
        self._read_env()

    def _read_env(self):
        while True:
            line = sys.stdin.readline().strip()
            if not line:
                break
            if ":" in line:
                key, val = line.split(":", 1)
                self.env[key.strip()] = val.strip()

    def send(self, cmd):
        sys.stdout.write(cmd + "\n")
        sys.stdout.flush()
        return sys.stdin.readline().strip()

    def answer(self):   return self.send("ANSWER")
    def hangup(self):   return self.send("HANGUP")

    def stream_file(self, filename, escape_digits=""):
        return self.send(f'STREAM FILE "{filename}" "{escape_digits}"')

    def record_file(self, filename, fmt="wav", escape_digits="#", timeout=8000, silence=3):
        return self.send(
            f'RECORD FILE "{filename}" "{fmt}" "{escape_digits}" {timeout} s={silence}'
        )

    def verbose(self, msg, level=1):
        # Sanitize before sending to Asterisk to prevent log injection
        msg = re.sub(r'[\r\n"\\]', '', str(msg))[:200]
        self.send(f'VERBOSE "{msg}" {level}')


# ── Audio helpers ─────────────────────────────────────────────
def text_to_urdu_audio(text: str, out_path: str) -> str:
    """Convert Urdu text to WAV for Asterisk. Validates output path."""
    # Validate path stays within AUDIO_TMP
    safe_path(out_path, AUDIO_TMP)

    text = re.sub(r'[^\w\s\u0600-\u06FF،؟۔,.\'\"-!?()\-:]', '', text).strip()
    mp3_path = out_path + ".mp3"
    wav_path = out_path + ".wav"

    tts = gTTS(text=text, lang="ur")
    tts.save(mp3_path)

    subprocess.run(
        ["ffmpeg", "-y", "-i", mp3_path, "-ar", "8000", "-ac", "1", "-f", "wav", wav_path],
        capture_output=True, timeout=30
    )
    # Remove mp3 after conversion
    if os.path.exists(mp3_path):
        os.unlink(mp3_path)

    return wav_path


def translate(text, from_code, to_code):
    if from_code == to_code:
        return text
    return argostranslate.translate.translate(text, from_code, to_code)


# ── Load models ───────────────────────────────────────────────
def load_models():
    device = "cuda" if torch.cuda.is_available() else "cpu"
    whisper_model = whisper.load_model("base", device=device)
    handler       = IntelligentQueryHandler()
    classifier    = IntentClassifier(intents_path=INTENTS_PATH)
    classifier.load_model()
    classifier.build_index()
    db            = Chroma(persist_directory=CHROMA_DIR,
                           embedding_function=OllamaEmbeddings(model=EMBED_MODEL))
    retriever     = db.as_retriever(search_kwargs={"k": 4})
    llm           = OllamaLLM(model=LLM_MODEL, num_gpu=20)
    rag_prompt    = PromptTemplate.from_template(
        ORG_PROFILE.get(
            "rag_system_prompt",
            "You are VOX, a helpful assistant for {organization_name}. Use only the provided organization context."
        ).format(organization_name=ORG_PROFILE.get("organization_name", "this organization")) + "\n\n"
        "Conversation history:\n{history}\n\n"
        "Context from documents:\n{context}\n\n"
        "Question: {question}\n\nAnswer:"
    )
    return whisper_model, handler, classifier, retriever, llm, rag_prompt


def build_history_text(history):
    return "\n".join(f"User: {t['user']}\nAssistant: {t['assistant']}" for t in history)


# ── Load models once at process startup ──────────────────────
_whisper_model, _handler, _classifier, _retriever, _llm, _rag_prompt = load_models()


# ── Main call handler ─────────────────────────────────────────
def handle_call():
    agi = AGI()
    agi.answer()
    logging.info("Call answered")

    caller_id = re.sub(r'[^\d+]', '', agi.env.get("agi_callerid", "unknown"))[:20]
    logging.info(f"Caller: {caller_id}")

    whisper_model = _whisper_model
    handler       = _handler
    classifier    = _classifier
    retriever     = _retriever
    llm           = _llm
    rag_prompt    = _rag_prompt
    conversation_history = []

    # Play greeting + disclaimer
    for i, text in enumerate([GREETING_UR, DISCLAIMER_UR]):
        audio_path = safe_path(os.path.join(AUDIO_TMP, f"greeting_{i}"), AUDIO_TMP)
        wav = text_to_urdu_audio(text, audio_path)
        sounds_path = f"/var/lib/asterisk/sounds/vox_greeting_{i}"
        subprocess.run(["cp", wav, sounds_path + ".wav"], capture_output=True)
        agi.stream_file(f"vox_greeting_{i}")

    turn = 0
    while turn < MAX_TURNS:
        turn += 1

        # Record caller audio
        rec_path = safe_path(os.path.join(AUDIO_TMP, f"input_{turn}"), AUDIO_TMP)
        agi.verbose(f"Recording turn {turn}")
        agi.record_file(rec_path, fmt="wav", timeout=8000, silence=3)

        wav_file = rec_path + ".wav"
        if not os.path.exists(wav_file):
            logging.warning(f"No audio on turn {turn}")
            continue

        # Transcribe
        result = whisper_model.transcribe(wav_file)
        detected_lang = result.get("language", "en")
        if detected_lang in ("ar", "fa", "ps"):
            result = whisper_model.transcribe(wav_file, language="ur")
            detected_lang = "ur"
        query = result["text"].strip()
        if sum(1 for c in query if '\u0600' <= c <= '\u06FF') > 2:
            detected_lang = "ur"

        if not query:
            continue

        # Sanitize input — strip control chars, enforce max length
        query = sanitize_query(query)

        # ── Layer 1 ───────────────────────────────────────────
        response, tag, confidence = handler.generate_adaptive_response(query, detected_lang)

        # ── Layer 2 ───────────────────────────────────────────
        if confidence < THRESHOLD:
            response, tag, confidence = classifier.get_response(query, language=detected_lang)

        # ── Layer 3: RAG + LLM ────────────────────────────────
        if confidence < THRESHOLD:
            english_query = translate(query, "ur", "en") if detected_lang == "ur" else query
            history_text  = build_history_text(conversation_history[-4:])
            rag_chain = (
                {"context": retriever, "question": RunnablePassthrough(), "history": lambda _: history_text}
                | rag_prompt | llm | StrOutputParser()
            )
            response = rag_chain.invoke(english_query)
            if detected_lang == "ur":
                response = translate(response, "en", "ur")

        # Privacy-safe logging — no raw query text
        safe_log(turn, detected_lang, tag, confidence)

        # Play response
        audio_path = safe_path(os.path.join(AUDIO_TMP, f"resp_{turn}"), AUDIO_TMP)
        wav = text_to_urdu_audio(response, audio_path)
        sounds_path = f"/var/lib/asterisk/sounds/vox_resp_{turn}"
        subprocess.run(["cp", wav, sounds_path + ".wav"], capture_output=True)
        agi.stream_file(f"vox_resp_{turn}")

        # Clean up audio files after playing
        for f in [wav, sounds_path + ".wav"]:
            if os.path.exists(f):
                os.unlink(f)

        if tag == "goodbye":
            break

        conversation_history.append({"user": query, "assistant": response})
        if len(conversation_history) > MAX_HISTORY:
            conversation_history.pop(0)

    agi.hangup()
    logging.info(f"Call ended | caller={caller_id} | turns={turn}")


if __name__ == "__main__":
    try:
        handle_call()
    except Exception as e:
        logging.error(f"Call error: {e}", exc_info=True)
