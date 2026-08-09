#!/usr/bin/python
# -*- coding: utf-8 -*-

import requests
import time
import vlc
import os
import sys
import json
import threading
import socket
import subprocess
import atexit
from tendo import singleton
from midi import MIDI_IN, MIDI_OUT, getMidiinLabels, getMidioutLabels, CONTROL_CHANGE, NOTE_ON

# --- CHARGEMENT DE LA CONFIGURATION (config.json) ---
CONFIG_FILE = "config.json"

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Erreur lecture {CONFIG_FILE}: {e}")
    return {}

config = load_config()

SERVER_URL = config.get("server_url", "http://127.0.0.1:8000/sync_check")
DB_DIR = config.get("db_dir", "/mnt/Data1/Documents/backNScoreData/database")
BNS_DIR = config.get("bns_dir", "")

MIDI_CONFIG_FILE = "midi_config.json"
AUDIO_CONFIG_FILE = "audio_config.json"
AUDIO_SPLASH_FILE = "audioSplash.mp3"
FF_RW_MSEC = 2000

class AudioPlayerClient:
    def __init__(self):
        self.instance = vlc.Instance('--no-video', '--quiet')
        self.player = self.instance.media_player_new()
        self.current_loc = None
        self.current_db_track_path = None
        self.current_mp3_path = None  # Chemin complet du MP3 en cours
        self.midi_config = {}
        self.midi_in = MIDI_IN()
        self.midi_out = MIDI_OUT()
        self.learning_mode = False
        self.pending_action = None
        self.last_cc_seen = None
        
        # État interne & anti-rebond MIDI
        self.locators = {"a": 0, "b": 0, "c": 0, "d": 0}
        self.is_muted = False
        self.is_system_muted = False
        self.is_looping = False
        self.current_rate = 1.0
        self.speed_mode_active = False  # État du mode édition de vitesse (bouton S)
        self.last_pot_value = 127        # Mémorisation de la dernière position physique du potard (défault = max/100%)
        
        # Variables pour la gestion fluide et sans perte du volume système et de la vitesse
        self.target_system_volume = None
        self.volume_thread_running = False
        
        # Système intelligent de gestion de la vitesse (tendance / anti-latence)
        self.target_speed_value = None
        self.speed_thread_running = False
        self.server_error_logged = False # Anti-spam pour l'absence de serveur
        self._lock = threading.Lock()
        
        # Sécurité anti-effet de bord : mémorisation des IDs mutés pour pouvoir les démuter à la sortie
        self.muted_node_ids = set()

    def udp_listener(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 9999))
        print("Écouteur UDP démarré sur 127.0.0.1:9999")
        while True:
            try:
                data, addr = sock.recvfrom(1024)
                message = data.decode('utf-8').strip()
                print(f"[UDP Reçu de {addr}] : {message}")
                
                mp3_path = self.find_mp3(message)
                if mp3_path:
                    if os.path.isfile(message):
                        print(f"-> Chargement via Pop-up Système (Fichier direct) : {mp3_path}")
                    else:
                        print(f"-> Chargement via UDP (Répertoire Database) : {mp3_path}")

                    self.locators = {"a": 0, "b": 0, "c": 0, "d": 0}
                    self.clear_all_tags_leds()
                    self.disable_loop()
                    self.reset_speed()
                    
                    if self.current_db_track_path and self.current_mp3_path:
                        self.load_track_data()
                    
                    self.player.stop()
                    media = self.instance.media_new(mp3_path)
                    self.player.set_media(media)
                    self.player.play()
                    self.player.set_rate(self.current_rate)
                else:
                    self.execute_action(message, 127)
            except Exception as e:
                print(f"Erreur UDP: {e}")

    def load_midi_config(self):
        if os.path.exists(MIDI_CONFIG_FILE):
            try:
                with open(MIDI_CONFIG_FILE, 'r', encoding='utf-8') as f:
                    self.midi_config = json.load(f)
                return True
            except Exception as e:
                print(f"Erreur lecture {MIDI_CONFIG_FILE}: {e}")
        
        print(f"Fichier {MIDI_CONFIG_FILE} introuvable. Création d'une configuration par défaut...")
        self.midi_config = {
            "system_volume": 7,
            "volume": 6,
            "play": 41,
            "stop": 42,
            "rewind": 43,
            "ff": 44,
            "set_a": 32,
            "set_b": 33,
            "set_c": 34,
            "set_d": 35,
            "goto_a": 64,
            "goto_b": 65,
            "goto_c": 66,
            "goto_d": 67,
            "cycle": 46,
            "speed_pot": 22,
            "speed_toggle": 38,
            "volume_mute": 54,
            "system_mute": 55
        }
        self.save_midi_config()
        return True

    def save_midi_config(self):
        with open(MIDI_CONFIG_FILE, 'w') as f:
            json.dump(self.midi_config, f, indent=4)

    def set_led(self, control_or_action, state):
        cc = None
        if isinstance(control_or_action, int):
            cc = control_or_action
        elif control_or_action in self.midi_config:
            cc = int(self.midi_config[control_or_action])
        
        if cc is not None:
            val = 127 if state else 0
            try:
                self.midi_out.control_change(cc, val, channel=0)
            except Exception as e:
                print(f"Erreur envoi LED CC {cc}: {e}")

    def clear_all_tags_leds(self):
        for key in ["a", "b", "c", "d"]:
            action = f"goto_{key}"
            if action in self.midi_config:
                self.set_led(action, False)
        if "cycle" in self.midi_config:
            self.set_led("cycle", False)
        if "speed_toggle" in self.midi_config:
            self.set_led("speed_toggle", False)
        if "volume_mute" in self.midi_config:
            self.set_led("volume_mute", self.is_muted)
        if "system_mute" in self.midi_config:
            self.set_led("system_mute", self.is_system_muted)
        self.speed_mode_active = False

    def disable_loop(self):
        self.is_looping = False
        self.set_led("cycle", False)

    def reset_speed(self):
        self.current_rate = 1.0
        self.player.set_rate(self.current_rate)
        self.save_track_data()

    # --- GESTION DE LA PERSISTANCE (UNIQUEMENT DANS LA DATABASE) ---
    def get_track_data_file(self):
        """Renvoie le chemin du fichier JSON uniquement si le MP3 est dans la database.
           Sinon, retourne None pour ne rien créer à l'extérieur."""
        if self.current_db_track_path and os.path.exists(self.current_db_track_path):
            return os.path.join(self.current_db_track_path, "track_config.json")
        return None

    def save_track_data(self):
        data_file = self.get_track_data_file()
        if data_file:
            try:
                data_to_save = {
                    "locators": self.locators,
                    "speed": self.current_rate
                }
                with open(data_file, 'w', encoding='utf-8') as f:
                    json.dump(data_to_save, f, indent=4)
                print(f"Données de piste sauvegardées dans la database : {data_file}")
            except Exception as e:
                print(f"Erreur sauvegarde données piste: {e}")

    def load_track_data(self):
        data_file = self.get_track_data_file()
        if data_file and os.path.exists(data_file):
            try:
                with open(data_file, 'r', encoding='utf-8') as f:
                    loaded_data = json.load(f)
                    
                    if isinstance(loaded_data, dict) and "locators" in loaded_data:
                        loaded_locators = loaded_data["locators"]
                        self.current_rate = loaded_data.get("speed", 1.0)
                    else:
                        loaded_locators = loaded_data
                        self.current_rate = 1.0

                    for key in ["a", "b", "c", "d"]:
                        if key in loaded_locators:
                            self.locators[key] = loaded_locators[key]
                            if self.locators[key] > 0:
                                self.set_led(f"goto_{key}", True)
                    
                    self.player.set_rate(self.current_rate)
                    print(f"Données de piste chargées depuis la database (Vitesse: {int(self.current_rate * 100)}%)")
            except Exception as e:
                print(f"Erreur lecture données piste: {e}")

    # --- FONCTIONS DE CONTRÔLE VOLUME PC ---
    def set_system_volume(self, value):
        with self._lock:
            self.target_system_volume = value

        if not self.volume_thread_running:
            self.volume_thread_running = True
            threading.Thread(target=self._process_system_volume_queue, daemon=True).start()

    def _process_system_volume_queue(self):
        last_applied = -1
        while True:
            with self._lock:
                current_target = self.target_system_volume

            if current_target == last_applied:
                with self._lock:
                    if self.target_system_volume == last_applied:
                        self.volume_thread_running = False
                        break

            if current_target is not None:
                last_applied = current_target
                vol = current_target / 127.0
                subprocess.run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{vol}"], check=False)

            time.sleep(0.03)

    # --- GESTION INTELLIGENTE DE LA VITESSE (0 = -50% / 127 = 100%) ---
    def set_speed_value(self, value):
        with self._lock:
            self.target_speed_value = value

        if not self.speed_thread_running:
            self.speed_thread_running = True
            threading.Thread(target=self._process_speed_queue, daemon=True).start()

    def _process_speed_queue(self):
        while True:
            with self._lock:
                current_target = self.target_speed_value
                self.target_speed_value = None  # Consommé

            if current_target is None:
                self.speed_thread_running = False
                break

            # Plage : 0 -> 0.5 (-50%), 127 -> 1.0 (100% / Normal)
            self.current_rate = round(0.5 + (current_target / 127.0) * 0.5, 2)
            
            self.player.set_rate(self.current_rate)
            print(f"Vitesse ajustée via potard : {int(self.current_rate * 100)}%")
            self.save_track_data()

            time.sleep(0.01)

    def toggle_system_mute(self, mute_state):
        """Coupe ou rétablit tous les flux audio sauf VLC pour éviter les bruits parasites."""
        try:
            result = subprocess.run(["pw-dump"], capture_output=True, text=True, check=True)
            nodes = json.loads(result.stdout)
            
            for node in nodes:
                if node.get("type") == "PipeWire:Interface:Node":
                    props = node.get("info", {}).get("props", {})
                    media_class = props.get("media.class", "")
                    
                    if "Stream" in media_class and "Audio" in media_class:
                        app_name = props.get("application.name", "").lower()
                        node_id = str(node.get("id"))
                        
                        # On épargne VLC
                        if "vlc" not in app_name:
                            if mute_state:
                                subprocess.run(["wpctl", "set-mute", node_id, "1"], check=False)
                                self.muted_node_ids.add(node_id)
                            else:
                                if node_id in self.muted_node_ids:
                                    subprocess.run(["wpctl", "set-mute", node_id, "0"], check=False)
            
            if not mute_state:
                self.muted_node_ids.clear()
                
        except Exception as e:
            print(f"Erreur lors de la gestion des flux tiers (mute/unmute) : {e}")

    def cleanup_on_exit(self):
        if self.muted_node_ids:
            print("\n[Sécurité] Restauration du son des applications tierces...")
            for node_id in list(self.muted_node_ids):
                subprocess.run(["wpctl", "set-mute", node_id, "0"], check=False)
            self.muted_node_ids.clear()

    def midi_callback(self, channel, control, value, timestamp):
        for action, mapped_control in self.midi_config.items():
            if control == int(mapped_control):
                if value == 0 and action not in ["volume", "system_volume", "speed_pot", "speed_toggle", "volume_mute", "system_mute"]:
                    return
                self.execute_action(action, value)
                break

    def execute_action(self, action, value):
        if action == "volume":
            vlc_vol = int((value / 127.0) * 100)
            self.player.audio_set_mute(False)
            self.player.audio_set_volume(vlc_vol)
            return

        elif action == "system_volume":
            self.set_system_volume(value)
            return

        elif action == "volume_mute" or action == "mute":
            if value > 0:  
                self.is_muted = not self.is_muted
                self.player.audio_set_mute(self.is_muted)
                self.set_led("volume_mute", self.is_muted)
                print(f"Lecteur VLC Mute : {'ACTIVÉ' if self.is_muted else 'DÉSACTIVÉ'}")
            return

        elif action == "system_mute":
            if value > 0:  
                self.is_system_muted = not self.is_system_muted
                self.toggle_system_mute(self.is_system_muted)
                self.set_led("system_mute", self.is_system_muted)
                print(f"Volume général Mute : {'ACTIVÉ' if self.is_system_muted else 'DÉSACTIVÉ'}")
            return

        elif action == "speed_toggle":
            if value > 0:  
                self.speed_mode_active = not self.speed_mode_active
                self.set_led("speed_toggle", self.speed_mode_active)
                print(f"Mode Vitesse : {'ACTIVÉ' if self.speed_mode_active else 'DÉSACTIVÉ'}")
                
                if self.speed_mode_active:
                    self.set_speed_value(self.last_pot_value)
                else:
                    self.reset_speed()
                    print("Vitesse réinitialisée à 100% suite à la désactivation du mode S.")
            return

        elif action == "speed_pot":
            self.last_pot_value = value
            if self.speed_mode_active:
                self.set_speed_value(value)
            return

        if action == "play":
            state = self.player.get_state()
            if state == vlc.State.Playing:
                self.player.pause()
            elif state == vlc.State.Ended:
                self.player.stop()
                self.player.play()
                self.player.set_rate(self.current_rate)
            else:
                self.player.play()
                self.player.set_rate(self.current_rate)
        
        elif action == "stop":
            self.player.stop()
            self.disable_loop()
            self.reset_speed()
            
        elif action == "goto_start":
            self.player.set_time(0)

        elif action == "ff":
            self.player.set_time(self.player.get_time() + FF_RW_MSEC)
            
        elif action == "rewind":
            self.player.set_time(max(0, self.player.get_time() - FF_RW_MSEC))

        elif action == "cycle":
            if self.locators["a"] < self.locators["b"]:
                self.is_looping = not self.is_looping
                self.set_led("cycle", self.is_looping)
                print(f"Boucle A-B : {'ACTIVÉE' if self.is_looping else 'DÉSACTIVÉE'}")
            else:
                print("Impossible d'activer la boucle : Tag A et Tag B invalides (A doit être < B).")
                self.disable_loop()

        elif action.startswith("set_"):
            key = action.split("_")[1]
            self.locators[key] = self.player.get_time()
            print(f"Tag {key.upper()} mémorisé à {self.locators[key]/1000:.1f}s")
            self.set_led(f"goto_{key}", True)
            self.save_track_data()

        elif action.startswith("goto_"):
            key = action.split("_")[1]
            self.player.set_time(self.locators.get(key, 0))
            print(f"Saut vers Tag {key.upper()}")

    def setup_midi_runtime(self):
        if not self.load_midi_config():
            print("Erreur: Aucun fichier config MIDI.")
            sys.exit(1)
        
        labels_in = getMidiinLabels()
        found_in = False
        for i, l in enumerate(labels_in):
            if any(name in l.lower() for name in ["nanokontrol", "genos", "nano"]):
                self.midi_in.open(i)
                self.midi_in.callbacks[CONTROL_CHANGE] = self.midi_callback
                self.midi_in.callbacks[NOTE_ON] = self.midi_callback
                print(f"MIDI Entrée connecté à : {l}")
                found_in = True
                break
        if not found_in:
            print("Contrôleur MIDI Entrée non trouvé.")

        labels_out = getMidioutLabels()
        found_out = False
        for i, l in enumerate(labels_out):
            if any(name in l.lower() for name in ["nanokontrol", "genos", "nano"]):
                self.midi_out.open(i)
                print(f"MIDI Sortie connecté à : {l}")
                found_out = True
                break
        if not found_out:
            print("Contrôleur MIDI Sortie non trouvé (les LED ne s'allumeront pas).")
        else:
            try:
                korg_global_sysex = [
                    0x42, 0x40, 0x00, 0x01, 0x03, 0x00, 0x42, 
                    0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 
                    0x00, 0x00, 0x00, 0x00, 0x01
                ]
                self.midi_out.sysex(korg_global_sysex)
                print("Trame SysEx de configuration LED External envoyée au nanoKONTROL.")
            except Exception as e:
                print(f"Impossible d'envoyer le SysEx LED: {e}")

    def find_mp3(self, loc):
        self.current_db_track_path = None
        self.current_mp3_path = None

        if os.path.isfile(loc):
            if loc.lower().endswith("mp3"):
                self.current_mp3_path = loc
                return loc
            else:
                return None
        
        loc = loc.lstrip('/')
        track_path = os.path.join(DB_DIR, loc)
        
        if os.path.isdir(track_path):
            self.current_db_track_path = track_path

            mp3_types = []
            if BNS_DIR:
                types_file = os.path.join(BNS_DIR, "server_data", "mp3_types.json")
                if os.path.exists(types_file):
                    try:
                        with open(types_file, 'r', encoding='utf-8') as f:
                            mp3_types = json.load(f)
                    except Exception:
                        pass

            if not mp3_types:
                mp3_types = ["noPiano", "demo", "noSecond", "melody", "backtrack", "noWind", "noDrum", "noBass"]

            for t in mp3_types:
                filename = t if t.lower().endswith(".mp3") else f"{t}.mp3"
                full_path = os.path.join(track_path, filename)
                if os.path.exists(full_path):
                    self.current_mp3_path = full_path
                    return full_path

            try:
                for f in os.listdir(track_path):
                    if f.lower().endswith(".mp3"):
                        full_path = os.path.join(track_path, f)
                        self.current_mp3_path = full_path
                        return full_path
            except Exception:
                pass
                
        return None

    def main_loop(self):
        print("Audio Player démarré")
        while True:
            try:
                if self.is_looping and self.player.get_state() == vlc.State.Playing:
                    current_time = self.player.get_time()
                    tag_b = self.locators["b"]
                    tag_a = self.locators["a"]
                    if current_time >= tag_b:
                        self.player.set_time(tag_a)

                response = requests.get(SERVER_URL, timeout=5)
                if response.status_code == 200:
                    self.server_error_logged = False 
                    new_loc = response.text.strip()
                    if new_loc != self.current_loc:
                        print(f"-> Chargement via Database (Polling HTTP) : {new_loc}")
                        self.current_loc = new_loc 
                        
                        self.locators = {"a": 0, "b": 0, "c": 0, "d": 0}
                        self.clear_all_tags_leds()
                        self.disable_loop()
                        self.reset_speed()
                        
                        mp3_path = self.find_mp3(new_loc)
                        
                        if self.current_db_track_path and self.current_mp3_path:
                            self.load_track_data()

                        self.player.stop()
                        if mp3_path != None:
                            media = self.instance.media_new(mp3_path)
                            self.player.set_media(media)
                        else:
                            media = self.instance.media_new("./404_vocal_msg.mp3")
                            self.player.set_media(media)
                        self.player.play()
                        self.player.set_rate(self.current_rate)
            except requests.exceptions.ConnectionError:
                if not self.server_error_logged:
                    print(f"Serveur injoignable ({SERVER_URL}). Le polling HTTP est en attente...")
                    self.server_error_logged = True
            except Exception as e:
                print(f"Erreur dans la boucle principale: {e}")
            time.sleep(1.0)

if __name__ == "__main__":
    try:
        me = singleton.SingleInstance()
    except singleton.SingleInstanceException:
        print(f"Erreur : Une autre instance {sys.argv[0]} est déjà en cours d'exécution.")
        sys.exit(1)

    audio_client = AudioPlayerClient()

    atexit.register(audio_client.cleanup_on_exit)

    if "-config" in sys.argv:
        audio_client.run_config_wizard()
    else:
        audio_client.setup_midi_runtime()
        thread = threading.Thread(target=audio_client.main_loop, daemon=True)
        thread.start()
        
        thread_udp = threading.Thread(target=audio_client.udp_listener, daemon=True)
        thread_udp.start()
        
        try:
            while True: 
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nArrêt propre de l'audio player.")