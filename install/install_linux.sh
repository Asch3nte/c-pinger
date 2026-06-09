#!/usr/bin/env bash
# Installation de ClaudePing sur Linux (systemd --user)
# Usage : bash install/install_linux.sh

set -euo pipefail

INSTALL_DIR="$HOME/claudeping"
SERVICE_FILE="$HOME/.config/systemd/user/claudeping.service"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== Installation de ClaudePing ==="

# 1. Vérifications
command -v python3 >/dev/null 2>&1 || { echo "❌ Python3 requis."; exit 1; }
command -v claude >/dev/null 2>&1 || { echo "⚠️  'claude' non trouvé dans PATH. Assurez-vous que Claude Code est installé."; }

# 2. Copie des fichiers
echo "→ Copie vers $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
cp -r "$PROJECT_DIR"/* "$INSTALL_DIR/"

# 3. Config
if [ ! -f "$INSTALL_DIR/config.yaml" ]; then
    cp "$INSTALL_DIR/config.yaml.example" "$INSTALL_DIR/config.yaml"
    echo "→ config.yaml créé depuis l'exemple. Éditez $INSTALL_DIR/config.yaml selon vos besoins."
fi

# 4. Dépendances Python
echo "→ Installation des dépendances Python..."
pip3 install --user -r "$INSTALL_DIR/requirements.txt"

# 5. Service systemd
echo "→ Installation du service systemd..."
mkdir -p "$HOME/.config/systemd/user/"

# Mise à jour du WorkingDirectory dans le service
sed "s|%h/claudeping|$INSTALL_DIR|g" "$SCRIPT_DIR/claudeping.service" > "$SERVICE_FILE"

systemctl --user daemon-reload
systemctl --user enable claudeping
systemctl --user start claudeping

echo ""
echo "✅ ClaudePing installé et démarré !"
echo ""
echo "Commandes utiles :"
echo "  systemctl --user status claudeping    # Statut du service"
echo "  journalctl --user -u claudeping -f    # Logs en temps réel"
echo "  python3 $INSTALL_DIR/main.py status   # Dashboard CLI"
echo "  python3 $INSTALL_DIR/main.py ping-now # Forcer un ping"
