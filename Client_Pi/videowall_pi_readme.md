# 📺 W0nd3rW4ll Client - Raspberry Pi Video Wall

[![Version](https://img.shields.io/badge/version-1.0-blue.svg)](https://github.com/yourusername/w0nd3rw4ll-pi-client)
[![Python](https://img.shields.io/badge/python-3.11+-green.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-orange.svg)](LICENSE)
[![Raspberry Pi](https://img.shields.io/badge/Raspberry%20Pi-3%20|%204%20|%205-red.svg)](https://www.raspberrypi.org/)

Client optimisé pour Raspberry Pi permettant de transformer votre Pi en écran de surveillance professionnel pour mur d'images. Compatible avec le serveur central W0nd3rW4ll.

## ✨ Fonctionnalités

- 🎥 **Multi-layouts** : Support 1x1, 2x2 et 3x3 avec adaptation automatique de la qualité
- 🔄 **Streaming temps réel** : Gestion intelligente des flux RTSP avec reconnexion automatique
- 🖥️ **Mode mur d'images** : Support natif des configurations 2x2 avec détection des quadrants
- 📊 **Optimisé pour Pi** : Utilisation du décodage matériel et gestion optimale de la mémoire
- 🔌 **Installation simple** : Script d'installation automatique avec toutes les dépendances
- 🚀 **Démarrage automatique** : Service systemd intégré

## 📋 Prérequis

### Matériel
- Raspberry Pi 3B+, 4 ou 5
- Carte SD 16GB minimum (32GB recommandé)
- Alimentation adaptée (5V 3A pour Pi 4/5)
- Connexion réseau (Ethernet recommandé)

### Logiciel
- Raspberry Pi OS (Bookworm ou ultérieur)
- Python 3.11+
- Accès au serveur W0nd3rW4ll

## 🚀 Installation rapide

```bash
# Cloner le repository
git clone https://github.com/yourusername/w0nd3rw4ll-pi-client.git
cd w0nd3rw4ll-pi-client

# Rendre le script exécutable
chmod +x install_pi_client_script.sh

# Lancer l'installation
./install_pi_client_script.sh
```

## ⚙️ Configuration

### 1. Configurer l'adresse du serveur

```bash
./configure_server.sh http://IP_DU_SERVEUR:1982
```

### 2. Paramètres disponibles

Le fichier `pi_client_config.json` contient tous les paramètres :

```json
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
```

## 📖 Utilisation

### Démarrage manuel

```bash
# Mode normal
./start_pi_client.sh

# Mode debug
./start_pi_client.sh --debug

# Avec logs détaillés
./start_pi_client.sh --debug --loglevel DEBUG
```

### Démarrage automatique

```bash
# Activer le service
sudo systemctl enable videowall-client

# Démarrer
sudo systemctl start videowall-client

# Vérifier le statut
sudo systemctl status videowall-client

# Voir les logs
sudo journalctl -u videowall-client -f
```

### Commandes clavier

| Touche | Action |
|--------|--------|
| ESC | Quitter l'application |
| R | Réinitialiser le cache |
| F | Basculer plein écran (debug) |

## 🔧 Dépannage

### Problème : "Impossible d'ouvrir le flux"

1. Vérifier l'URL RTSP avec VLC :
   ```bash
   vlc rtsp://user:pass@IP_CAMERA/stream
   ```

2. Tester la connectivité :
   ```bash
   ping IP_CAMERA
   ```

### Problème : Performance dégradée

1. Vérifier la température :
   ```bash
   vcgencmd measure_temp
   ```

2. Augmenter la mémoire GPU dans `/boot/config.txt` :
   ```
   gpu_mem=256
   ```

3. Utiliser Ethernet plutôt que Wi-Fi

### Test de diagnostic

```bash
# Test OpenCV
python3 -c "import cv2; print(cv2.__version__)"

# Test connexion serveur
curl http://IP_SERVEUR:1982/api/status

# Test RTSP
ffprobe rtsp://user:pass@IP_CAMERA/stream
```

## 🎯 Optimisations

### Optimisations Raspberry Pi

Ajouter dans `/boot/config.txt` :

```
# Mémoire GPU
gpu_mem=256

# Décodage matériel
dtoverlay=vc4-kms-v3d

# Overclocking (avec refroidissement)
over_voltage=2
arm_freq=1500  # Pi 3
# arm_freq=2000  # Pi 4
```

### Optimisations système

```bash
# Désactiver les services inutiles
sudo systemctl disable bluetooth
sudo systemctl disable avahi-daemon

# Monter /tmp en RAM
echo "tmpfs /tmp tmpfs defaults,noatime,nosuid,size=100m 0 0" | sudo tee -a /etc/fstab

# Réduire le swappiness
echo "vm.swappiness=10" | sudo tee -a /etc/sysctl.conf
```

## 📊 Architecture

```
w0nd3rw4ll-pi-client/
├── pi_client.py              # Client principal
├── pi_client_config.json     # Configuration
├── install_pi_client_script.sh   # Script d'installation
├── start_pi_client.sh        # Script de démarrage
├── configure_server.sh       # Configuration serveur
├── requirements_pi.txt       # Dépendances Python
└── README.md                # Ce fichier
```

## 🔌 API

### Communication Socket.IO

Le client communique avec le serveur via Socket.IO :

**Événements émis :**
- `register_screen` : Enregistrement initial
- `heartbeat` : Signal de vie

**Événements reçus :**
- `config_update` : Nouvelle configuration d'affichage
- `disconnect` : Déconnexion du serveur

### Endpoints REST utilisés

- `GET /api/cameras` : Liste des caméras disponibles
- `GET /api/rtsp/{id}?layout={layout}` : URL RTSP adaptée au layout

## 📝 Fichiers de configuration

### pi_client_config.json

Configuration principale du client avec tous les paramètres de fonctionnement.

### Service systemd

Le service est créé automatiquement dans `/etc/systemd/system/videowall-client.service`

## 🐛 Logs et debug

Les logs sont disponibles à plusieurs endroits :

```bash
# Logs en temps réel (mode manuel)
./start_pi_client.sh --loglevel DEBUG

# Logs du service systemd
sudo journalctl -u videowall-client -f

# Logs système
tail -f /var/log/syslog | grep videowall
```
---

Made with ❤️ for Raspberry Pi enthusiasts
