#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import socket
import sys

# Configuration
UDP_IP = "127.0.0.1"
UDP_PORT = 9999
MP3_PATH = "/mnt/Data1/Documents/audio/mp3/bustamento_ackerBilk.mp3"

def send_mp3_path(path):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Envoi du chemin du fichier au playerAudio.py
        sock.sendto(path.encode('utf-8'), (UDP_IP, UDP_PORT))
        print(f"Demande envoyée avec succès pour : {path}")
    except Exception as e:
        print(f"Erreur lors de l'envoi UDP : {e}")
    finally:
        sock.close()

if __name__ == "__main__":
    # Permet aussi de passer un autre fichier en argument si besoin
    target_path = sys.argv[1] if len(sys.argv) > 1 else MP3_PATH
    send_mp3_path(target_path)