#!/usr/bin/env python3
"""
Serveur central optimisé pour la gestion des écrans et caméras
Version avec interface web
"""

from flask import Flask, jsonify, request, render_template
from flask_socketio import SocketIO, emit
import json
import os
from datetime import datetime
import logging
import uuid
from typing import Dict, List, Optional, Tuple
import signal
import sys

# Configuration
CONFIG = {
    'USE_STREAM_PARAMETERS': True,
    'PORT': 1982,
    'HOST': '0.0.0.0',
    'CAMERAS_FILE': 'cameras/cameras.json',
    'SCENES_DIR': 'scenes',
    'SCENES_FILE': 'scenes/scenes.json',
    'LOG_LEVEL': logging.INFO,
    'SCREEN_WIDTH': 420,
    'SCREEN_HEIGHT': 300,
    'POSITION_TOLERANCE': 50
}

# Configuration du logging
logging.basicConfig(
    level=CONFIG['LOG_LEVEL'],
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Application Flask
app = Flask(__name__, template_folder='templates')
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-secret-key')
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# État global
connected_screens: Dict = {}
screen_sid_mapping: Dict = {}
camera_config: List = []
screen_positions: Dict = {}


class ServerManager:
    """Gestionnaire principal du serveur"""
    
    @staticmethod
    def ensure_directories():
        """Crée les répertoires nécessaires"""
        for directory in ['cameras', CONFIG['SCENES_DIR'], 'templates']:
            if not os.path.exists(directory):
                os.makedirs(directory)
                logger.info(f"Répertoire '{directory}' créé")
    
    @staticmethod
    def load_cameras() -> List[Dict]:
        """Charge la configuration des caméras"""
        try:
            with open(CONFIG['CAMERAS_FILE'], 'r') as f:
                data = json.load(f)
                cameras = data.get('cameras', [])
                logger.info(f"Chargé {len(cameras)} caméras")
                return cameras
        except FileNotFoundError:
            logger.error(f"Fichier {CONFIG['CAMERAS_FILE']} non trouvé")
            return []
        except json.JSONDecodeError as e:
            logger.error(f"Erreur parsing JSON: {e}")
            return []
    
    @staticmethod
    def generate_persistent_id(screen_data: Dict) -> str:
        """Génère un ID persistant pour un écran"""
        name = screen_data.get('name', 'unknown')
        ip = screen_data.get('ip', '0.0.0.0').replace('.', '_')
        return f"{name}_{ip}"


class SceneManager:
    """Gestionnaire des scènes"""
    
    @staticmethod
    def load_scenes() -> Dict:
        """Charge les scènes sauvegardées"""
        scenes_file = CONFIG['SCENES_FILE']
        if os.path.exists(scenes_file):
            try:
                with open(scenes_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Erreur chargement des scènes: {e}")
        return {}
    
    @staticmethod
    def save_scenes(scenes: Dict) -> bool:
        """Sauvegarde les scènes"""
        try:
            with open(CONFIG['SCENES_FILE'], 'w', encoding='utf-8') as f:
                json.dump(scenes, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            logger.error(f"Erreur sauvegarde des scènes: {e}")
            return False
    
    @staticmethod
    def get_current_configuration() -> Dict:
        """Récupère la configuration actuelle"""
        config = {'screens': {}, 'screen_positions': {}}
        
        for screen_id, screen_data in connected_screens.items():
            persistent_id = screen_data.get('persistent_id', screen_id)
            config['screens'][persistent_id] = {
                'name': screen_data['name'],
                'layout': screen_data.get('layout', '2x2'),
                'cameras': screen_data.get('cameras', []),
                'ip': screen_data['ip']
            }
        
        return config


class VideoWallDetector:
    """Détecteur de murs d'images"""
    
    @staticmethod
    def detect_groups() -> List[Dict]:
        """Détecte les groupes d'écrans formant un mur d'images"""
        groups = []
        processed_screens = set()
        
        for screen_id, screen_data in connected_screens.items():
            if screen_id in processed_screens:
                continue
            
            # Vérifier si écran en mode 1x1 avec caméra
            if (screen_data.get('layout') == '1x1' and 
                screen_data.get('cameras') and 
                len(screen_data['cameras']) > 0 and
                screen_data['cameras'][0] is not None):
                
                camera_id = screen_data['cameras'][0]
                matching_screens = VideoWallDetector._find_matching_screens(camera_id)
                
                if len(matching_screens) == 4:
                    if VideoWallDetector._check_2x2_formation(matching_screens):
                        group = {
                            'camera_id': camera_id,
                            'screens': VideoWallDetector._assign_quadrants(matching_screens)
                        }
                        groups.append(group)
                        
                        for screen in matching_screens:
                            processed_screens.add(screen['id'])
        
        return groups
    
    @staticmethod
    def _find_matching_screens(camera_id: int) -> List[Dict]:
        """Trouve les écrans avec la même caméra"""
        matching = []
        for screen_id, screen_data in connected_screens.items():
            if (screen_data.get('layout') == '1x1' and 
                screen_data.get('cameras') and 
                len(screen_data['cameras']) > 0 and
                screen_data['cameras'][0] == camera_id):
                
                matching.append({
                    'id': screen_id,
                    'position': screen_positions.get(screen_id, {'x': 0, 'y': 0})
                })
        return matching
    
    @staticmethod
    def _check_2x2_formation(screens: List[Dict]) -> bool:
        """Vérifie si 4 écrans forment une grille 2x2"""
        if len(screens) != 4:
            return False
        
        positions = [(s['position']['x'], s['position']['y']) for s in screens]
        x_values = sorted(set(pos[0] for pos in positions))
        y_values = sorted(set(pos[1] for pos in positions))
        
        if len(x_values) != 2 or len(y_values) != 2:
            return False
        
        # Vérifier alignement avec tolérance
        if abs(x_values[1] - x_values[0] - CONFIG['SCREEN_WIDTH']) > CONFIG['POSITION_TOLERANCE']:
            return False
        if abs(y_values[1] - y_values[0] - CONFIG['SCREEN_HEIGHT']) > CONFIG['POSITION_TOLERANCE']:
            return False
        
        return True
    
    @staticmethod
    def _assign_quadrants(screens: List[Dict]) -> List[Dict]:
        """Assigne les quadrants aux écrans"""
        sorted_screens = sorted(screens, key=lambda s: (s['position']['y'], s['position']['x']))
        quadrants = ['top-left', 'top-right', 'bottom-left', 'bottom-right']
        
        result = []
        for i, screen in enumerate(sorted_screens):
            screen['quadrant'] = quadrants[i] if i < 4 else 'unknown'
            result.append(screen)
        
        return result


class RTSPManager:
    """Gestionnaire des URLs RTSP avec templates flexibles"""
    
    @staticmethod
    def generate_url(camera: Dict, layout: str = '2x2') -> str:
        """
        Génère l'URL RTSP pour une caméra en utilisant son template
        
        Args:
            camera: Dictionnaire contenant les infos de la caméra
            layout: Layout de l'écran (pour adapter la qualité du flux)
            
        Returns:
            URL RTSP complète
        """
        # Récupérer le template RTSP
        rtsp_template = camera.get('rtsp_template')
        
        # Si pas de template, utiliser l'ancien format Axis par défaut
        if not rtsp_template:
            # Compatibilité avec l'ancienne configuration
            base_url = f"rtsp://{camera['login']}:{camera['password']}@{camera['ip']}/axis-media/media.amp"
            if CONFIG['USE_STREAM_PARAMETERS']:
                resolution = RTSPManager._get_resolution_for_layout(camera, layout)
                return f"{base_url}?resolution={resolution}"
            return base_url
        
        # Préparer les variables de substitution
        params = {
            'login': camera.get('login', 'admin'),
            'password': camera.get('password', ''),
            'ip': camera.get('ip', ''),
            'port': camera.get('port', 554),
            'channel': camera.get('channel', 1),
            'stream': camera.get('stream', 1),
            'resolution': camera.get('stream_resolution', '640x480'),
            'fps': camera.get('stream_fps', 15),
            'quality': camera.get('quality', 'main')
        }
        
        # Adapter les paramètres selon le layout si nécessaire
        params = RTSPManager._adapt_params_for_layout(params, camera, layout)
        
        # Remplacer les placeholders dans le template
        try:
            rtsp_url = rtsp_template.format(**params)
            
            # Nettoyer l'URL si pas d'authentification
            # Enlever :@ si login et password sont vides
            if not camera.get('login') and not camera.get('password'):
                rtsp_url = rtsp_url.replace(':@', '')
                rtsp_url = rtsp_url.replace('://@', '://')
            
            return rtsp_url
        except KeyError as e:
            logger.error(f"Placeholder manquant dans le template RTSP: {e}")
            logger.error(f"Template: {rtsp_template}")
            logger.error(f"Paramètres disponibles: {params}")
            # Retourner une URL par défaut en cas d'erreur
            return f"rtsp://{params['login']}:{params['password']}@{params['ip']}/stream"
    
    @staticmethod
    def _adapt_params_for_layout(params: Dict, camera: Dict, layout: str) -> Dict:
        """
        Adapte les paramètres selon le layout de l'écran
        
        Args:
            params: Paramètres de base
            camera: Infos de la caméra
            layout: Layout de l'écran
            
        Returns:
            Paramètres adaptés
        """
        # Si la caméra définit des flux/canaux spécifiques par layout
        layout_config = camera.get('layout_config', {})
        
        if layout in layout_config:
            config = layout_config[layout]
            # Mettre à jour les paramètres avec la config spécifique au layout
            params.update(config)
        else:
            # Logique par défaut basée sur le layout
            if layout == '1x1':
                # Haute qualité pour affichage plein écran
                params['quality'] = camera.get('high_quality', 'main')
                params['stream'] = camera.get('main_stream', 1)
                params['channel'] = camera.get('main_channel', 1)
                # Utiliser la résolution supportée la plus haute si disponible
                if 'supported_resolutions' in camera:
                    supported = camera['supported_resolutions']
                    high_res = ['1920x1080', '1280x720', '960x540']
                    for res in high_res:
                        if res in supported:
                            params['resolution'] = res
                            break
            elif layout in ['2x2', '3x3']:
                # Qualité réduite pour affichages multiples
                params['quality'] = camera.get('low_quality', 'sub')
                params['stream'] = camera.get('sub_stream', 2)
                params['channel'] = camera.get('sub_channel', 2)
                # Adapter la résolution si possible
                if 'sub_resolution' in camera:
                    params['resolution'] = camera['sub_resolution']
                elif 'supported_resolutions' in camera:
                    supported = camera['supported_resolutions']
                    low_res = ['640x480', '640x360', '320x240']
                    for res in low_res:
                        if res in supported:
                            params['resolution'] = res
                            break
        
        return params
    
    @staticmethod
    def _get_resolution_for_layout(camera: Dict, layout: str) -> str:
        """
        Détermine la résolution optimale selon le layout
        
        Règles:
        - 1x1 : 1080p si disponible, sinon la plus haute résolution 16:9
        - 2x2 : 720p si disponible, sinon 540p ou inférieur  
        - 3x3 : 450p si disponible, sinon 360p ou la plus basse
        
        Args:
            camera: Dictionnaire contenant les infos de la caméra
            layout: Layout de l'écran ('1x1', '2x2', '3x3')
            
        Returns:
            Résolution optimale au format WIDTHxHEIGHT
        """
        supported = camera.get('supported_resolutions', [])
        if not supported:
            # Fallback sur la résolution configurée ou une valeur par défaut
            return camera.get('stream_resolution', '640x480')
        
        # Convertir les résolutions en tuples (width, height) pour faciliter le tri
        res_tuples = []
        for res in supported:
            if 'x' in res:
                try:
                    w, h = map(int, res.split('x'))
                    res_tuples.append((w, h, res))
                except ValueError:
                    continue
        
        if not res_tuples:
            return camera.get('stream_resolution', '640x480')
        
        # Trier par hauteur décroissante
        res_tuples.sort(key=lambda x: x[1], reverse=True)
        
        # Logique spécifique selon le layout
        if layout == '1x1':
            # Pour 1x1 : viser 1080p max
            for w, h, res in res_tuples:
                if h <= 1080:
                    logger.debug(f"Layout 1x1: sélection de {res} pour {camera.get('name')}")
                    return res
            # Si aucune <= 1080p, prendre la plus basse disponible
            return res_tuples[-1][2]
            
        elif layout == '2x2':
            # Pour 2x2 : viser 720p max
            for w, h, res in res_tuples:
                if h <= 720:
                    logger.debug(f"Layout 2x2: sélection de {res} pour {camera.get('name')}")
                    return res
            # Si aucune <= 720p, prendre la plus basse disponible
            return res_tuples[-1][2]
            
        else:  # 3x3
            # Pour 3x3 : viser 450p ou moins
            # D'abord chercher spécifiquement 450p
            for w, h, res in res_tuples:
                if h == 450:
                    logger.debug(f"Layout 3x3: sélection de {res} (450p exact) pour {camera.get('name')}")
                    return res
            
            # Sinon chercher <= 450p
            for w, h, res in res_tuples:
                if h <= 450:
                    logger.debug(f"Layout 3x3: sélection de {res} pour {camera.get('name')}")
                    return res
            
            # Si aucune <= 450p, prendre la plus basse disponible
            logger.debug(f"Layout 3x3: aucune résolution <= 450p, sélection de {res_tuples[-1][2]} pour {camera.get('name')}")
            return res_tuples[-1][2]


# Route pour l'interface web
@app.route('/')
def index():
    """Page d'accueil - Interface de contrôle"""
    return render_template('control.html')


# Routes API
@app.route('/api/cameras')
def get_cameras():
    """Liste des caméras"""
    return jsonify(camera_config)


@app.route('/api/screens')
def get_screens():
    """Liste des écrans connectés"""
    return jsonify(connected_screens)


@app.route('/api/screen/<screen_id>/config', methods=['POST'])
def update_screen_config(screen_id):
    """Met à jour la configuration d'un écran"""
    if screen_id not in connected_screens:
        return jsonify({'error': 'Screen not found'}), 404
    
    try:
        data = request.json
        layout = data.get('layout', '1x1')
        cameras = data.get('cameras', [])
        
        # Validation
        if layout not in ['1x1', '2x2', '3x3']:
            return jsonify({'error': 'Invalid layout'}), 400
        
        # Mise à jour
        connected_screens[screen_id]['layout'] = layout
        connected_screens[screen_id]['cameras'] = cameras
        
        # Détection mur d'images
        video_wall_groups = VideoWallDetector.detect_groups()
        
        # Configuration pour cet écran
        config_data = {
            'layout': layout,
            'cameras': cameras,
            'video_wall_mode': False,
            'quadrant': None
        }
        
        # Vérifier si fait partie d'un groupe
        for group in video_wall_groups:
            for screen in group['screens']:
                if screen['id'] == screen_id:
                    config_data['video_wall_mode'] = True
                    config_data['quadrant'] = screen['quadrant']
                    break
        
        # Notifier l'écran
        socketio.emit('config_update', config_data, room=screen_id)
        
        # Notifier les autres écrans du groupe si nécessaire
        if config_data['video_wall_mode']:
            for group in video_wall_groups:
                for screen in group['screens']:
                    if screen['id'] != screen_id:
                        other_config = {
                            'layout': connected_screens[screen['id']]['layout'],
                            'cameras': connected_screens[screen['id']]['cameras'],
                            'video_wall_mode': True,
                            'quadrant': screen['quadrant']
                        }
                        socketio.emit('config_update', other_config, room=screen['id'])
        
        return jsonify({'status': 'success'})
        
    except Exception as e:
        logger.error(f"Erreur configuration écran {screen_id}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/screens/positions', methods=['POST'])
def update_screen_positions():
    """Met à jour les positions des écrans"""
    global screen_positions
    
    try:
        data = request.json
        screen_positions = data.get('positions', {})
        
        # Notifier les écrans concernés par les murs d'images
        video_wall_groups = VideoWallDetector.detect_groups()
        
        for group in video_wall_groups:
            for screen in group['screens']:
                if screen['id'] in connected_screens:
                    config_data = {
                        'layout': connected_screens[screen['id']]['layout'],
                        'cameras': connected_screens[screen['id']]['cameras'],
                        'video_wall_mode': True,
                        'quadrant': screen['quadrant']
                    }
                    socketio.emit('config_update', config_data, room=screen['id'])
        
        return jsonify({'status': 'success', 'groups': len(video_wall_groups)})
        
    except Exception as e:
        logger.error(f"Erreur mise à jour positions: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/rtsp/<int:camera_id>')
def get_rtsp_url(camera_id):
    """Retourne l'URL RTSP d'une caméra"""
    layout = request.args.get('layout', '2x2')
    
    for cam in camera_config:
        if cam['id'] == camera_id:
            url = RTSPManager.generate_url(cam, layout)
            return jsonify({'url': url})
    
    return jsonify({'error': 'Camera not found'}), 404


@app.route('/api/scenes', methods=['GET'])
def get_scenes():
    """Liste des scènes"""
    scenes = SceneManager.load_scenes()
    scenes_list = []
    
    for scene_id, scene_data in scenes.items():
        scene_data['id'] = scene_id
        scenes_list.append(scene_data)
    
    scenes_list.sort(key=lambda x: x.get('modified_at', ''), reverse=True)
    return jsonify(scenes_list)


@app.route('/api/scenes', methods=['POST'])
def create_scene():
    """Crée une nouvelle scène"""
    try:
        data = request.json
        scene_name = data.get('name', 'Nouvelle scène')
        scene_id = str(uuid.uuid4())
        
        current_config = SceneManager.get_current_configuration()
        
        # Gestion des positions
        if 'screen_positions' in data:
            converted_positions = {}
            for sid, pos in data['screen_positions'].items():
                if sid in connected_screens:
                    persistent_id = connected_screens[sid].get('persistent_id', sid)
                    converted_positions[persistent_id] = pos
            current_config['screen_positions'] = converted_positions
        
        scene = {
            'name': scene_name,
            'created_at': datetime.now().isoformat(),
            'modified_at': datetime.now().isoformat(),
            'configuration': current_config,
            'description': data.get('description', '')
        }
        
        scenes = SceneManager.load_scenes()
        scenes[scene_id] = scene
        
        if SceneManager.save_scenes(scenes):
            scene['id'] = scene_id
            return jsonify(scene)
        
        return jsonify({'error': 'Save failed'}), 500
        
    except Exception as e:
        logger.error(f"Erreur création scène: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/scenes/<scene_id>', methods=['PUT'])
def update_scene(scene_id):
    """Met à jour une scène existante"""
    try:
        data = request.json
        scenes = SceneManager.load_scenes()
        
        if scene_id not in scenes:
            return jsonify({'error': 'Scene not found'}), 404
        
        # Mettre à jour les informations
        scenes[scene_id]['name'] = data.get('name', scenes[scene_id]['name'])
        scenes[scene_id]['description'] = data.get('description', scenes[scene_id].get('description', ''))
        scenes[scene_id]['modified_at'] = datetime.now().isoformat()
        
        if SceneManager.save_scenes(scenes):
            return jsonify(scenes[scene_id])
        
        return jsonify({'error': 'Save failed'}), 500
        
    except Exception as e:
        logger.error(f"Erreur mise à jour scène: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/scenes/<scene_id>', methods=['DELETE'])
def delete_scene(scene_id):
    """Supprime une scène"""
    try:
        scenes = SceneManager.load_scenes()
        
        if scene_id not in scenes:
            return jsonify({'error': 'Scene not found'}), 404
        
        del scenes[scene_id]
        
        if SceneManager.save_scenes(scenes):
            return jsonify({'status': 'success'})
        
        return jsonify({'error': 'Save failed'}), 500
        
    except Exception as e:
        logger.error(f"Erreur suppression scène: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/scenes/<scene_id>/apply', methods=['POST'])
def apply_scene(scene_id):
    """Applique une scène"""
    try:
        scenes = SceneManager.load_scenes()
        
        if scene_id not in scenes:
            return jsonify({'error': 'Scene not found'}), 404
        
        scene = scenes[scene_id]
        config = scene['configuration']
        
        applied_count = 0
        screen_mapping = {}
        
        # Appliquer la configuration
        for saved_id, screen_config in config['screens'].items():
            for current_id, current_screen in connected_screens.items():
                # Matching par persistent_id ou nom
                if (current_screen.get('persistent_id') == saved_id or 
                    current_screen['name'] == screen_config['name']):
                    
                    connected_screens[current_id]['layout'] = screen_config['layout']
                    connected_screens[current_id]['cameras'] = screen_config['cameras']
                    screen_mapping[saved_id] = current_id
                    applied_count += 1
                    
                    # Notifier l'écran
                    socketio.emit('config_update', {
                        'layout': screen_config['layout'],
                        'cameras': screen_config['cameras']
                    }, room=current_id)
                    break
        
        # Convertir les positions
        converted_positions = {}
        if 'screen_positions' in config:
            for saved_id, pos in config['screen_positions'].items():
                if saved_id in screen_mapping:
                    converted_positions[screen_mapping[saved_id]] = pos
        
        return jsonify({
            'status': 'success',
            'applied_screens': applied_count,
            'screen_positions': converted_positions
        })
        
    except Exception as e:
        logger.error(f"Erreur application scène: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/status')
def get_status():
    """Statut du système"""
    return jsonify({
        'screens_connected': len(connected_screens),
        'cameras_configured': len(camera_config),
        'use_stream_parameters': CONFIG['USE_STREAM_PARAMETERS'],
        'uptime': datetime.now().isoformat()
    })


# Événements SocketIO
@socketio.on('register_screen')
def handle_screen_registration(data):
    """Enregistre un nouvel écran"""
    try:
        screen_id = request.sid
        persistent_id = ServerManager.generate_persistent_id(data)
        
        screen_info = {
            'id': screen_id,
            'persistent_id': persistent_id,
            'ip': data.get('ip', 'unknown'),
            'name': data.get('name', f"Wall-{len(connected_screens)+1}"),
            'last_seen': datetime.now().isoformat(),
            'layout': '2x2',
            'cameras': [],
            'position': data.get('position')
        }
        
        connected_screens[screen_id] = screen_info
        screen_sid_mapping[persistent_id] = screen_id
        
        logger.info(f"Écran enregistré: {screen_info['name']} ({screen_info['ip']})")
        
        emit('screens_updated', list(connected_screens.values()), broadcast=True)
        emit('config_update', {
            'layout': screen_info['layout'],
            'cameras': screen_info['cameras']
        })
        
    except Exception as e:
        logger.error(f"Erreur enregistrement écran: {e}")


@socketio.on('disconnect')
def handle_disconnect():
    """Gère la déconnexion d'un écran"""
    screen_id = request.sid
    if screen_id in connected_screens:
        screen_name = connected_screens[screen_id]['name']
        persistent_id = connected_screens[screen_id].get('persistent_id')
        
        if persistent_id in screen_sid_mapping:
            del screen_sid_mapping[persistent_id]
        
        del connected_screens[screen_id]
        logger.info(f"Écran déconnecté: {screen_name}")
        
        emit('screens_updated', list(connected_screens.values()), broadcast=True)


@socketio.on('heartbeat')
def handle_heartbeat():
    """Maintient la connexion active"""
    screen_id = request.sid
    if screen_id in connected_screens:
        connected_screens[screen_id]['last_seen'] = datetime.now().isoformat()


def signal_handler(sig, frame):
    """Gestion propre de l'arrêt"""
    logger.info("Arrêt du serveur...")
    sys.exit(0)


def main():
    """Point d'entrée principal"""
    # Gestion des signaux
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Initialisation
    ServerManager.ensure_directories()
    
    # Charger les caméras
    global camera_config
    camera_config = ServerManager.load_cameras()
    
    if not camera_config:
        logger.warning("Aucune caméra configurée")
    
    # Démarrer le serveur
    logger.info(f"Démarrage du serveur sur {CONFIG['HOST']}:{CONFIG['PORT']}")
    logger.info(f"Interface web disponible sur http://localhost:{CONFIG['PORT']}")
    
    socketio.run(
        app, 
        host=CONFIG['HOST'], 
        port=CONFIG['PORT'], 
        debug=False, 
        use_reloader=False
    )


if __name__ == '__main__':
    main()