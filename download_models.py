"""
download_models.py
Downloads all models required by the VOX system.
Run this once after pip install -r requirements.txt

Models downloaded:
  1. Ollama — qwen3.2:3b        (LLM)
  2. Ollama — nomic-embed-text  (Embeddings, ~270MB)
  3. Whisper — base             (STT, ~150MB)
  4. Argos Translate — ur<->en  (Translation, ~100MB)
  5. Sentence Transformers      (Intent classifier, ~120MB)
"""

import subprocess
import sys

def step(msg):
    print(f"\n{'='*55}")
    print(f"  {msg}")
    print(f"{'='*55}")

def ok(msg):  print(f"  [OK] {msg}")
def err(msg): print(f"  [ERROR] {msg}")


# ── 1. Ollama models ──────────────────────────────────────────
step("1/5  Ollama — qwen3.2:3b (LLM)")
try:
    subprocess.run(["ollama", "pull", "qwen3.2:3b"], check=True)
    ok("qwen3.2:3b downloaded")
except FileNotFoundError:
    err("Ollama not installed. Install from https://ollama.com then re-run.")
    sys.exit(1)
except subprocess.CalledProcessError:
    err("Failed to pull qwen3.2:3b — is Ollama running?")
    sys.exit(1)

step("2/5  Ollama — nomic-embed-text (Embeddings ~270MB)")
try:
    subprocess.run(["ollama", "pull", "nomic-embed-text"], check=True)
    ok("nomic-embed-text downloaded")
except subprocess.CalledProcessError:
    err("Failed to pull nomic-embed-text")
    sys.exit(1)


# ── 2. Whisper ────────────────────────────────────────────────
step("3/5  Whisper base model (~150MB)")
try:
    import whisper
    whisper.load_model("base")
    ok("Whisper base model downloaded and cached")
except Exception as e:
    err(f"Whisper download failed: {e}")
    sys.exit(1)


# ── 3. Argos Translate ────────────────────────────────────────
step("4/5  Argos Translate — Urdu <-> English (~100MB)")
try:
    import argostranslate.package
    import argostranslate.translate

    argostranslate.package.update_package_index()
    available = argostranslate.package.get_available_packages()
    installed = [(p.from_code, p.to_code) for p in argostranslate.package.get_installed_packages()]

    for from_code, to_code in [("ur", "en"), ("en", "ur")]:
        if (from_code, to_code) in installed:
            ok(f"{from_code} -> {to_code} already installed")
            continue
        pkg = next((p for p in available if p.from_code == from_code and p.to_code == to_code), None)
        if pkg:
            argostranslate.package.install_from_path(pkg.download())
            ok(f"{from_code} -> {to_code} installed")
        else:
            err(f"Package {from_code} -> {to_code} not found in index")
except Exception as e:
    err(f"Argos Translate download failed: {e}")
    sys.exit(1)


# ── 4. Sentence Transformers ──────────────────────────────────
step("5/5  Sentence Transformers — paraphrase-multilingual-MiniLM-L12-v2 (~120MB)")
try:
    from sentence_transformers import SentenceTransformer
    SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
    ok("Sentence Transformers model downloaded and cached")
except Exception as e:
    err(f"Sentence Transformers download failed: {e}")
    sys.exit(1)


# ── Done ──────────────────────────────────────────────────────
print(f"\n{'='*55}")
print("  All models downloaded successfully!")
print("  Next step: python index.py")
print(f"{'='*55}\n")
