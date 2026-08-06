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
        self.current_db_track_path = None
        self.current_mp3_path = None  # Chemin complet du MP3 en cours (pour la persistance par piste)
        self.midi_config = {}
        self.midi_in = MIDI_IN()
        self.midi_out = MIDI_OUT()
        self.learning_mode = False
        self.pending_action = None
        self.last_cc_seen = None
        
        # État interne & anti-rebond MIDI
        self.locators = {"a": 0, "b": 0, "c": 0, "d": 0}
        self.is_muted = False
        self.is_looping = False
        self.current_rate = 1.0
        
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
                    if os.path.isfile(message):
                        print(f"-> Chargement via Pop-up Système (Fichier direct) : {mp3_path}")
                        self.current_db_track_path = None
                        self.current_mp3_path = mp3_path
                    else:
                        print(f"-> Chargement via UDP (Répertoire Database) : {mp3_path}")

                    self.locators = {"a": 0, "b": 0, "c": 0, "d": 0}
                    self.clear_all_tags_leds()
                    self.disable_loop()
                    self.reset_speed()
                    
                    if self.current_db_track_path and self.current_mp3_path:
                        self.load_tags_for_current_track()
                    
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
            "set_d": 67,
            "cycle": 46,
            "speed_up": 58,
            "speed_down": 59,
            "speed_reset": 60
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

    def disable_loop(self):
        self.is_looping = False
        self.set_led("cycle", False)

    def reset_speed(self):
        self.current_rate = 1.0
        self.player.set_rate(self.current_rate)

    # --- GESTION DE LA PERSISTANCE DES TAGS PAR FICHIER MP3 ---
    def save_tags_for_current_track(self):
        if self.current_mp3_path:
            # Ex: /path/to/track/melody.mp3 -> /path/to/track/melody.json
            base_name, _ = os.path.splitext(self.current_mp3_path)
            tags_file = f"{base_name}.json"
            try:
                with open(tags_file, 'w', encoding='utf-8') as f:
                    json.dump(self.locators, f, indent=4)
                print(f"Tags sauvegardés dans : {tags_file}")
            except Exception as e:
                print(f"Erreur sauvegarde tags: {e}")

    def load_tags_for_current_track(self):
        if self.current_mp3_path:
            base_name, _ = os.path.splitext(self.current_mp3_path)
            tags_file = f"{base_name}.json"
            if os.path.exists(tags_file):
                try:
                    with open(tags_file, 'r', encoding='utf-8') as f:
                        loaded_tags = json.load(f)
                        for key in ["a", "b", "c", "d"]:
                            if key in loaded_tags:
                                self.locators[key] = loaded_tags[key]
                                if self.locators[key] > 0:
                                    self.set_led(f"goto_{key}", True)
                    print(f"Tags chargés depuis : {tags_file}")
                except Exception as e:
                    print(f"Erreur lecture tags: {e}")

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

    def toggle_system_mute(self):
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
                self.player.set_rate(self.current_rate)
        
        elif action == "stop":
            self.player.stop()
            self.disable_loop()
            self.reset_speed()
            
        elif action == "mute":
            self.is_muted = not self.is_muted
            self.player.audio_set_mute(self.is_muted)

        elif action == "goto_start":
            self.player.set_time(0)

        elif action == "ff":
            self.player.set_time(self.player.get_time() + FF_RW_MSEC)
            
        elif action == "rewind":
            self.player.set_time(max(0, self.player.get_time() - FF_RW_MSEC))

        elif action == "speed_up":
            self.current_rate = round(min(2.0, self.current_rate + 0.05), 2)
            self.player.set_rate(self.current_rate)
            print(f"Vitesse de lecture : {int(self.current_rate * 100)}%")

        elif action == "speed_down":
            self.current_rate = round(max(0.3, self.current_rate - 0.05), 2)
            self.player.set_rate(self.current_rate)
            print(f"Vitesse de lecture : {int(self.current_rate * 100)}%")

        elif action == "speed_reset":
            self.reset_speed()
            print("Vitesse de lecture réinitialisée à 100%")

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
            self.save_tags_for_current_track()

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
                            self.load_tags_for_current_track()

                        if mp3_path != None:
                            self.player.set_media(self.instance.media_new(mp3_path))
                        else:
                            self.player.set_media(self.instance.media_new("./404_vocal_msg.mp3"))
                        self.player.play()
            except Exception as e:
                pass
            time.sleep(0.05)

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