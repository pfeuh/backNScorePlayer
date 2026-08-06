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
        self.midi_config = {}
        self.midi_in = MIDI_IN()
        self.midi_out = MIDI_OUT()
        self.learning_mode = False
        self.pending_action = None
        self.last_cc_seen = None
        
        # État interne & anti-rebond MIDI
        self.locators = {"a": 0, "b": 0, "c": 0, "d": 0}
        self.is_muted = False
        
        # Variables pour la gestion fluide et sans perte du volume système
        self.target_system_volume = None
        self.volume_thread_running = False
        self._lock = threading.Lock()

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
                    print(f"Lecture du média trouvé : {mp3_path}")
                    self.player.set_media(self.instance.media_new(mp3_path))
                    self.player.play()
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
        
        # Si le fichier n'existe pas ou est corrompu, on crée une configuration par défaut
        print(f"Fichier {MIDI_CONFIG_FILE} introuvable. Création d'une configuration par défaut...")
        self.midi_config = {
            "system_volume": 7,
            "play": 41,
            "stop": 42,
            "mute": 43,
            "volume": 14,
            "goto_a": 48,
            "goto_b": 49,
            "goto_c": 50,
            "goto_d": 51,
            "set_a": 64,
            "set_b": 65,
            "set_c": 66,
            "set_d": 67
        }
        self.save_midi_config()
        return True

    def save_midi_config(self):
        with open(MIDI_CONFIG_FILE, 'w') as f:
            json.dump(self.midi_config, f, indent=4)

    def set_led(self, control_or_action, state):
        """Allume (127) ou éteint (0) la LED associée via Control Change (mode externe)"""
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
        """Éteint les LED de tous les boutons R (goto)"""
        for key in ["a", "b", "c", "d"]:
            action = f"goto_{key}"
            if action in self.midi_config:
                self.set_led(action, False)

    # --- FONCTIONS DE CONTRÔLE VOLUME PC (wpctl avec Worker fluide sans perte) ---
    def set_system_volume(self, value):
        """Met à jour la cible du volume et s'assure qu'un thread dédié applique la dernière valeur sans sauter le 127"""
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

            # Si on a atteint la dernière valeur demandée et qu'il n'y a plus de mouvement, on met en pause le thread
            if current_target == last_applied:
                with self._lock:
                    # Double check si rien n'a bougé entre temps
                    if self.target_system_volume == last_applied:
                        self.volume_thread_running = False
                        break

            if current_target is not None:
                last_applied = current_target
                vol = current_target / 127.0
                subprocess.run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{vol}"], check=False)

            # Petit délai pour cadencer proprement les appels système sans engorger wpctl
            time.sleep(0.03)

    def toggle_system_mute(self):
        """Bascule l'état Mute de la carte son principale du PC"""
        subprocess.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"], check=False)

    def midi_callback(self, channel, control, value, timestamp):
        for action, mapped_control in self.midi_config.items():
            if control == int(mapped_control):
                if value == 0 and action not in ["volume", "system_volume"]:
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

        elif action == "system_mute":
            self.toggle_system_mute()
            return

        if action == "play":
            state = self.player.get_state()
            if state == vlc.State.Playing:
                self.player.pause()
            elif state == vlc.State.Ended:
                self.player.stop()
                self.player.play()
            else:
                self.player.play()
        
        elif action == "stop":
            self.player.stop()
            
        elif action == "mute":
            self.is_muted = not self.is_muted
            self.player.audio_set_mute(self.is_muted)

        elif action == "goto_start":
            self.player.set_time(0)

        elif action == "ff":
            self.player.set_time(self.player.get_time() + FF_RW_MSEC)
            
        elif action == "rewind":
            self.player.set_time(max(0, self.player.get_time() - FF_RW_MSEC))

        # --- GESTION DES TAGS / LOCATORS ---
        elif action.startswith("set_"):
            key = action.split("_")[1]
            self.locators[key] = self.player.get_time()
            print(f"Tag {key.upper()} mémorisé à {self.locators[key]/1000:.1f}s")
            self.set_led(f"goto_{key}", True)

        elif action.startswith("goto_"):
            key = action.split("_")[1]
            self.player.set_time(self.locators.get(key, 0))
            print(f"Saut vers Tag {key.upper()}")

    def setup_midi_runtime(self):
        if not self.load_midi_config():
            print("Erreur: Aucun fichier config MIDI.")
            sys.exit(1)
        
        # Connexion Entrée MIDI
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

        # Connexion Sortie MIDI (pour piloter les LED)
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
        if os.path.isfile(loc):
            if loc.lower().endswith("mp3"):
                return loc
            else:
                return None
        loc = loc.lstrip('/')
        track_path = os.path.join(DB_DIR, loc)
        
        if os.path.isdir(track_path):
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
                    return full_path

            try:
                for f in os.listdir(track_path):
                    if f.lower().endswith(".mp3"):
                        return os.path.join(track_path, f)
            except Exception:
                pass
                
        return None

    def main_loop(self):
        print("Audio Player démarré")
        while True:
            try:
                response = requests.get(SERVER_URL, timeout=5)
                if response.status_code == 200:
                    new_loc = response.text.strip()
                    if new_loc != self.current_loc:
                        print(new_loc)
                        self.current_loc = new_loc 
                        
                        self.clear_all_tags_leds()
                        
                        mp3_path = self.find_mp3(new_loc)
                        if mp3_path != None:
                            self.player.set_media(self.instance.media_new(mp3_path))
                        else:
                            self.player.set_media(self.instance.media_new("./404_vocal_msg.mp3"))
                        self.player.play()
            except Exception as e:
                pass
            time.sleep(2)

if __name__ == "__main__":
    try:
        me = singleton.SingleInstance()
    except singleton.SingleInstanceException:
        print(f"Erreur : Une autre instance {sys.argv[0]} est déjà en cours d'exécution.")
        sys.exit(1)

    audio_client = AudioPlayerClient()

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