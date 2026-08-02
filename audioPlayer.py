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
from tendo import singleton
from midi import MIDI_IN, getMidiinLabels, CONTROL_CHANGE

# --- CONFIGURATION ---
SERVER_URL = "http://127.0.0.1:8000/sync_check"
DB_DIR = "/mnt/Data1/Documents/backNScoreData/database"
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
        self.learning_mode = False
        self.pending_action = None
        self.last_cc_seen = None
        
        # État interne
        self.locators = {"a": 0, "b": 0, "c": 0, "d": 0}
        self.is_muted = False

    def udp_listener(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 9999))
        while True:
            try:
                data, _ = sock.recvfrom(1024)
                message = data.decode('utf-8').strip()
                
                # Si le message est un chemin de fichier ou un identifiant valide, on joue le média directement
                mp3_path = self.find_mp3(message)
                if mp3_path:
                    print(f"Lecture demandée via UDP : {mp3_path}")
                    self.player.set_media(self.instance.media_new(mp3_path))
                    self.player.play()
                else:
                    # Sinon, on traite comme une action standard (play, stop, etc.)
                    self.execute_action(message, 127)
            except Exception as e:
                print(f"Erreur UDP: {e}")

    def load_midi_config(self):
        if os.path.exists(MIDI_CONFIG_FILE):
            with open(MIDI_CONFIG_FILE, 'r') as f:
                self.midi_config = json.load(f)
            return True
        return False

    def save_midi_config(self):
        with open(MIDI_CONFIG_FILE, 'w') as f:
            json.dump(self.midi_config, f, indent=4)

    def midi_callback(self, channel, control, value, timestamp):
        if self.learning_mode and self.pending_action:

            # On ignore les répétitions du même CC
            if control == self.last_cc_seen:
                return

            self.last_cc_seen = control

            self.midi_config[self.pending_action] = control

            self.pending_action = None

        else:
            for action, mapped_control in self.midi_config.items():
                if control == int(mapped_control):
                    self.execute_action(action, value)

    def execute_action(self, action, value):
        # --- ACTIONS CONTINUES (Sliders / Potards) ---
        if action == "volume":
            vlc_vol = int((value / 127.0) * 100)

            self.player.audio_set_mute(False)
            self.player.audio_set_volume(vlc_vol)
            return

        # --- ACTIONS BINAIRES (Boutons - On ignore le relâchement value=0) ---
        if value == 0: return

        elif action == "play":
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

        # --- GESTION DES LOCATORS ---
        elif action.startswith("set_"):
            key = action.split("_")[1]
            self.locators[key] = self.player.get_time()
            print(f"Locator {key.upper()} mémorisé à {self.locators[key]/1000:.1f}s")

        elif action.startswith("goto_"):
            key = action.split("_")[1]
            self.player.set_time(self.locators.get(key, 0))
            print(f"Saut vers Locator {key.upper()}")

    def run_config_wizard(self):
        print("\n--- MODE CONFIGURATION MIDI (LEARN) ---")
        labels = getMidiinLabels()
        for i, l in enumerate(labels): print(f"[{i}] {l}")
        
        try:
            idx = int(input("\nChoisissez l'index du port MIDI : "))
            self.midi_in.open(idx)
        except (ValueError, IndexError):
            print("Index invalide.")
            sys.exit(1)

        self.midi_in.callbacks[CONTROL_CHANGE] = self.midi_callback
        self.learning_mode = True

        actions = [
            "volume", "mute", "play", "stop", "goto_start", 
            "rewind", "ff", "set_a", "goto_a", "set_b", "goto_b", 
            "set_c", "goto_c", "set_d", "goto_d"
        ]

        for action in actions:
            self.pending_action = action
            # On définit le message selon le type d'action
            if action in ["volume", "balance"]:
                instruction = "BOUGEZ le curseur ou potard"
            else:
                instruction = "APPUYEZ sur le bouton"
            
            print(f"\n[{instruction}] pour l'action : {action.upper()}")
            
            # Attente de l'entrée MIDI via le callback
            while self.pending_action is not None:
                time.sleep(0.1)
        
        self.save_midi_config()
        print("\nConfiguration terminée avec succès. Relancez sans l'option -config.")
        sys.exit(0)

    def setup_midi_runtime(self):
        if not self.load_midi_config():
            print("Erreur: Aucun fichier config. Lancez avec -config")
            sys.exit(1)
        
        labels = getMidiinLabels()
        found = False
        for i, l in enumerate(labels):
            if any(name in l.lower() for name in ["nano", "genos"]):
                self.midi_in.open(i)
                self.midi_in.callbacks[CONTROL_CHANGE] = self.midi_callback
                print(f"MIDI connecté à : {l}")
                found = True
                break
        if not found:
            print("Contrôleur MIDI non trouvé.")

    def find_mp3(self, loc):
        if os.path.isfile(loc):
            if loc.endswith("mp3"):
                return loc
            else:
                return None
        loc = loc.lstrip('/')
        track_path = os.path.join(DB_DIR, loc)
        for filename in ["backtrack.mp3", "melody.mp3"]:
            full_path = os.path.join(track_path, filename)
            if os.path.exists(full_path):
                return full_path
        # pas de mp3 trouvé
        
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
                        mp3_path = self.find_mp3(new_loc)
                        if mp3_path != None:
                            self.player.set_media(self.instance.media_new(mp3_path))
                        else:
                            self.player.set_media(self.instance.media_new("./404_vocal_msg.mp3"))
                        self.player.play()
            except Exception as e:
                # On évite de polluer la console si le serveur est juste éteint temporairement
                pass
            time.sleep(2)

if __name__ == "__main__":
    
    try:
        me = singleton.SingleInstance()
    except singleton.SingleInstanceException:
        print(f"Erreur : Une autre instance {sys.argv[0]} est déjà en cours d'exécution.")
        sys.exit(1)

    # on créé la classe audioPlayer
    audio_client = AudioPlayerClient()

    if "-config" in sys.argv:
        audio_client.run_config_wizard()
    else:
        audio_client.setup_midi_runtime()
        # Thread 1 : Surveillance du changement de morceau (Polling 2s)
        thread = threading.Thread(target=audio_client.main_loop, daemon=True)
        thread.start()
        
        # Thread 2 : Commandes Web Instantanées (UDP)
        thread_udp = threading.Thread(target=audio_client.udp_listener, daemon=True)
        thread_udp.start()
        
        try:
            # Boucle principale pour garder le script actif et réactif au MIDI
            while True: 
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nArrêt propre de l'audio player.")