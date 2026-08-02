#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import socket
import os
import json
import tkinter as tk
from tkinter import filedialog, messagebox

# --- CONFIGURATION ---
UDP_IP = "127.0.0.1"
UDP_PORT = 9999
CONFIG_FILE = "sendMp3_config.json"
DEFAULT_MP3_PATH = "/mnt/Data1/Documents/audio/mp3/bustamento_ackerBilk.mp3"

class Mp3SenderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Sélecteur & Lecteur MP3 (UDP)")
        self.root.geometry("650x200")
        self.root.resizable(False, False)

        self.current_path = tk.StringVar()
        self.load_config()

        self.create_widgets()

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    path = data.get("last_path", DEFAULT_MP3_PATH)
                    if os.path.exists(path):
                        self.current_path.set(path)
                        return
            except Exception:
                pass
        self.current_path.set(DEFAULT_MP3_PATH)

    def save_config(self):
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump({"last_path": self.current_path.get()}, f, indent=4)
        except Exception as e:
            print(f"Erreur lors de la sauvegarde de la config : {e}")

    def browse_file(self):
        initial_dir = os.path.dirname(self.current_path.get()) if os.path.exists(self.current_path.get()) else "/"
        filename = filedialog.askopenfilename(
            title="Sélectionner un fichier MP3",
            initialdir=initial_dir,
            filetypes=[("Fichiers MP3", "*.mp3"), ("Tous les fichiers", "*.*")]
        )
        if filename:
            self.current_path.set(filename)
            self.save_config()

    def send_mp3(self):
        path = self.current_path.get()
        if not path:
            messagebox.showwarning("Attention", "Aucun fichier sélectionné.")
            return

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.sendto(path.encode('utf-8'), (UDP_IP, UDP_PORT))
            self.save_config()
            self.status_label.config(text=f"Envoyé avec succès : {os.path.basename(path)}", fg="green")
        except Exception as e:
            self.status_label.config(text=f"Erreur d'envoi : {e}", fg="red")
        finally:
            sock.close()

    def create_widgets(self):
        # Cadre principal
        main_frame = tk.Frame(self.root, padx=15, pady=15)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Label d'information / Titre
        lbl_title = tk.Label(main_frame, text="Fichier MP3 cible :", font=("Arial", 11, "bold"))
        lbl_title.pack(anchor="w", pady=(0, 5))

        # Champ affichant le chemin du fichier
        path_frame = tk.Frame(main_frame)
        path_frame.pack(fill=tk.X, pady=(0, 10))

        entry_path = tk.Entry(path_frame, textvariable=self.current_path, font=("Arial", 10), state="readonly")
        entry_path.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))

        btn_browse = tk.Button(path_frame, text="Parcourir...", command=self.browse_file, width=12)
        btn_browse.pack(side=tk.RIGHT)

        # Bouton d'envoi UDP
        btn_send = tk.Button(main_frame, text="Jouer sur le Player Audio (UDP)", bg="#4CAF50", fg="white", font=("Arial", 11, "bold"), command=self.send_mp3)
        btn_send.pack(fill=tk.X, pady=(5, 10))

        # Label de statut
        self.status_label = tk.Label(main_frame, text="Prêt", font=("Arial", 9, "italic"), fg="gray")
        self.status_label.pack(anchor="w")

if __name__ == "__main__":
    root = tk.Tk()
    app = Mp3SenderApp(root)
    root.mainloop()