#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Client Video Wall pour Raspberry Pi
Affiche les flux de caméras selon la configuration reçue du serveur central
"""

import socketio
import cv2
import numpy as np
import threading
import time
import socket
import requests
import logging
from queue import Queue, Empty
import signal
import sys
import os
import unicodedata
import json

# Forcer l'encodage UTF-8
import locale
try:
    locale.setlocale(locale.LC_ALL, 'fr_FR.UTF-8')
except:
    try:
        locale.setlocale(locale.LC_ALL, 'C.UTF-8')
    except:
        pass

# Configuration par défaut
DEFAULT_CONFIG = {
    'SERVER_URL': 'http://localhost:1982',
    'DEBUG_MODE': False,
    'LOG_LEVEL': 'INFO',
    'TARGET_FPS': 25,
    'MAX_FRAME_AGE': 0.2,  # 200ms
    'RECONNECT_INTERVAL': 10,
    'HEARTBEAT_INTERVAL': 30,
    'OPENCV_BACKEND': cv2.CAP_FFMPEG
}

# Configuration du logging avec encodage UTF-8
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

# Forcer l'encodage UTF-8 pour les handlers de logging
for handler in logging.root.handlers:
    if hasattr(handler, 'stream'):
        if hasattr(handler.stream, 'reconfigure'):
            handler.stream.reconfigure(encoding='utf-8')

logger = logging.getLogger(__name__)

def clean_text_for_opencv(text):
    """Supprime les accents pour l'affichage OpenCV"""
    nfd = unicodedata.normalize('NFD', text)
    return ''.join(char for char in nfd if unicodedata.category(char) != 'Mn')

def load_config(config_file='pi_client_config.json'):
    """Charge la configuration depuis un fichier JSON"""
    config = DEFAULT_CONFIG.copy()
    
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r') as f:
                user_config = json.load(f)
                config.update(user_config)
                logger.info(f"Configuration chargée depuis {config_file}")
        except Exception as e:
            logger.warning(f"Erreur chargement config: {e}, utilisation config par défaut")
    
    return config

class PiVideoWall:
    def __init__(self, server_url, pi_name=None, debug_mode=False, config=None):
        self.config = config or DEFAULT_CONFIG
        self.server_url = server_url
        self.pi_name = pi_name or socket.gethostname()
        self.pi_ip = self.get_local_ip()
        self.debug_mode = debug_mode
        self.sio = socketio.Client(logger=False, engineio_logger=False)
        self.current_layout = '2x2'
        self.current_cameras = []
        self.video_threads = {}
        self.frame_queues = {}
        self.running = True
        self.screen_width = 1920
        self.screen_height = 1080
        self.is_connected = False
        
        # Support du mode mur d'images
        self.video_wall_mode = False
        self.quadrant = None
        
        # Dictionnaire pour stocker la dernière frame valide de chaque caméra
        self.last_valid_frames = {}
        
        # Dictionnaire pour stocker les timestamps des dernières frames
        self.last_frame_timestamps = {}
        
        # Dictionnaire pour stocker les infos des caméras (nom, etc.)
        self.camera_info = {}
        
        # Configuration OpenCV
        self.window_name = f'Video Wall - {self.pi_name}'
        
        # Paramètres de synchronisation
        self.target_fps = self.config['TARGET_FPS']
        self.frame_interval = 1.0 / self.target_fps
        self.max_frame_age = self.config['MAX_FRAME_AGE']
        
        # Statistiques
        self.stats = {
            'frames_displayed': 0,
            'connection_attempts': 0,
            'start_time': time.time()
        }
        
        # Enregistrement des handlers Socket.IO
        self.setup_socketio_handlers()
        
    def get_local_ip(self):
        """Obtient l'IP locale du Pi"""
        try:
            # Méthode plus fiable pour obtenir l'IP locale
            hostname = socket.gethostname()
            local_ip = socket.gethostbyname(hostname)
            
            # Si on obtient 127.0.0.1, essayer une autre méthode
            if local_ip.startswith('127.'):
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    s.connect(('8.8.8.8', 80))
                    local_ip = s.getsockname()[0]
                finally:
                    s.close()
            
            return local_ip
        except Exception as e:
            logger.error(f"Erreur obtention IP: {e}")
            return "127.0.0.1"
    
    def setup_socketio_handlers(self):
        """Configure les handlers Socket.IO"""
        @self.sio.event
        def connect():
            logger.info("✅ Connecté au serveur central")
            self.is_connected = True
            self.sio.emit('register_screen', {
                'ip': self.pi_ip,
                'name': self.pi_name,
                'version': '1.0',  # Version du client
                'capabilities': {
                    'layouts': ['1x1', '2x2', '3x3'],
                    'video_wall': True,
                    'max_cameras': 9
                }
            })
        
        @self.sio.event
        def disconnect():
            logger.warning("❌ Déconnecté du serveur central")
            self.is_connected = False
        
        @self.sio.event
        def config_update(data):
            logger.info("📋 Nouvelle configuration reçue: %s", data)
            self.video_wall_mode = data.get('video_wall_mode', False)
            self.quadrant = data.get('quadrant', None)
            if self.video_wall_mode:
                logger.info(f"🖥️ Mode mur d'images activé - Quadrant: {self.quadrant}")
            self.update_display(data['layout'], data['cameras'])
        
        @self.sio.event
        def connect_error(data):
            logger.error("❌ Erreur de connexion: %s", data)
    
    def start(self):
        """Démarre le client"""
        logger.info(f"🚀 Démarrage du client Video Wall")
        logger.info(f"📍 Nom: {self.pi_name}, IP: {self.pi_ip}")
        logger.info(f"🖥️ OpenCV: {cv2.__version__}, Backend: {self.config['OPENCV_BACKEND']}")
        
        # Créer la fenêtre OpenCV
        cv2.namedWindow(self.window_name, cv2.WINDOW_NORMAL)
        
        if not self.debug_mode:
            cv2.setWindowProperty(self.window_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
        else:
            cv2.resizeWindow(self.window_name, 1280, 720)
            logger.info("🐛 Mode debug activé - Fenêtre non plein écran")
        
        # Tenter la connexion au serveur
        connected = False
        retry_count = 0
        max_retries = 3
        
        while not connected and retry_count < max_retries:
            try:
                self.stats['connection_attempts'] += 1
                logger.info(f"🔌 Connexion au serveur {self.server_url}... (tentative {retry_count + 1}/{max_retries})")
                
                # Charger les infos des caméras (non bloquant si échec)
                self.load_all_cameras()
                
                # Connexion Socket.IO
                self.sio.connect(self.server_url, wait_timeout=5)
                connected = True
                self.is_connected = True
                logger.info("✅ Connecté au serveur avec succès")
                
                # Thread pour le heartbeat
                heartbeat_thread = threading.Thread(target=self.send_heartbeat, name="Heartbeat")
                heartbeat_thread.daemon = True
                heartbeat_thread.start()
                
            except Exception as e:
                retry_count += 1
                logger.warning(f"⚠️ Échec de connexion: {e}")
                if retry_count < max_retries:
                    logger.info(f"⏳ Nouvelle tentative dans 2 secondes...")
                    time.sleep(2)
                else:
                    logger.error("❌ Impossible de se connecter au serveur")
                    logger.info("📡 Démarrage en mode hors ligne")
        
        # Thread de reconnexion automatique
        if not connected:
            reconnect_thread = threading.Thread(target=self.auto_reconnect, name="AutoReconnect")
            reconnect_thread.daemon = True
            reconnect_thread.start()
        
        # Thread de statistiques
        stats_thread = threading.Thread(target=self.print_stats, name="Stats")
        stats_thread.daemon = True
        stats_thread.start()
        
        # Boucle d'affichage principale
        try:
            self.display_loop()
        except KeyboardInterrupt:
            logger.info("⌨️ Interruption clavier détectée")
        except Exception as e:
            logger.error(f"❌ Erreur dans la boucle d'affichage: {e}")
        finally:
            self.cleanup()
    
    def auto_reconnect(self):
        """Tente de se reconnecter automatiquement au serveur"""
        while self.running and not self.is_connected:
            time.sleep(self.config['RECONNECT_INTERVAL'])
            if not self.is_connected:
                try:
                    self.stats['connection_attempts'] += 1
                    logger.info("🔄 Tentative de reconnexion automatique...")
                    self.sio.connect(self.server_url, wait_timeout=5)
                except Exception as e:
                    logger.debug(f"Reconnexion échouée: {e}")
    
    def send_heartbeat(self):
        """Envoie un heartbeat périodique au serveur"""
        while self.running:
            if self.sio.connected:
                try:
                    # Envoyer heartbeat sans données pour compatibilité serveur
                    self.sio.emit('heartbeat')
                except:
                    pass
            time.sleep(self.config['HEARTBEAT_INTERVAL'])
    
    def print_stats(self):
        """Affiche des statistiques périodiques"""
        while self.running:
            time.sleep(60)  # Toutes les minutes
            if self.running:
                uptime = int(time.time() - self.stats['start_time'])
                logger.info(f"📊 Stats - Uptime: {uptime//60}min, Frames: {self.stats['frames_displayed']}, "
                          f"Connexions: {self.stats['connection_attempts']}")
    
    def update_display(self, layout, camera_ids):
        """Met à jour l'affichage avec la nouvelle configuration"""
        logger.info(f"🔄 Mise à jour: layout {self.current_layout} → {layout}")
        
        # Filtrer les None/null pour avoir seulement les IDs réels
        old_active_cameras = [c for c in self.current_cameras if c is not None]
        new_active_cameras = [c for c in camera_ids if c is not None]
        
        logger.info(f"📷 Caméras: {len(old_active_cameras)} → {len(new_active_cameras)}")
        
        # Vérifier si le layout a changé
        layout_changed = self.current_layout != layout
        
        # Convertir en sets pour faciliter les comparaisons
        old_cameras = set(old_active_cameras)
        new_cameras = set(new_active_cameras)
        
        # Si le layout a changé, on doit redémarrer toutes les caméras
        if layout_changed:
            cameras_to_remove = old_cameras
            cameras_to_add = new_cameras
            cameras_to_keep = set()
        else:
            # Identifier les changements normaux
            cameras_to_remove = old_cameras - new_cameras
            cameras_to_add = new_cameras - old_cameras
            cameras_to_keep = old_cameras & new_cameras
        
        if cameras_to_remove:
            logger.info(f"➖ Suppression: {cameras_to_remove}")
        if cameras_to_add:
            logger.info(f"➕ Ajout: {cameras_to_add}")
        if cameras_to_keep:
            logger.debug(f"✅ Conservation: {cameras_to_keep}")
        
        # Mettre à jour le layout AVANT de redémarrer les caméras
        self.current_layout = layout
        
        # Arrêter seulement les caméras qui sont supprimées
        for cam_id in cameras_to_remove:
            self.stop_single_camera(cam_id)
        
        # Mettre à jour la liste des caméras (avec les null pour garder les positions)
        self.current_cameras = camera_ids
        
        # Démarrer seulement les nouvelles caméras
        for cam_id in cameras_to_add:
            self.start_camera_thread(cam_id)
    
    def load_all_cameras(self):
        """Charge toutes les infos des caméras une seule fois"""
        try:
            response = requests.get(f"{self.server_url}/api/cameras", timeout=5)
            if response.status_code == 200:
                cameras = response.json()
                for cam in cameras:
                    self.camera_info[cam['id']] = {
                        'name': cam.get('name', f'Camera {cam["id"]}'),
                        'zone': cam.get('zone', ''),
                        'ip': cam.get('ip', ''),
                        'model': cam.get('model', 'Unknown')
                    }
                logger.info(f"📥 Chargé {len(self.camera_info)} caméras dans le cache")
                return True
        except requests.exceptions.ConnectionError:
            logger.warning(f"⚠️ Impossible de se connecter au serveur pour charger les caméras")
        except requests.exceptions.Timeout:
            logger.warning("⏱️ Timeout lors du chargement des caméras")
        except Exception as e:
            logger.error(f"❌ Erreur chargement des caméras: {e}")
        return False
    
    def get_camera_info(self, camera_id):
        """Récupère les informations d'une caméra depuis le cache"""
        return self.camera_info.get(camera_id, {
            'name': f'Camera {camera_id}',
            'zone': 'Unknown',
            'ip': 'Unknown',
            'model': 'Unknown'
        })
    
    def get_rtsp_url(self, camera_id):
        """Récupère l'URL RTSP d'une caméra depuis le serveur"""
        try:
            url = f"{self.server_url}/api/rtsp/{camera_id}?layout={self.current_layout}"
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                rtsp_url = response.json()['url']
                cam_info = self.get_camera_info(camera_id)
                logger.info(f"🔗 URL RTSP pour {cam_info['name']} (layout: {self.current_layout})")
                return rtsp_url
            else:
                logger.error(f"❌ Erreur HTTP {response.status_code} pour caméra {camera_id}")
        except Exception as e:
            logger.error(f"❌ Erreur récupération URL RTSP: {e}")
        return None
    
    def start_camera_thread(self, camera_id):
        """Démarre un thread pour capturer une caméra"""
        rtsp_url = self.get_rtsp_url(camera_id)
        if not rtsp_url:
            logger.error(f"❌ Impossible d'obtenir l'URL pour la caméra {camera_id}")
            return
        
        cam_info = self.get_camera_info(camera_id)
        
        # Queue plus petite et avec stratégie LIFO pour toujours avoir la frame la plus récente
        queue = Queue(maxsize=2)
        self.frame_queues[camera_id] = queue
        
        thread = threading.Thread(
            target=self.capture_camera, 
            args=(camera_id, rtsp_url, queue),
            name=f"Camera-{camera_id}"
        )
        thread.daemon = True
        thread.start()
        self.video_threads[camera_id] = thread
        
        logger.info(f"🎬 Thread démarré pour {cam_info['name']} ({cam_info['model']})")
    
    def capture_camera(self, camera_id, rtsp_url, queue):
        """Capture les frames d'une caméra avec gestion optimisée des buffers"""
        cam_info = self.get_camera_info(camera_id)
        logger.info(f"📹 Démarrage capture {cam_info['name']} (ID: {camera_id})")
        
        logger.info(f"🔗 URL RTSP tentée: {rtsp_url}")
        
        cap = cv2.VideoCapture(rtsp_url)
        
        # Configuration pour minimiser le buffering
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        
        # Tentative de connexion avec timeout
        start_time = time.time()
        timeout = 10
        
        while not cap.isOpened() and (time.time() - start_time) < timeout:
            logger.warning(f"⏳ Attente ouverture flux {cam_info['name']}... ({int(time.time() - start_time)}s)")
            time.sleep(1)
            cap.release()
            cap = cv2.VideoCapture(rtsp_url)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        
        if not cap.isOpened():
            logger.error(f"❌ Impossible d'ouvrir le flux pour {cam_info['name']} après {timeout}s")
            return
        
        # Test de lecture initial
        ret, test_frame = cap.read()
        if not ret or test_frame is None:
            logger.error(f"❌ Échec de la première lecture pour {cam_info['name']}")
            cap.release()
            return
        
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        backend = cap.getBackendName()
        
        logger.info(f"✅ Flux ouvert pour {cam_info['name']}: {width}x{height} @ {fps:.0f}fps ({backend})")
        
        consecutive_failures = 0
        frame_count = 0
        last_log_time = time.time()
        
        # Variables pour le skip de frames
        frames_to_skip = 0
        
        while self.running and camera_id in [c for c in self.current_cameras if c is not None]:
            try:
                current_time = time.time()
                
                # Stratégie de skip adaptative basée sur la charge de la queue
                if queue.qsize() >= 1:
                    # Si la queue contient des frames, on skip pour rattraper
                    frames_to_skip = max(2, int(fps / 10))  # Skip plus de frames si FPS élevé
                else:
                    frames_to_skip = 0
                
                # Lire et potentiellement skipper des frames
                for _ in range(frames_to_skip + 1):
                    ret, frame = cap.read()
                    if not ret:
                        break
                
                if ret and frame is not None:
                    consecutive_failures = 0
                    frame_count += 1
                    
                    # Timestamp de la frame
                    frame_timestamp = current_time
                    
                    # Log périodique
                    if current_time - last_log_time > 30:  # Log toutes les 30s
                        logger.debug(f"📊 {cam_info['name']}: {frame_count} frames, queue: {queue.qsize()}")
                        last_log_time = current_time
                    
                    # Gestion de la queue avec remplacement immédiat
                    try:
                        # Si la queue est pleine, on retire l'ancienne frame
                        if queue.full():
                            try:
                                queue.get_nowait()
                            except Empty:
                                pass
                        
                        # Ajouter la nouvelle frame avec son timestamp
                        queue.put((frame, frame_timestamp), block=False)
                        
                    except:
                        pass  # En cas d'erreur, on continue
                else:
                    consecutive_failures += 1
                    
                    if consecutive_failures > 50:  # ~2 secondes à 25fps
                        logger.warning(f"⚠️ Perte de flux pour {cam_info['name']}, reconnexion...")
                        cap.release()
                        time.sleep(2)
                        
                        # Reconnexion avec les mêmes options optimisées
                        cap = cv2.VideoCapture(rtsp_url)
                        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                        
                        reconnect_start = time.time()
                        while not cap.isOpened() and (time.time() - reconnect_start) < 5:
                            time.sleep(0.5)
                        
                        if cap.isOpened():
                            ret, test_frame = cap.read()
                            if ret and test_frame is not None:
                                logger.info(f"✅ Reconnexion réussie pour {cam_info['name']}")
                                consecutive_failures = 0
                            else:
                                logger.error(f"❌ Reconnexion échouée pour {cam_info['name']}")
                                break
                        else:
                            logger.error(f"❌ Impossible de rouvrir le flux pour {cam_info['name']}")
                            break
                
                # Pause adaptative pour ne pas surcharger le CPU
                if frames_to_skip > 0:
                    time.sleep(0.001)  # Pause minimale si on skip
                else:
                    time.sleep(0.01)  # Pause normale
                
            except Exception as e:
                logger.error(f"❌ Erreur capture {cam_info['name']}: {e}")
                break
        
        cap.release()
        logger.info(f"🛑 Thread arrêté pour {cam_info['name']} après {frame_count} frames")
    
    def stop_single_camera(self, camera_id):
        """Arrête le thread d'une seule caméra"""
        cam_info = self.get_camera_info(camera_id)
        logger.info(f"🛑 Arrêt de {cam_info['name']} (ID: {camera_id})")
        
        time.sleep(0.1)
        
        if camera_id in self.video_threads:
            del self.video_threads[camera_id]
        if camera_id in self.frame_queues:
            del self.frame_queues[camera_id]
        if camera_id in self.last_valid_frames:
            del self.last_valid_frames[camera_id]
        if camera_id in self.last_frame_timestamps:
            del self.last_frame_timestamps[camera_id]
    
    def stop_video_threads(self):
        """Arrête tous les threads vidéo"""
        logger.info("🛑 Arrêt de tous les threads vidéo...")
        self.current_cameras = []
        time.sleep(0.5)
        
        self.video_threads.clear()
        self.frame_queues.clear()
        self.last_valid_frames.clear()
        self.last_frame_timestamps.clear()
        
        logger.info("✅ Tous les threads vidéo arrêtés")
    
    def create_layout_grid(self):
        """Crée une grille selon le layout"""
        layouts = {
            '1x1': (1, 1),
            '2x2': (2, 2),
            '3x3': (3, 3)
        }
        return layouts.get(self.current_layout, (2, 2))
    
    def get_quadrant_crop(self, frame, quadrant):
        """Retourne la portion de la frame correspondant au quadrant"""
        h, w = frame.shape[:2]
        half_w = w // 2
        half_h = h // 2
        
        quadrants = {
            'top-left': (0, half_h, 0, half_w),
            'top-right': (0, half_h, half_w, w),
            'bottom-left': (half_h, h, 0, half_w),
            'bottom-right': (half_h, h, half_w, w)
        }
        
        if quadrant in quadrants:
            y1, y2, x1, x2 = quadrants[quadrant]
            return frame[y1:y2, x1:x2]
        return frame
    
    def display_loop(self):
        """Boucle principale d'affichage avec synchronisation améliorée"""
        logger.info("🎬 Démarrage de la boucle d'affichage")
        last_display_time = time.time()
        last_log_time = time.time()
        
        while self.running:
            current_time = time.time()
            
            # Calculer le temps écoulé depuis le dernier affichage
            time_since_last_display = current_time - last_display_time
            
            # Attendre si nécessaire pour maintenir le FPS cible
            if time_since_last_display < self.frame_interval:
                time.sleep(self.frame_interval - time_since_last_display)
                current_time = time.time()
            
            last_display_time = current_time
            self.stats['frames_displayed'] += 1
            
            # Log périodique
            if current_time - last_log_time > 30:  # Log toutes les 30s
                active_cameras = [c for c in self.current_cameras if c is not None]
                if active_cameras:
                    logger.info(f"📊 Affichage - Caméras actives: {len(active_cameras)}, "
                              f"Layout: {self.current_layout}, FPS: {self.target_fps}")
                last_log_time = current_time
            
            if not self.current_cameras:
                # Écran d'attente
                black_screen = np.zeros((self.screen_height, self.screen_width, 3), dtype=np.uint8)
                
                # Titre principal
                main_text = f"{self.pi_name}"
                text_size = cv2.getTextSize(main_text, cv2.FONT_HERSHEY_SIMPLEX, 2, 3)[0]
                text_x = (self.screen_width - text_size[0]) // 2
                cv2.putText(black_screen, main_text, 
                           (text_x, self.screen_height//2 - 50), cv2.FONT_HERSHEY_SIMPLEX, 
                           2, (255, 255, 255), 3)
                
                # Sous-titre
                subtitle = "En attente de configuration"
                text_size = cv2.getTextSize(subtitle, cv2.FONT_HERSHEY_SIMPLEX, 1, 2)[0]
                text_x = (self.screen_width - text_size[0]) // 2
                cv2.putText(black_screen, subtitle,
                           (text_x, self.screen_height//2 + 20), cv2.FONT_HERSHEY_SIMPLEX,
                           1, (200, 200, 200), 2)
                
                # Statut de connexion
                if self.is_connected:
                    status_text = "Connexion au serveur : OK"
                    status_color = (0, 255, 0)
                else:
                    status_text = "Hors ligne - Reconnexion..."
                    status_color = (0, 165, 255)
                
                status_full = f"{status_text}"
                text_size = cv2.getTextSize(status_full, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)[0]
                text_x = (self.screen_width - text_size[0]) // 2
                cv2.putText(black_screen, status_full,
                           (text_x, self.screen_height//2 + 80), cv2.FONT_HERSHEY_SIMPLEX,
                           0.8, status_color, 2)
                
                # Informations système
                info_text = f"IP: {self.pi_ip} | Serveur: {self.server_url}"
                text_size = cv2.getTextSize(info_text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)[0]
                text_x = (self.screen_width - text_size[0]) // 2
                cv2.putText(black_screen, info_text,
                           (text_x, self.screen_height - 50), cv2.FONT_HERSHEY_SIMPLEX,
                           0.6, (100, 100, 100), 1)
                
                cv2.imshow(self.window_name, black_screen)
            else:
                # Créer la mosaïque
                rows, cols = self.create_layout_grid()
                cell_width = self.screen_width // cols
                cell_height = self.screen_height // rows
                
                mosaic = np.zeros((self.screen_height, self.screen_width, 3), dtype=np.uint8)
                
                for idx in range(min(len(self.current_cameras), rows*cols)):
                    cam_id = self.current_cameras[idx] if idx < len(self.current_cameras) else None
                    
                    row = idx // cols
                    col = idx % cols
                    y1 = row * cell_height
                    y2 = y1 + cell_height
                    x1 = col * cell_width
                    x2 = x1 + cell_width
                    
                    if cam_id is None:
                        # Cellule vide
                        placeholder = np.zeros((cell_height, cell_width, 3), dtype=np.uint8)
                        cv2.rectangle(placeholder, (2, 2), (cell_width-3, cell_height-3), 
                                     (50, 50, 50), 2)
                        cv2.putText(placeholder, f"Position {idx + 1}", 
                                   (cell_width//2 - 60, cell_height//2), 
                                   cv2.FONT_HERSHEY_SIMPLEX, 
                                   0.8, (80, 80, 80), 2)
                        mosaic[y1:y2, x1:x2] = placeholder
                        continue
                    
                    frame_to_display = None
                    frame_timestamp = None
                    
                    # Récupérer la frame la plus récente
                    if cam_id in self.frame_queues:
                        queue = self.frame_queues[cam_id]
                        
                        try:
                            # Vider la queue jusqu'à la dernière frame
                            last_item = None
                            while True:
                                try:
                                    item = queue.get_nowait()
                                    last_item = item
                                except Empty:
                                    break
                            
                            if last_item is not None:
                                frame, timestamp = last_item
                                
                                # Vérifier l'âge de la frame
                                frame_age = current_time - timestamp
                                
                                if frame_age < self.max_frame_age:
                                    # Frame assez récente
                                    frame_to_display = frame
                                    frame_timestamp = timestamp
                                    self.last_valid_frames[cam_id] = frame
                                    self.last_frame_timestamps[cam_id] = timestamp
                                else:
                                    # Frame trop vieille, utiliser la dernière valide
                                    if cam_id in self.last_valid_frames:
                                        frame_to_display = self.last_valid_frames[cam_id]
                                        frame_timestamp = self.last_frame_timestamps.get(cam_id, 0)
                        except:
                            pass
                    
                    # Si pas de nouvelle frame, utiliser la dernière valide
                    if frame_to_display is None and cam_id in self.last_valid_frames:
                        frame_to_display = self.last_valid_frames[cam_id]
                        frame_timestamp = self.last_frame_timestamps.get(cam_id, 0)
                    
                    # Afficher la frame ou un placeholder
                    if frame_to_display is not None:
                        try:
                            # Si on est en mode mur d'images, cropper la frame
                            if self.video_wall_mode and self.quadrant and self.current_layout == '1x1':
                                frame_to_display = self.get_quadrant_crop(frame_to_display, self.quadrant)
                            
                            border_size = 2
                            target_width = cell_width - 2 * border_size
                            target_height = cell_height - 2 * border_size
                            
                            resized = cv2.resize(frame_to_display, (target_width, target_height))
                            
                            cell_with_border = np.zeros((cell_height, cell_width, 3), dtype=np.uint8)
                            cell_with_border[border_size:cell_height-border_size, 
                                           border_size:cell_width-border_size] = resized
                            
                            mosaic[y1:y2, x1:x2] = cell_with_border
                            
                        except Exception as e:
                            logger.error(f"❌ Erreur affichage caméra {cam_id}: {e}")
                    else:
                        # Placeholder pour caméra en cours de chargement
                        placeholder = np.zeros((cell_height, cell_width, 3), dtype=np.uint8)
                        cv2.rectangle(placeholder, (2, 2), (cell_width-3, cell_height-3), 
                                     (50, 50, 50), 2)
                        
                        cam_info = self.get_camera_info(cam_id)
                        cam_name = clean_text_for_opencv(cam_info['name'])
                        
                        text_size = cv2.getTextSize(cam_name, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)[0]
                        text_x = cell_width - text_size[0] - 10
                        cv2.putText(placeholder, cam_name, 
                                   (text_x, 30), cv2.FONT_HERSHEY_SIMPLEX, 
                                   0.8, (100, 100, 100), 2)
                        
                        cv2.putText(placeholder, "Connexion...", 
                                   (cell_width//2 - 80, cell_height//2), 
                                   cv2.FONT_HERSHEY_SIMPLEX, 
                                   0.8, (100, 100, 100), 2)
                        
                        # Animation de chargement
                        loading_dots = "." * ((int(time.time()) % 4))
                        cv2.putText(placeholder, loading_dots, 
                                   (cell_width//2 + 50, cell_height//2), 
                                   cv2.FONT_HERSHEY_SIMPLEX, 
                                   0.8, (100, 100, 100), 2)
                        
                        mosaic[y1:y2, x1:x2] = placeholder
                
                cv2.imshow(self.window_name, mosaic)
            
            # Gestion des touches
            key = cv2.waitKey(1) & 0xFF
            if key == 27:  # ESC
                logger.info("⌨️ Touche ESC pressée, arrêt...")
                self.running = False
            elif key == ord('r'):  # R pour reset
                logger.info("🔄 Reset des frames en cache")
                self.last_valid_frames.clear()
                self.last_frame_timestamps.clear()
            elif key == ord('f'):  # F pour fullscreen toggle (mode debug)
                if self.debug_mode:
                    current_fullscreen = cv2.getWindowProperty(
                        self.window_name, cv2.WND_PROP_FULLSCREEN)
                    cv2.setWindowProperty(
                        self.window_name, cv2.WND_PROP_FULLSCREEN,
                        cv2.WINDOW_NORMAL if current_fullscreen else cv2.WINDOW_FULLSCREEN)
    
    def cleanup(self):
        """Nettoyage des ressources"""
        logger.info("🧹 Nettoyage des ressources...")
        self.running = False
        self.stop_video_threads()
        time.sleep(1)
        cv2.destroyAllWindows()
        if self.sio.connected:
            self.sio.disconnect()
        
        # Afficher les statistiques finales
        uptime = int(time.time() - self.stats['start_time'])
        logger.info(f"📊 Statistiques finales:")
        logger.info(f"   - Durée d'exécution: {uptime//60} minutes")
        logger.info(f"   - Frames affichées: {self.stats['frames_displayed']}")
        logger.info(f"   - Tentatives de connexion: {self.stats['connection_attempts']}")
        
        logger.info("✅ Client arrêté proprement")

def signal_handler(sig, frame):
    """Gestion propre de l'arrêt"""
    logger.info("\n⚠️ Signal d'arrêt reçu...")
    sys.exit(0)

def main():
    """Point d'entrée principal"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Client Video Wall pour Raspberry Pi',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples:
  %(prog)s                           # Utilise les paramètres par défaut
  %(prog)s --server http://10.0.0.1:1982 --name "Salon"
  %(prog)s --debug --loglevel DEBUG # Mode debug avec logs détaillés
  %(prog)s --config my_config.json  # Utilise un fichier de configuration
        """
    )
    
    parser.add_argument('--server', 
                       help='URL du serveur central (défaut: depuis config ou localhost:1982)')
    parser.add_argument('--name', 
                       help='Nom du Pi (défaut: hostname)')
    parser.add_argument('--debug', action='store_true', 
                       help='Mode debug (fenêtre non plein écran)')
    parser.add_argument('--loglevel', 
                       choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                       help='Niveau de log (défaut: depuis config ou INFO)')
    parser.add_argument('--config', default='pi_client_config.json',
                       help='Fichier de configuration JSON (défaut: pi_client_config.json)')
    parser.add_argument('--version', action='version', version='%(prog)s 1.0')
    
    args = parser.parse_args()
    
    # Charger la configuration
    config = load_config(args.config)
    
    # Surcharger avec les arguments ligne de commande
    if args.server:
        config['SERVER_URL'] = args.server
    if args.loglevel:
        config['LOG_LEVEL'] = args.loglevel
    if args.debug:
        config['DEBUG_MODE'] = True
    
    # Configurer le niveau de log
    numeric_level = getattr(logging, config['LOG_LEVEL'].upper(), None)
    if numeric_level is not None:
        logging.getLogger().setLevel(numeric_level)
    
    # Gestion des signaux
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Forcer l'encodage UTF-8
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    
    # Afficher les informations de démarrage
    logger.info("="*60)
    logger.info("🎥 CLIENT VIDEO WALL POUR RASPBERRY PI")
    logger.info("="*60)
    logger.info(f"📌 Version: 1.0")
    logger.info(f"🐍 Python: {sys.version.split()[0]}")
    logger.info(f"📷 OpenCV: {cv2.__version__}")
    backend_info = cv2.getBuildInformation().split('Video I/O:')[1].split('\n')[0].strip()
    logger.info(f"🎬 Backend: {backend_info}")
    
    # Créer et démarrer le client
    client = PiVideoWall(
        server_url=config['SERVER_URL'],
        pi_name=args.name,
        debug_mode=config['DEBUG_MODE'],
        config=config
    )
    
    try:
        client.start()
    except Exception as e:
        logger.error(f"❌ Erreur fatale: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()