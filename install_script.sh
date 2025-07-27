#!/bin/bash

# Script d'installation pour le système de gestion Video Wall
# Compatible avec Linux, Raspberry Pi OS et macOS

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

# Vérifier si on est root
if [[ $EUID -eq 0 ]]; then
   print_error "Ce script ne doit pas être exécuté en tant que root!"
   print_info "Lancez-le avec votre utilisateur normal, sudo sera utilisé si nécessaire"
   exit 1
fi

echo "======================================"
echo "Installation du système Video Wall"
echo "======================================"
echo

# Détecter le système d'exploitation
if [[ "$OSTYPE" == "darwin"* ]]; then
    OS="macOS"
    VER=$(sw_vers -productVersion)
    IS_MACOS=true
elif [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$NAME
    VER=$VERSION_ID
    IS_MACOS=false
else
    print_error "Impossible de détecter le système d'exploitation"
    exit 1
fi

print_info "Système détecté: $OS $VER"

# Variable globale pour la commande Python
PYTHON_CMD="python3"

# Installation pour macOS
if [ "$IS_MACOS" = true ]; then
    # Vérifier si Homebrew est installé
    if ! command -v brew &> /dev/null; then
        print_info "Installation de Homebrew..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        
        # Ajouter Homebrew au PATH selon l'architecture
        if [[ -f /opt/homebrew/bin/brew ]]; then
            # Apple Silicon
            echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zshrc
            eval "$(/opt/homebrew/bin/brew shellenv)"
            print_status "Homebrew installé (Apple Silicon)"
        elif [[ -f /usr/local/bin/brew ]]; then
            # Intel
            echo 'eval "$(/usr/local/bin/brew shellenv)"' >> ~/.zshrc
            eval "$(/usr/local/bin/brew shellenv)"
            print_status "Homebrew installé (Intel)"
        fi
        
        # Recharger le shell
        source ~/.zshrc 2>/dev/null || true
    else
        print_status "Homebrew déjà installé"
        # S'assurer que Homebrew est dans le PATH
        if [[ -f /opt/homebrew/bin/brew ]]; then
            eval "$(/opt/homebrew/bin/brew shellenv)"
        elif [[ -f /usr/local/bin/brew ]]; then
            eval "$(/usr/local/bin/brew shellenv)"
        fi
    fi
    
    # Mise à jour de Homebrew
    print_info "Mise à jour de Homebrew..."
    brew update
    
    # Vérifier la version de Python
    PYTHON_VERSION=""
    if command -v python3 &> /dev/null; then
        PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
        PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
        PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)
        
        # Si Python 3.11 ou plus récent est déjà installé, on l'utilise
        if [ "$PYTHON_MAJOR" -ge 3 ] && [ "$PYTHON_MINOR" -ge 11 ]; then
            print_status "Python $PYTHON_VERSION déjà installé"
        else
            print_info "Python $PYTHON_VERSION détecté, installation de Python 3.11+..."
            brew install python@3.11
            brew link python@3.11 --overwrite
        fi
    else
        print_info "Installation de Python 3.11+..."
        brew install python@3.11
        brew link python@3.11 --overwrite
    fi
    
    # Installation des outils de développement
    print_info "Installation des outils de développement..."
    brew install git curl
    
else
    # Installation pour Linux/Raspberry Pi
    
    # Mise à jour du système
    print_info "Mise à jour de la liste des paquets..."
    sudo apt-get update -y
    
    # Installation de Python 3 et pip
    print_info "Vérification de Python 3..."
    if ! command -v python3 &> /dev/null; then
        print_info "Installation de Python 3..."
        sudo apt-get install -y python3 python3-dev
    else
        PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
        print_status "Python 3 déjà installé (version $PYTHON_VERSION)"
    fi
    
    # Installation de pip3
    print_info "Vérification de pip3..."
    if ! command -v pip3 &> /dev/null; then
        print_info "Installation de pip3..."
        sudo apt-get install -y python3-pip
    else
        PIP_VERSION=$(pip3 --version 2>&1 | awk '{print $2}')
        print_status "pip3 déjà installé (version $PIP_VERSION)"
    fi
    
    # Installation des dépendances système
    print_info "Installation des dépendances système..."
    sudo apt-get install -y \
        build-essential \
        git \
        curl \
        net-tools \
        iputils-ping \
        traceroute \
        libssl-dev \
        libffi-dev
    
    # Pour Raspberry Pi, installer des dépendances spécifiques
    if [[ "$OS" == *"Raspbian"* ]] || [[ "$OS" == *"Raspberry Pi OS"* ]]; then
        print_info "Configuration spécifique pour Raspberry Pi détectée"
        sudo apt-get install -y \
            libatlas-base-dev \
            libjpeg-dev \
            zlib1g-dev
    fi
fi

# Vérification de pip3
if ! command -v pip3 &> /dev/null; then
    print_info "Installation de pip3..."
    if [ "$IS_MACOS" = true ]; then
        # Sur macOS, pip est normalement installé avec Python via Homebrew
        print_error "pip3 n'est pas trouvé. Vérification de l'installation Python..."
        brew reinstall python@3.11
    else
        curl https://bootstrap.pypa.io/get-pip.py -o get-pip.py
        $PYTHON_CMD get-pip.py --break-system-packages
        rm get-pip.py
    fi
fi

# Installation des dépendances Python
print_info "Installation des dépendances Python..."
if [ -f requirements.txt ]; then
    pip3 install -r requirements.txt --break-system-packages
    print_status "Dépendances Python installées"
else
    print_error "Fichier requirements.txt non trouvé!"
    exit 1
fi

# Création des répertoires nécessaires
print_info "Création des répertoires..."
directories=("cameras" "scenes" "templates" "logs")
for dir in "${directories[@]}"; do
    if [ ! -d "$dir" ]; then
        mkdir -p "$dir"
        print_status "Répertoire '$dir' créé"
    else
        print_info "Répertoire '$dir' existe déjà"
    fi
done

# Vérification et copie du fichier cameras.json
if [ ! -f "cameras/cameras.json" ] && [ -f "cameras.json" ]; then
    print_info "Déplacement du fichier cameras.json..."
    mv cameras.json cameras/
    print_status "Fichier cameras.json déplacé dans le répertoire cameras/"
fi

# Permissions d'exécution pour les scripts Python
print_info "Configuration des permissions..."
chmod +x *.py 2>/dev/null || true

# Test de connexion réseau
print_info "Test de connectivité réseau..."
if ping -c 1 8.8.8.8 &> /dev/null; then
    print_status "Connexion Internet OK"
else
    print_error "Pas de connexion Internet détectée"
    print_info "Le système peut fonctionner en réseau local"
fi

# Création d'un script de démarrage
cat > start_server.sh << 'EOF'
#!/bin/bash

# Démarrer le serveur
echo "Démarrage du serveur Video Wall..."
python3 W0nd3rW4ll_Server_Web.py
EOF

chmod +x start_server.sh

# Création d'un script de mise à jour des résolutions
cat > update_resolutions.sh << 'EOF'
#!/bin/bash

# Mettre à jour les résolutions
echo "Mise à jour des résolutions des caméras..."
python3 update_camera_resolutions.py
EOF

chmod +x update_resolutions.sh

echo
echo "======================================"
echo -e "${GREEN}Installation terminée avec succès!${NC}"
echo "======================================"
echo
print_info "Prochaines étapes:"
echo "  1. Mettez à jour les résolutions: ./update_resolutions.sh"
echo "  2. Démarrez le serveur: ./start_server.sh"
echo "  3. Accédez à l'interface: http://localhost:1982"
echo