#!/usr/init/python
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

# --- CHARGEMENT DE LA CONFIGURATION DE BASE ---
CONFIG_FILE = "config.json"

def load_config():
    config = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
        except Exception as e:
            print(f"Erreur lecture {CONFIG_FILE}: {e}")
            
    # Détection automatique du dossier parent backnscore si absent
    if not config.get("bns_dir"):
        parent_dir = os.path.abspath(os.path.join(os.getcwd(), ".."))
        for d in os.listdir(parent_dir):
            if "backnscore" in d.lower():
                config["bns_dir"] = os.path.join(parent_dir, d)
                break
        if not config.get("bns_dir"):
            config["bns_dir"] = parent_dir

    return config

config = load_config()

SERVER_URL = config.get("server_url", "http://127.0.0.1:8000/sync_check")
BNS_DIR = config.get("bns_dir", "")

print(f"[INIT] Dossier BNS détecté : {BNS_DIR}")

MIDI_CONFIG_FILE = "midi_config.json"
AUDIO_CONFIG_FILE = "audio_config.json"
AUDIO_SPLASH_FILE = "audioSplash.mp3"
FF_RW_MSEC = 2000

def get_current_db_dir():
    """Récupère dynamiquement le chemin de la base de données depuis le config.json de backnscore."""
    if BNS_DIR:
        bns_config_path = os.path.join(BNS_DIR, "config.json")
        if os.path.exists(bns_config_path):
            try:
                with open(bns_config_path, 'r', encoding='utf-8') as f:
                    bns_cfg = json.load(f)
                    if "DATABASE" in bns_cfg:
                        return bns_cfg["DATABASE"]
            except Exception:
                pass
    # Fallback sur la config locale ou une valeur par défaut
    return config.get("db_dir", "/mnt/usbkey/database")

def is_target_browser(props):
    if not isinstance(props, dict):
        return False
    browsers = ["firefox", "chrome", "chromium", "brave", "edge", "opera", "vivaldi"]
    app_name = str(props.get("application.name", "") or "").lower()
    node_name = str(props.get("node.name", "") or "").lower()
    binary = str(props.get("process.binary", "") or "").lower()
    return any(b in app_name or b in node_name or b in binary for b in browsers)

class AudioPlayerClient:
    def __init__(self):
        self.instance = vlc.Instance('--no-video', '--quiet', '--aout=pulse')
        self.player = self.instance.media_player_new()
        self.player.audio_set_volume(100)  # Volume VLC forcé à 100%
        self.current_loc = None
        self.current_db_track_path = None
        self.current_mp3_path = None
        self.midi_config = {}
        self.midi_in = MIDI_IN()
        self.midi_out = MIDI_OUT()
        self.midi_connected = False
        self.learning_mode = False
        self.pending_action = None
        self.last_cc_seen = None
        
        self.locators = {"a": 0, "b": 0, "c": 0, "d": 0}
        self.is_muted = False
        self.is_system_muted = False
        self.is_browser_muted = False
        self.is_looping = False
        self.current_rate = 1.0
        self.speed_mode_active = False
        self.last_pot_value = 127
        
        self.target_system_volume = None
        self.volume_thread_running = False
        self.target_browser_volume = None
        self.browser_volume_thread_running = False
        self.target_speed_value = None
        self.speed_thread_running = False
        self.server_error_logged = False
        self._lock = threading.Lock()
        self.muted_node_ids = set()

    def udp_listener(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 9999))
        print("Écouteur UDP démarré sur 127.0.0.1:9999")
        while True:
            try:
                data, addr = sock.recvfrom(1024)
                message = data.decode('utf-8').strip()
                
                if message == "get_current_track":
                    response_msg = self.current_mp3_path if self.current_mp3_path else ""
                    sock.sendto(response_msg.encode('utf-8'), addr)
                    continue

                print(f"[UDP Reçu de {addr}] : {message}")
                
                mp3_path = self.find_mp3(message)
                if mp3_path:
                    print(f"-> Fichier MP3 trouvé et validé : {mp3_path}")
                    self.locators = {"a": 0, "b": 0, "c": 0, "d": 0}
                    self.clear_all_tags_leds()
                    self.disable_loop()
                    
                    if self.current_mp3_path:
                        self.load_track_data()
                    else:
                        self.reset_speed()
                    
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
        default_config = {
            "system_volume": 7, "volume": 6, "firefox_volume": 5, "firefox_mute": 53,
            "play": 41, "stop": 42, "rewind": 43, "ff": 44,
            "set_a": 32, "set_b": 33, "set_c": 34, "set_d": 35,
            "goto_a": 64, "goto_b": 65, "goto_c": 66, "goto_d": 67,
            "cycle": 46, "speed_pot": 22, "speed_toggle": 38,
            "volume_mute": 54, "system_mute": 55
        }
        if os.path.exists(MIDI_CONFIG_FILE):
            try:
                with open(MIDI_CONFIG_FILE, 'r', encoding='utf-8') as f:
                    self.midi_config = json.load(f)
                return True
            except Exception:
                pass
        self.midi_config = default_config
        return True

    def save_midi_config(self):
        with open(MIDI_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.midi_config, f, indent=4)

    def set_led(self, control_or_action, state):
        if not self.midi_connected:
            return
        cc = self.midi_config.get(control_or_action) if isinstance(control_or_action, str) else control_or_action
        if cc is not None:
            try:
                self.midi_out.control_change(int(cc), 127 if state else 0, channel=0)
            except Exception:
                pass

    def clear_all_tags_leds(self):
        for key in ["a", "b", "c", "d"]:
            self.set_led(f"goto_{key}", False)
        self.set_led("cycle", False)
        self.set_led("speed_toggle", False)
        self.speed_mode_active = False

    def disable_loop(self):
        self.is_looping = False
        self.set_led("cycle", False)

    def reset_speed(self):
        self.current_rate = 1.0
        self.player.set_rate(self.current_rate)

    def get_track_data_file(self):
        if self.current_db_track_path and os.path.exists(self.current_db_track_path):
            return os.path.join(self.current_db_track_path, "track_config.json")
        elif self.current_mp3_path:
            mp3_dir = os.path.dirname(self.current_mp3_path)
            base_name = os.path.splitext(os.path.basename(self.current_mp3_path))[0]
            return os.path.join(mp3_dir, f"{base_name}_track_config.json")
        return None

    def save_track_data(self):
        data_file = self.get_track_data_file()
        if data_file:
            try:
                with open(data_file, 'w', encoding='utf-8') as f:
                    json.dump({"locators": self.locators}, f, indent=4)
            except Exception as e:
                print(f"Erreur sauvegarde données piste: {e}")

    def load_track_data(self):
        data_file = self.get_track_data_file()
        if data_file and os.path.exists(data_file):
            try:
                with open(data_file, 'r', encoding='utf-8') as f:
                    loaded_data = json.load(f)
                loaded_locators = loaded_data.get("locators", loaded_data) if isinstance(loaded_data, dict) else loaded_data
                for key in ["a", "b", "c", "d"]:
                    if key in loaded_locators:
                        self.locators[key] = loaded_locators[key]
                        if self.locators[key] > 0:
                            self.set_led(f"goto_{key}", True)
            except Exception as e:
                print(f"Erreur lecture données piste: {e}")
        self.reset_speed()

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
                subprocess.run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{current_target / 127.0}"], check=False)
            time.sleep(0.03)

    def set_firefox_volume(self, value):
        with self._lock:
            self.target_browser_volume = value
        if not self.browser_volume_thread_running:
            self.browser_volume_thread_running = True
            threading.Thread(target=self._process_browser_volume_queue, daemon=True).start()

    def _process_browser_volume_queue(self):
        last_applied = -1
        while True:
            with self._lock:
                current_target = self.target_browser_volume
            if current_target == last_applied:
                with self._lock:
                    if self.target_browser_volume == last_applied:
                        self.browser_volume_thread_running = False
                        break
            if current_target is not None:
                last_applied = current_target
                try:
                    result = subprocess.run(["pw-dump"], capture_output=True, text=True, check=True)
                    for node in json.loads(result.stdout):
                        if node.get("type") == "PipeWire:Interface:Node":
                            if is_target_browser(node.get("info", {}).get("props", {})):
                                subprocess.run(["wpctl", "set-volume", str(node.get("id")), f"{current_target / 127.0}"], check=False)
                except Exception:
                    pass
            time.sleep(0.03)

    def toggle_browser_mute(self, mute_state):
        try:
            result = subprocess.run(["pw-dump"], capture_output=True, text=True, check=True)
            for node in json.loads(result.stdout):
                if node.get("type") == "PipeWire:Interface:Node":
                    if is_target_browser(node.get("info", {}).get("props", {})):
                        subprocess.run(["wpctl", "set-mute", str(node.get("id")), "1" if mute_state else "0"], check=False)
        except Exception:
            pass

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
                self.target_speed_value = None
            if current_target is None:
                self.speed_thread_running = False
                break
            self.current_rate = round(0.5 + (current_target / 127.0) * 0.5, 2)
            self.player.set_rate(self.current_rate)
            time.sleep(0.01)

    def toggle_system_mute(self, mute_state):
        try:
            result = subprocess.run(["pw-dump"], capture_output=True, text=True, check=True)
            for node in json.loads(result.stdout):
                if node.get("type") == "PipeWire:Interface:Node":
                    props = node.get("info", {}).get("props", {})
                    if "Stream" in str(props.get("media.class", "")) and "Audio" in str(props.get("media.class", "")):
                        node_id = str(node.get("id"))
                        if not is_target_browser(props):
                            if mute_state:
                                subprocess.run(["wpctl", "set-mute", node_id, "1"], check=False)
                                self.muted_node_ids.add(node_id)
                            elif node_id in self.muted_node_ids:
                                subprocess.run(["wpctl", "set-mute", node_id, "0"], check=False)
            if not mute_state:
                self.muted_node_ids.clear()
        except Exception:
            pass

    def cleanup_on_exit(self):
        if self.muted_node_ids:
            for node_id in list(self.muted_node_ids):
                subprocess.run(["wpctl", "set-mute", node_id, "0"], check=False)
            self.muted_node_ids.clear()

    def midi_callback(self, channel, control, value, timestamp):
        for action, mapped_control in self.midi_config.items():
            if control == int(mapped_control):
                if value == 0 and action not in ["volume", "system_volume", "firefox_volume", "speed_pot", "speed_toggle", "volume_mute", "system_mute", "firefox_mute"]:
                    return
                self.execute_action(action, value)
                break

    def execute_action(self, action, value):
        if action == "volume":
            self.player.audio_set_mute(False)
            self.player.audio_set_volume(int((value / 127.0) * 100))
            return
        elif action == "system_volume":
            self.set_system_volume(value)
            return
        elif action == "firefox_volume":
            self.set_firefox_volume(value)
            return
        elif action == "volume_mute" or action == "mute":
            if value > 0:
                self.is_muted = not self.is_muted
                self.player.audio_set_mute(self.is_muted)
            return
        elif action == "firefox_mute":
            if value > 0:
                self.is_browser_muted = not self.is_browser_muted
                self.toggle_browser_mute(self.is_browser_muted)
            return
        elif action == "system_mute":
            if value > 0:
                self.is_system_muted = not self.is_system_muted
                self.toggle_system_mute(self.is_system_muted)
            return
        elif action == "speed_toggle":
            if value > 0:
                self.speed_mode_active = not self.speed_mode_active
                if self.speed_mode_active:
                    self.set_speed_value(self.last_pot_value)
                else:
                    self.reset_speed()
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
            else:
                self.disable_loop()
        elif action.startswith("set_"):
            key = action.split("_")[1]
            self.locators[key] = self.player.get_time()
            self.set_led(f"goto_{key}", True)
            self.save_track_data()
        elif action.startswith("goto_"):
            key = action.split("_")[1]
            self.player.set_time(self.locators.get(key, 0))

    def setup_midi_runtime(self):
        if not self.load_midi_config():
            return
        for i, l in enumerate(getMidiinLabels()):
            if any(name in l.lower() for name in ["nanokontrol", "genos", "nano"]):
                try:
                    self.midi_in.open(i)
                    self.midi_in.callbacks[CONTROL_CHANGE] = self.midi_callback
                    self.midi_in.callbacks[NOTE_ON] = self.midi_callback
                except Exception:
                    pass
                break
        for i, l in enumerate(getMidioutLabels()):
            if any(name in l.lower() for name in ["nanokontrol", "genos", "nano"]):
                try:
                    self.midi_out.open(i)
                    self.midi_connected = True
                except Exception:
                    pass
                break

    def find_mp3(self, loc):
        self.current_db_track_path = None
        self.current_mp3_path = None

        # RÉCUPÉRATION DYNAMIQUE À CHAQUE RECHERCHE (Gère l'arrachement à chaud de la clé)
        current_db_dir = get_current_db_dir()

        if not current_db_dir or not os.path.exists(current_db_dir):
            print(f"[ERREUR DB] La base de données est introuvable (clé arrachée ?) : {current_db_dir}")
            return None

        if os.path.isfile(loc):
            if loc.lower().endswith("mp3"):
                self.current_mp3_path = loc
                return loc
            return None
        
        loc = loc.lstrip('/')
        track_path = os.path.join(current_db_dir, loc)
        
        if os.path.isdir(track_path):
            self.current_db_track_path = track_path
            mp3_types = []
            if BNS_DIR and os.path.exists(os.path.join(BNS_DIR, "server_data", "mp3_types.json")):
                try:
                    with open(os.path.join(BNS_DIR, "server_data", "mp3_types.json"), 'r', encoding='utf-8') as f:
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
        else:
            print(f"[ERREUR DB] Le dossier cible n'existe pas : {track_path}")
                
        return None

    def loop_checker_daemon(self):
        while True:
            if self.is_looping and self.player.get_state() == vlc.State.Playing:
                if self.player.get_time() >= self.locators["b"]:
                    self.player.set_time(self.locators["a"])
            time.sleep(0.02)

    def main_loop(self):
        print("Audio Player démarré (Polling HTTP actif - Dynamique)")
        while True:
            try:
                response = requests.get(SERVER_URL, timeout=5)
                if response.status_code == 200:
                    self.server_error_logged = False 
                    new_loc = response.text.strip()
                    if new_loc != self.current_loc:
                        print(f"-> Polling HTTP détecte un changement de morceau : {new_loc}")
                        self.current_loc = new_loc 
                        
                        self.locators = {"a": 0, "b": 0, "c": 0, "d": 0}
                        self.clear_all_tags_leds()
                        self.disable_loop()
                        
                        mp3_path = self.find_mp3(new_loc)
                        
                        if mp3_path:
                            print(f"-> Fichier MP3 validé : {mp3_path}")
                            self.load_track_data()
                            self.player.stop()
                            media = self.instance.media_new(mp3_path)
                            self.player.set_media(media)
                            self.player.play()
                            self.player.set_rate(self.current_rate)
                        else:
                            print(f"-> ÉCHEC : Impossible de trouver un fichier MP3 valide pour '{new_loc}'.")
            except requests.exceptions.ConnectionError:
                if not self.server_error_logged:
                    print(f"Serveur injoignable ({SERVER_URL}). En attente...")
                    self.server_error_logged = True
            except Exception as e:
                print(f"Erreur boucle principale: {e}")
            time.sleep(1.0)

if __name__ == "__main__":
    try:
        me = singleton.SingleInstance()
    except singleton.SingleInstanceException:
        print(f"Erreur : Une autre instance est déjà en cours d'exécution.")
        sys.exit(1)

    # Force le volume système à 100% au démarrage
    subprocess.run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "1.0"], check=False)

    audio_client = AudioPlayerClient()
    atexit.register(audio_client.cleanup_on_exit)

    if "-config" not in sys.argv:
        audio_client.setup_midi_runtime()
        
        threading.Thread(target=audio_client.main_loop, daemon=True).start()
        threading.Thread(target=audio_client.udp_listener, daemon=True).start()
        threading.Thread(target=audio_client.loop_checker_daemon, daemon=True).start()
        
        try:
            while True: 
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nArrêt propre.")