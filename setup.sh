#!/bin/bash
# =============================================================
#  VOX — Server Setup Script
#  Run this once on the university mainframe Linux VM
#  Usage: chmod +x setup.sh && ./setup.sh
# =============================================================

set -e  # exit on any error

echo ""
echo "============================================================"
echo "  VOX System — Automated Setup"
echo "============================================================"

# [1/8] System packages
echo ""
echo "[1/8] Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y \
    python3 python3-pip python3-venv \
    ffmpeg \
    git \
    curl \
    espeak-ng \
    libportaudio2 \
    asterisk \
    asterisk-dev

# [2/8] Python virtual environment
echo ""
echo "[2/8] Setting up Python virtual environment..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip -q

# [3/8] Python dependencies
echo ""
echo "[3/8] Installing Python dependencies..."
pip install -q \
    langchain \
    langchain-community \
    langchain-ollama \
    langchain-chroma \
    langchain-text-splitters \
    chromadb \
    pypdf \
    sentence-transformers \
    faiss-cpu \
    openai-whisper \
    argostranslate \
    gtts \
    pygame \
    pandas \
    openpyxl \
    torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu \
    RealtimeTTS \
    sounddevice \
    numpy \
    flask

# [4/8] Ollama
echo ""
echo "[4/8] Installing Ollama..."
curl -fsSL https://ollama.com/install.sh | sh
echo "  Pulling qwen3.2:3b model..."
ollama pull qwen3.2:3b
echo "  Pulling nomic-embed-text model..."
ollama pull nomic-embed-text

# [5/8] Download all models
echo ""
echo "[5/8] Downloading all models..."
python3 download_models.py

# [6/8] Index documents
echo ""
echo "[6/8] Indexing documents into ChromaDB..."
python3 index.py

# Security: lock down sensitive directories and files
mkdir -p audio_tmp
chmod 700 cache/ audio_tmp/
chmod 600 organizations/default/intents.json 2>/dev/null || true
echo "  [OK] Directory and file permissions secured"

# [7/8] Asterisk AGI setup
echo ""
echo "[7/8] Configuring Asterisk AGI..."
sudo cp agi_bridge.py /var/lib/asterisk/agi-bin/agi_bridge.py
sudo chmod +x /var/lib/asterisk/agi-bin/agi_bridge.py
sudo cp asterisk_dialplan.conf /etc/asterisk/extensions_vox.conf

if ! grep -q "extensions_vox" /etc/asterisk/extensions.conf; then
    echo '#include "extensions_vox.conf"' | sudo tee -a /etc/asterisk/extensions.conf
fi

sudo asterisk -rx "dialplan reload" 2>/dev/null || echo "  (Asterisk not running yet, dialplan will load on start)"

# [8/8] Systemd service with security hardening
echo ""
echo "[8/8] Creating systemd service..."

WORK_DIR=$(pwd)

sudo tee /etc/systemd/system/vox.service > /dev/null <<EOF
[Unit]
Description=VOX Voice Assistant
After=network.target ollama.service

[Service]
Type=simple
User=$USER
WorkingDirectory=$WORK_DIR
Environment=VOX_ROOT=$WORK_DIR
ExecStart=$WORK_DIR/venv/bin/python $WORK_DIR/serve.py
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=$WORK_DIR /var/log/vox.log /var/lib/asterisk/sounds

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable vox
sudo systemctl enable asterisk

echo ""
echo "============================================================"
echo "  Setup Complete!"
echo ""
echo "  Start the VOX system:"
echo "    sudo systemctl start asterisk"
echo "    sudo systemctl start vox"
echo ""
echo "  Check status:"
echo "    sudo systemctl status vox"
echo ""
echo "  View logs:"
echo "    journalctl -u vox -f"
echo "============================================================"
