#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import socket
import sys
from urllib.parse import urlparse, unquote

UDP_IP = "127.0.0.1"
UDP_PORT = 9999
LOG_FILE = "/tmp/openMp3With.log"

def log_debug(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{msg}\n")
    except Exception:
        pass

if __name__ == "__main__":
    log_debug(f"Arguments reçus : {sys.argv}")
    if len(sys.argv) > 1:
        raw_path = sys.argv[1]
        
        # Nettoyage des éventuels guillemets simples ou doubles ajoutés par le gestionnaire
        raw_path = raw_path.strip("'\"")

        # Conversion si l'OS passe une URI file://
        if raw_path.startswith("file://"):
            parsed = urlparse(raw_path)
            mp3_path = unquote(parsed.path)
        else:
            mp3_path = raw_path

        log_debug(f"Chemin nettoyé : {mp3_path}")

        # Vérification stricte de l'extension .mp3 (insensible à la casse)
        if mp3_path.lower().endswith(".mp3"):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.sendto(mp3_path.encode('utf-8'), (UDP_IP, UDP_PORT))
                log_debug("Envoyé avec succès en UDP.")
            except Exception as e:
                log_debug(f"Erreur UDP: {e}")
            finally:
                sock.close()
        else:
            log_debug(f"Fichier ignoré (ce n'est pas un MP3) : {mp3_path}")