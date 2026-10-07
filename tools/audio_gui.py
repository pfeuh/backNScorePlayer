import tkinter as tk
import socket
import os

# Configuration UDP
UDP_IP = "127.0.0.1"
PLAYER_UDP_PORT = 9999

def send_command(action):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(action.encode('utf-8'), (UDP_IP, PLAYER_UDP_PORT))
        status_var.set(f"Envoyé : {action}")
        status_label.config(fg="#4CAF50")
    except Exception as e:
        status_var.set(f"Erreur : {e}")
        status_label.config(fg="#ff4d4d")

# Création de la fenêtre principale
root = tk.Tk()
root.title("🎛️ AudioPlayer Transport — (Aucun morceau)")
root.geometry("460x520")
root.configure(bg="#1a1a1a")
root.resizable(False, False)

# Couleurs de style
BG_DARK = "#2a2a2a"
BTN_BG = "#3b3b3b"
BTN_FG = "#ffffff"
BTN_ACTIVE = "#505050"
COLOR_PLAY = "#007acc"
COLOR_STOP = "#cc3b3b"

def poll_current_track():
    """Interroge le player via UDP pour récupérer le titre du morceau en cours"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0.2)
        sock.sendto(b"get_current_track", (UDP_IP, PLAYER_UDP_PORT))
        data, _ = sock.recvfrom(1024)
        track_path = data.decode('utf-8').strip()
        if track_path:
            base_name = os.path.basename(track_path)
            root.title(f"🎛️ AudioPlayer Transport — {base_name}")
        else:
            root.title("🎛️ AudioPlayer Transport — (Aucun morceau)")
        sock.close()
    except Exception:
        pass
    
    # Relance la vérification dans 1 seconde (1000 ms)
    root.after(1000, poll_current_track)

def create_section(parent, title, buttons):
    frame = tk.LabelFrame(parent, text=title, fg="#ffcc00", bg=BG_DARK, font=("Arial", 10, "bold"), bd=1, relief="solid")
    frame.pack(fill="x", padx=10, pady=8, ipadx=5, ipady=5)
    
    for idx, (text, action, bg_color) in enumerate(buttons):
        row = idx // 4
        col = idx % 4
        bg = bg_color if bg_color else BTN_BG
        
        btn = tk.Button(
            frame, text=text, command=lambda a=action: send_command(a),
            bg=bg, fg=BTN_FG, activebackground=BTN_ACTIVE, activeforeground=BTN_FG,
            relief="flat", font=("Arial", 9, "bold"), width=10, pady=6
        )
        btn.grid(row=row, column=col, padx=4, pady=4, sticky="ew")
        frame.columnconfigure(col, weight=1)

# --- SECTIONS DE LA GUI ---
transport_btns = [
    ("Play / Pause", "play", COLOR_PLAY),
    ("Stop", "stop", COLOR_STOP),
    ("Début", "goto_start", None),
    ("Rew (-2s)", "rewind", None),
    ("FF (+2s)", "ff", None)
]
create_section(root, "Transport Principal", transport_btns)

locator_btns = [
    ("Cycle A-B", "cycle", None),
    ("Goto A", "goto_a", None),
    ("Goto B", "goto_b", None),
    ("Goto C", "goto_c", None),
    ("Goto D", "goto_d", None)
]
create_section(root, "Boucles & Repères", locator_btns)

set_btns = [
    ("Set A", "set_a", None),
    ("Set B", "set_b", None),
    ("Set C", "set_c", None),
    ("Set D", "set_d", None)
]
create_section(root, "Mémorisation des Tags", set_btns)

mute_btns = [
    ("Mute VLC", "volume_mute", None),
    ("Mute Browser", "firefox_mute", None),
    ("Mute Sys (M8)", "system_mute", None),
    ("Speed Mode", "speed_toggle", None)
]
create_section(root, "Mutes & Options", mute_btns)

# --- BARRE DE STATUT ---
status_var = tk.StringVar(value="Prêt")
status_label = tk.Label(root, textvariable=status_var, bg="#1a1a1a", fg="#4CAF50", font=("Arial", 9))
status_label.pack(side="bottom", pady=10)

# Démarrage de l'interrogation UDP du morceau
root.after(1000, poll_current_track)

root.mainloop()