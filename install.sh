#!/usr/bin/env bash
# install.sh — Deploy CDCN Agent on Raspberry Pi OS 64-bit Bookworm
#
# Usage:
#   bash install.sh           # installs to /opt/cdcn-agent
#   bash install.sh --force   # skip Pi hardware check
#
set -euo pipefail

FORCE=false
for arg in "$@"; do
    [[ "$arg" == "--force" ]] && FORCE=true
done

# ── Colour helpers ─────────────────────────────────────────────────────────────
RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; NC='\033[0m'
info()  { echo -e "${GREEN}==>${NC} $*"; }
warn()  { echo -e "${YELLOW}WARN:${NC} $*"; }
error() { echo -e "${RED}ERROR:${NC} $*" >&2; exit 1; }

# ── 1. Verify hardware ─────────────────────────────────────────────────────────
info "Checking hardware..."
if [[ "$FORCE" == false ]]; then
    if [[ ! -f /proc/device-tree/model ]]; then
        warn "Cannot read /proc/device-tree/model — this does not look like a Raspberry Pi."
        warn "Run with --force to install anyway."
        exit 1
    fi
    MODEL=$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo "")
    if ! echo "$MODEL" | grep -qi "raspberry pi"; then
        warn "Hardware model: '$MODEL'"
        warn "This script targets Raspberry Pi OS Bookworm 64-bit."
        warn "Run with --force to install anyway."
        exit 1
    fi
    info "Hardware: $MODEL — OK"
fi

# ── 2. System dependencies ─────────────────────────────────────────────────────
info "Installing system dependencies..."
sudo apt-get update -qq
sudo apt-get install -y \
    python3 \
    python3-venv \
    python3-dev \
    git \
    python3-magic \
    libmagic1 \
    sqlite3 \
    curl \
    poppler-utils \
    --no-install-recommends

# ── 3. System user ─────────────────────────────────────────────────────────────
info "Creating cdcn-agent system user..."
if ! id cdcn-agent &>/dev/null; then
    sudo useradd \
        --system \
        --no-create-home \
        --shell /usr/sbin/nologin \
        --comment "CDCN Agent service account" \
        cdcn-agent
    info "User cdcn-agent created."
else
    info "User cdcn-agent already exists — skipping."
fi

# ── 4. Copy project to /opt/cdcn-agent ────────────────────────────────────────
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/cdcn-agent"

info "Installing project to $INSTALL_DIR..."
sudo mkdir -p "$INSTALL_DIR"
sudo rsync -a --delete \
    --exclude='.git' \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.env' \
    --exclude='data/' \
    "$REPO_DIR/" "$INSTALL_DIR/"
sudo chown -R cdcn-agent:cdcn-agent "$INSTALL_DIR"
sudo chmod 755 "$INSTALL_DIR"

# ── 5. Python virtual environment ─────────────────────────────────────────────
info "Creating Python virtual environment..."
sudo -u cdcn-agent python3 -m venv "$INSTALL_DIR/.venv"

info "Installing Python dependencies..."
sudo -u cdcn-agent "$INSTALL_DIR/.venv/bin/pip" install \
    --upgrade pip setuptools wheel --quiet
sudo -u cdcn-agent "$INSTALL_DIR/.venv/bin/pip" install \
    -r "$INSTALL_DIR/requirements.txt" --quiet

# ── 6. Runtime directories ─────────────────────────────────────────────────────
info "Creating runtime directories..."
sudo mkdir -p \
    /etc/cdcn-agent \
    /var/lib/cdcn-agent/documents \
    /var/lib/cdcn-agent/chroma \
    /var/lib/cdcn-agent/memory/journal \
    /var/lib/cdcn-agent/memory/session_log \
    /var/lib/cdcn-agent/pending_changes/applied \
    /var/lib/cdcn-agent/pending_changes/rejected \
    /var/log/cdcn-agent

sudo chown -R cdcn-agent:cdcn-agent \
    /var/lib/cdcn-agent \
    /var/log/cdcn-agent

sudo chmod 700 /etc/cdcn-agent
sudo chmod 750 /var/lib/cdcn-agent /var/log/cdcn-agent

# ── 7. Environment file ────────────────────────────────────────────────────────
info "Setting up configuration..."
if [[ ! -f /etc/cdcn-agent/.env ]]; then
    sudo cp "$INSTALL_DIR/.env.example" /etc/cdcn-agent/.env

    # Set absolute paths for the system installation
    sudo sed -i \
        -e 's|^WATCHED_FOLDER=.*|WATCHED_FOLDER=/var/lib/cdcn-agent/documents|' \
        -e 's|^CHROMA_PATH=.*|CHROMA_PATH=/var/lib/cdcn-agent/chroma|' \
        -e 's|^MEMORY_PATH=.*|MEMORY_PATH=/var/lib/cdcn-agent/memory|' \
        -e 's|^AUDIT_LOG_PATH=.*|AUDIT_LOG_PATH=/var/lib/cdcn-agent/cdcn_audit.db|' \
        -e 's|^PENDING_CHANGES_PATH=.*|PENDING_CHANGES_PATH=/var/lib/cdcn-agent/pending_changes|' \
        /etc/cdcn-agent/.env

    sudo chown root:cdcn-agent /etc/cdcn-agent/.env
    sudo chmod 640 /etc/cdcn-agent/.env

    echo ""
    echo -e "${YELLOW}  ✎ Edit /etc/cdcn-agent/.env before starting the service${NC}"
    echo ""
else
    info "/etc/cdcn-agent/.env already exists — skipping."
fi

# Symlink so the app finds .env from its working directory
sudo ln -sf /etc/cdcn-agent/.env "$INSTALL_DIR/.env"

# ── 8. CLI wrapper ─────────────────────────────────────────────────────────────
info "Installing cdcn-agent CLI..."
sudo tee /usr/local/bin/cdcn-agent > /dev/null << 'EOF'
#!/usr/bin/env bash
cd /opt/cdcn-agent && exec /opt/cdcn-agent/.venv/bin/python -m app.cli "$@"
EOF
sudo chmod +x /usr/local/bin/cdcn-agent

# ── 9. Systemd service ────────────────────────────────────────────────────────
info "Installing systemd service..."
sudo cp "$INSTALL_DIR/systemd/cdcn-agent.service" /etc/systemd/system/cdcn-agent.service
sudo systemctl daemon-reload
sudo systemctl enable cdcn-agent.service
info "Service enabled (not started — edit .env first)."

# ── Next steps ────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║        CDCN Agent installed — next steps                     ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "a) Edit the configuration:"
echo "   sudo nano /etc/cdcn-agent/.env"
echo ""
echo "   Key values to set:"
echo "   • OLLAMA_BASE_URL      — R710 LAN IP e.g. http://192.168.1.100:11434"
echo "   • DREAM_OLLAMA_BASE_URL — http://127.0.0.1:11434  (Pi local Ollama)"
echo "   • WAKEONLAN_MAC        — R710 MAC address e.g. AA:BB:CC:DD:EE:FF"
echo "   • DISCORD_BOT_TOKEN    — from discord.com/developers"
echo "   • TELEGRAM_BOT_TOKEN   — from @BotFather"
echo "   • SECRET_KEY           — run: openssl rand -hex 32"
echo ""
echo "b) Install Ollama on this Pi (dream mode inference):"
echo "   curl -fsSL https://ollama.com/install.sh | sh"
echo "   ollama pull phi3:mini"
echo "   ollama pull nomic-embed-text"
echo ""
echo "c) Install Ollama on the R710 (see docs/r710-setup.md):"
echo "   curl -fsSL https://ollama.com/install.sh | sh"
echo "   # Edit /etc/systemd/system/ollama.service:"
echo "   # Environment=OLLAMA_HOST=0.0.0.0:11434"
echo "   ollama pull llama3.1:14b"
echo ""
echo "d) Start the service:"
echo "   sudo systemctl start cdcn-agent"
echo "   sudo journalctl -u cdcn-agent -f"
echo ""
echo "e) Create the first admin user:"
echo "   cdcn-agent add-user admin admin"
echo ""
echo "f) (Optional) Install Tailscale for remote web UI access:"
echo "   curl -fsSL https://tailscale.com/install.sh | sh"
echo "   sudo tailscale up --ssh"
echo ""
echo "   Then access the web UI at:"
echo "   http://<pi-tailscale-ip>:8400"
echo ""
