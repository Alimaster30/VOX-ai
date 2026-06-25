import subprocess
import sys


def run(cmd, description):
    print(f"\n>>> {description}")
    result = subprocess.run(cmd, shell=True)
    if result.returncode != 0:
        print(f"[FAILED] {description}")
        sys.exit(1)
    print(f"[OK] {description}")


def check_ollama():
    result = subprocess.run("ollama --version", shell=True, capture_output=True)
    if result.returncode != 0:
        print("[ERROR] Ollama is not installed. Download it from https://ollama.com and re-run this script.")
        sys.exit(1)
    print(f"[OK] Ollama found: {result.stdout.decode().strip()}")


if __name__ == "__main__":
    check_ollama()
    run("pip install -r requirements.txt", "Installing Python dependencies")
    run("ollama pull qwen3.2:3b", "Pulling qwen3.2:3b model")
    run("ollama pull nomic-embed-text", "Pulling nomic-embed-text model")
    print("\n✅ Phase A complete. Drop your PDFs into the data/ folder, then run index.py")
