#!/bin/bash

# Script d'installation pour le client Video Wall sur Raspberry Pi
# Compatible avec Raspberry Pi OS (32-bit et 64-bit)

set -e  # Arrêter en cas d'erreur

# Couleurs pour l'affichage
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Fonction d'affichage
print_status() {
    echo -e "${GREEN}[✓]${NC} $1"
}

print_error() {
    echo -e "${RED}[✗]${NC} $1"
}

print_info() {
    echo -e "${YELLOW}[i]${NC} $1"
}

echo "======================================"
echo "Installation du client Video Wall Pi"
echo "======================================"
echo

# Vérifier qu'on est sur Raspberry Pi
if [ ! -f /proc/device-tree/model ]; then
    print_error "Ce script est conçu pour Raspberry Pi"
    exit 1
fi

MODEL=$(cat /proc/device-tree/model)
print_info "Modèle détecté: $MODEL"

# Mise à jour du système
print_info "Mise à jour du système..."
sudo apt-get update -y

# Installation de Python 3 et pip
print_info "Installation de Python 3 et dépendances..."
sudo apt-get install -y \
    python3 \
    python3-pip \
    python3-dev \
    python3-venv

# Installation des dépendances pour OpenCV et streaming
print_info "Installation des dépendances OpenCV et codecs..."
sudo apt-get install -y \
    libopencv-dev \
    python3-opencv \
    libatlas-base-dev \
    libgstreamer1.0-0 \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly \
    gstreamer1.0-libav \
    gstreamer1.0-tools \
    libgstreamer-plugins-base1.0-dev \
    libgstreamer1.0-dev \
    libgtk-3-0 \
    libavcodec-dev \
    libavformat-dev \
    libswscale-dev \
    libv4l-dev \
    libxvidcore-dev \
    libx264-dev \
    ffmpeg \
    libavcodec-extra \
    v4l-utils

# Optimisations spécifiques Raspberry Pi
print_info "Application des optimisations Raspberry Pi..."

# Augmenter la mémoire GPU pour le décodage vidéo
if grep -q "gpu_mem=" /boot/config.txt; then
    sudo sed -i 's/gpu_mem=.*/gpu_mem=256/' /boot/config.txt
else
    echo "gpu_mem=256" | sudo tee -a /boot/config.txt
fi
print_status "Mémoire GPU configurée à 256MB"

# Activer le décodage matériel
if ! grep -q "dtoverlay=vc4-kms-v3d" /boot/config.txt; then
    echo "dtoverlay=vc4-kms-v3d" | sudo tee -a /boot/config.txt
    print_status "Accélération matérielle activée"
fi

# Installation des dépendances Python
print_info "Installation des dépendances Python..."
cat > requirements_pi.txt << 'EOF'
# Socket.IO pour la communication
python-socketio[client]==5.10.0

# OpenCV est installé via apt
# opencv-python==4.8.1.78

# Autres dépendances
numpy==1.24.3
requests==2.31.0
EOF

pip3 install -r requirements_pi.txt --break-system-packages
print_status "Dépendances Python installées"

# Vérification d'OpenCV
print_info "Vérification d'OpenCV..."
python3 -c "import cv2; print(f'OpenCV version: {cv2.__version__}')" || {
    print_error "OpenCV non trouvé, installation manuelle..."
    pip3 install opencv-python --break-system-packages
}

# Création du fichier de configuration par défaut
if [ ! -f pi_client_config.json ]; then
    print_info "Création du fichier de configuration..."
    cat > pi_client_config.json << 'EOF'
{
    "SERVER_URL": "http://localhost:1982",
    "DEBUG_MODE": false,
    "LOG_LEVEL": "INFO",
    "TARGET_FPS": 25,
    "MAX_FRAME_AGE": 0.2,
    "RECONNECT_INTERVAL": 10,
    "HEARTBEAT_INTERVAL": 30,
    "OPENCV_BACKEND": 128
}
EOF
    print_status "Fichier de configuration créé"
fi

# Script de démarrage
cat > start_pi_client.sh << 'EOF'
#!/bin/bash

# Script de démarrage du client Video Wall pour Raspberry Pi

echo "[START] Démarrage du client Video Wall..."

# Vérifier que nous sommes sur Raspberry Pi
if [ ! -f /proc/device-tree/model ]; then
    echo "[ATTENTION] Attention: Ce script est optimisé pour Raspberry Pi"
fi

# Vérifier la configuration
if [ ! -f pi_client_config.json ]; then
    echo "[ERREUR] Fichier de configuration pi_client_config.json non trouvé!"
    echo "   Exécutez d'abord ./install_pi_client.sh"
    exit 1
fi

# Extraire l'URL du serveur de la configuration
SERVER_URL=$(python3 -c "import json; print(json.load(open('pi_client_config.json'))['SERVER_URL'])" 2>/dev/null)

if [ -z "$SERVER_URL" ]; then
    echo "[ERREUR] Impossible de lire l'URL du serveur depuis la configuration"
    exit 1
fi

echo "[RESEAU] Serveur configuré: $SERVER_URL"

# Vérifier que Python et les dépendances sont installés
if ! command -v python3 &> /dev/null; then
    echo "[ERREUR] Python 3 n'est pas installé"
    exit 1
fi

# Vérifier OpenCV
if ! python3 -c "import cv2" 2>/dev/null; then
    echo "[ERREUR] OpenCV n'est pas installé"
    echo "   Exécutez: sudo apt-get install python3-opencv"
    exit 1
fi

# Vérifier socketio
if ! python3 -c "import socketio" 2>/dev/null; then
    echo "[ERREUR] python-socketio n'est pas installé"
    echo "   Exécutez: pip3 install -r requirements_pi.txt --break-system-packages"
    exit 1
fi

# S'assurer que DISPLAY est défini (nécessaire pour l'affichage)
if [ -z "$DISPLAY" ]; then
    export DISPLAY=:0
    echo "[INFO] Variable DISPLAY définie à :0"
fi

# Options par défaut
DEBUG_MODE=""
LOG_LEVEL=""

# Parsing des arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --debug)
            DEBUG_MODE="--debug"
            echo "[DEBUG] Mode debug activé"
            shift
            ;;
        --loglevel)
            LOG_LEVEL="--loglevel $2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

# Information sur les touches
echo ""
echo "[INFO] Commandes clavier:"
echo "   ESC : Quitter"
echo "   R   : Reset du cache"
echo "   F   : Plein écran (mode debug)"
echo ""

# Démarrer le client
exec python3 pi_client.py $DEBUG_MODE $LOG_LEVEL
EOF

chmod +x start_pi_client.sh
print_status "Script de démarrage créé"

# Script de configuration du serveur
cat > configure_server.sh << 'EOF'
#!/bin/bash

if [ $# -eq 0 ]; then
    echo "Usage: $0 <server_url>"
    echo "Exemple: $0 http://192.168.1.100:1982"
    exit 1
fi

SERVER_URL=$1

# Mise à jour de la configuration
if [ -f pi_client_config.json ]; then
    # Utiliser Python pour modifier le JSON proprement
    python3 -c "
import json
with open('pi_client_config.json', 'r') as f:
    config = json.load(f)
config['SERVER_URL'] = '$SERVER_URL'
with open('pi_client_config.json', 'w') as f:
    json.dump(config, f, indent=4)
"
    echo "✓ Configuration mise à jour avec le serveur: $SERVER_URL"
else
    echo "✗ Fichier pi_client_config.json non trouvé"
    exit 1
fi
EOF

chmod +x configure_server.sh
print_status "Script de configuration créé"

# Service systemd pour démarrage automatique
print_info "Création du service systemd..."
SERVICE_NAME="videowall-client"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

sudo tee $SERVICE_FILE > /dev/null << EOF
[Unit]
Description=Video Wall Client
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$(pwd)
ExecStart=$(pwd)/start_pi_client.sh
Restart=on-failure
RestartSec=10
Environment="DISPLAY=:0"
Environment="XAUTHORITY=/home/$USER/.Xauthority"

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
print_status "Service systemd créé"

# Informations finales
echo
echo "======================================"
echo -e "${GREEN}Installation terminée avec succès!${NC}"
echo "======================================"
echo
print_info "Prochaines étapes:"
echo
echo "  1. Configurer l'adresse du serveur:"
echo "     ./configure_server.sh http://ADRESSE_IP:1982"
echo
echo "  2. Tester le client:"
echo "     ./start_pi_client.sh"
echo
echo "  3. Pour démarrage automatique:"
echo "     sudo systemctl enable ${SERVICE_NAME}"
echo "     sudo systemctl start ${SERVICE_NAME}"
echo
echo "  4. Vérifier les logs:"
echo "     sudo journalctl -u ${SERVICE_NAME} -f"
echo

# Vérifier si un redémarrage est nécessaire
if grep -q "gpu_mem=256" /boot/config.txt && [ ! -d /sys/module/vc4 ]; then
    print_info "Un redémarrage est recommandé pour appliquer les optimisations GPU"
    read -p "Redémarrer maintenant? [o/N] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Oo]$ ]]; then
        sudo reboot
    fi
fi