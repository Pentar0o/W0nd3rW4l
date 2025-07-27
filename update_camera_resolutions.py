#!/usr/bin/env python3
"""
Script pour mettre à jour automatiquement les résolutions des caméras
en interrogeant directement les caméras Axis
"""

import json
import sys
import os
import requests
from requests.auth import HTTPDigestAuth
import urllib3
from typing import List, Dict, Optional
import argparse

# Désactiver les warnings SSL pour les tests
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class CameraResolutionUpdater:
    def __init__(self, cameras_file: str = 'cameras.json'):
        self.cameras_file = cameras_file
        self.cameras_data = None
        self.updated_count = 0
        self.failed_cameras = []
    
    def load_cameras(self) -> bool:
        """Charge le fichier de configuration des caméras"""
        try:
            with open(self.cameras_file, 'r', encoding='utf-8') as f:
                self.cameras_data = json.load(f)
            print(f"✅ Fichier {self.cameras_file} chargé avec succès")
            print(f"   {len(self.cameras_data.get('cameras', []))} caméras trouvées")
            return True
        except FileNotFoundError:
            print(f"❌ Fichier {self.cameras_file} non trouvé")
            return False
        except json.JSONDecodeError as e:
            print(f"❌ Erreur de parsing JSON: {e}")
            return False
    
    def save_cameras(self, backup: bool = True) -> bool:
        """Sauvegarde le fichier de configuration des caméras"""
        try:
            # Créer le répertoire cameras s'il n'existe pas
            cameras_dir = os.path.dirname(self.cameras_file)
            if cameras_dir and not os.path.exists(cameras_dir):
                os.makedirs(cameras_dir)
                print(f"📁 Répertoire {cameras_dir} créé")
            
            # Faire une sauvegarde si demandé
            if backup and os.path.exists(self.cameras_file):
                backup_file = f"{self.cameras_file}.backup"
                with open(self.cameras_file, 'r') as f_in:
                    with open(backup_file, 'w') as f_out:
                        f_out.write(f_in.read())
                print(f"📁 Sauvegarde créée: {backup_file}")
            
            # Si on sauvegarde depuis la racine vers cameras/
            if self.cameras_file == 'cameras.json' and os.path.exists('cameras'):
                target_file = 'cameras/cameras.json'
                with open(target_file, 'w', encoding='utf-8') as f:
                    json.dump(self.cameras_data, f, indent=4, ensure_ascii=False)
                print(f"✅ Fichier sauvegardé dans {target_file}")
                # Supprimer l'ancien fichier à la racine
                if os.path.exists('cameras.json'):
                    os.remove('cameras.json')
                    print(f"🗑️  Ancien fichier cameras.json supprimé de la racine")
            else:
                # Sauvegarde normale
                with open(self.cameras_file, 'w', encoding='utf-8') as f:
                    json.dump(self.cameras_data, f, indent=4, ensure_ascii=False)
                print(f"✅ Fichier {self.cameras_file} mis à jour avec succès")
            
            return True
        except Exception as e:
            print(f"❌ Erreur lors de la sauvegarde: {e}")
            return False
    
    def test_camera_connection(self, camera: Dict) -> Optional[str]:
        """Teste la connexion à une caméra et retourne le protocole qui fonctionne"""
        ip = camera.get('ip')
        login = camera.get('login', 'root')
        password = camera.get('password', '')
        
        for protocol in ['http', 'https']:
            url = f"{protocol}://{ip}/axis-cgi/basicdeviceinfo.cgi"
            
            try:
                response = requests.get(
                    url,
                    auth=HTTPDigestAuth(login, password),
                    timeout=5,
                    verify=False
                )
                
                if response.status_code == 200:
                    return protocol
                    
            except Exception:
                continue
        
        return None
    
    def get_camera_resolutions(self, camera: Dict, protocol: str) -> Optional[List[str]]:
        """Récupère les résolutions disponibles d'une caméra"""
        ip = camera.get('ip')
        login = camera.get('login', 'root')
        password = camera.get('password', '')
        
        url = f"{protocol}://{ip}/axis-cgi/param.cgi?action=list&group=Properties.Image.Resolution"
        
        try:
            response = requests.get(
                url,
                auth=HTTPDigestAuth(login, password),
                timeout=10,
                verify=False
            )
            
            if response.status_code == 200:
                content = response.text.strip()
                
                # Parser les résolutions
                if '=' in content and 'Properties.Image.Resolution' in content:
                    res_string = content.split('=', 1)[1]
                    all_resolutions = [r.strip() for r in res_string.split(',')]
                    
                    # Filtrer les résolutions 16:9
                    resolutions_16_9 = []
                    for res in all_resolutions:
                        if 'x' in res:
                            try:
                                w, h = res.split('x')
                                w, h = int(w), int(h)
                                ratio = w / h
                                
                                # Vérifier si c'est du 16:9 (avec une tolérance)
                                if abs(ratio - 16/9) < 0.02:
                                    resolutions_16_9.append(res)
                            except ValueError:
                                continue
                    
                    # Trier par résolution décroissante
                    resolutions_16_9.sort(key=lambda r: int(r.split('x')[0]), reverse=True)
                    
                    return resolutions_16_9
                
        except Exception as e:
            print(f"   ❌ Erreur lors de la récupération: {e}")
        
        return None
    
    def update_camera(self, camera: Dict, index: int) -> bool:
        """Met à jour les résolutions d'une caméra"""
        name = camera.get('name', f"Camera {index+1}")
        ip = camera.get('ip', 'unknown')
        
        print(f"\n📷 Traitement de {name} ({ip})...")
        
        # Tester la connexion
        protocol = self.test_camera_connection(camera)
        if not protocol:
            print(f"   ❌ Impossible de se connecter à la caméra")
            self.failed_cameras.append(name)
            return False
        
        print(f"   ✅ Connexion établie en {protocol.upper()}")
        
        # Récupérer les résolutions
        resolutions = self.get_camera_resolutions(camera, protocol)
        if not resolutions:
            print(f"   ❌ Impossible de récupérer les résolutions")
            self.failed_cameras.append(name)
            return False
        
        print(f"   ✅ {len(resolutions)} résolutions 16:9 trouvées")
        
        # Mettre à jour la caméra
        camera['supported_resolutions'] = resolutions
        
        # Vérifier et ajuster la résolution de stream si nécessaire
        current_res = camera.get('stream_resolution', '')
        if current_res not in resolutions:
            # Choisir une résolution par défaut (640x360 ou la plus basse disponible)
            default_res = '640x360'
            if default_res in resolutions:
                camera['stream_resolution'] = default_res
            elif resolutions:
                camera['stream_resolution'] = resolutions[-1]  # La plus basse
                print(f"   ⚠️  Résolution de stream ajustée à {camera['stream_resolution']}")
        
        print(f"   📝 Résolutions 16:9: {', '.join(resolutions[:3])}{'...' if len(resolutions) > 3 else ''}")
        
        self.updated_count += 1
        return True
    
    def update_all_cameras(self) -> None:
        """Met à jour toutes les caméras"""
        cameras = self.cameras_data.get('cameras', [])
        
        print(f"\n🔄 Mise à jour des résolutions pour {len(cameras)} caméras...")
        print("="*60)
        
        for i, camera in enumerate(cameras):
            self.update_camera(camera, i)
        
        print("\n" + "="*60)
        print("📊 RÉSUMÉ:")
        print(f"   ✅ Caméras mises à jour: {self.updated_count}/{len(cameras)}")
        
        if self.failed_cameras:
            print(f"   ❌ Caméras en échec: {', '.join(self.failed_cameras)}")
        
        if self.updated_count > 0:
            print(f"\n💾 Sauvegarde des modifications...")
            if self.save_cameras():
                print("✅ Mise à jour terminée avec succès!")
            else:
                print("❌ Erreur lors de la sauvegarde")
        else:
            print("\n⚠️  Aucune caméra mise à jour")


def main():
    parser = argparse.ArgumentParser(
        description="Met à jour automatiquement les résolutions des caméras Axis"
    )
    parser.add_argument(
        '--file', '-f',
        default='cameras/cameras.json',
        help='Fichier JSON des caméras (défaut: cameras/cameras.json)'
    )
    parser.add_argument(
        '--no-backup',
        action='store_true',
        help='Ne pas créer de sauvegarde du fichier original'
    )
    
    args = parser.parse_args()
    
    print("🎥 MISE À JOUR AUTOMATIQUE DES RÉSOLUTIONS CAMÉRAS")
    print("="*60)
    
    updater = CameraResolutionUpdater(args.file)
    
    # Charger les caméras
    if not updater.load_cameras():
        sys.exit(1)
    
    # Mettre à jour toutes les caméras
    updater.update_all_cameras()


if __name__ == "__main__":
    main()