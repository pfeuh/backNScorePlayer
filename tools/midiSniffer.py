#!/usr/bin/python3
# -*- coding: utf-8 -*-

import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import midi

def run_sniffer():
    # 1. Lister les ports d'entrée
    print("--- PORTS MIDI D'ENTRÉE DISPONIBLES ---")
    labels = midi.getMidiinLabels()
    if not labels:
        print("Aucun périphérique MIDI détecté.")
        return

    for i, label in enumerate(labels):
        print(f"[{i}] {label}")

    # 2. Choisir le port
    try:
        choice = input("\nEntrez l'index du port à sniffer (ou 'q' pour quitter) : ")
        if choice.lower() == 'q':
            return
        
        port_index = int(choice)
        
        # 3. Lancer l'espionnage
        # On utilise simple_log=True pour avoir le timestamp, 
        # pratique pour voir si un bouton envoie plusieurs messages d'un coup.
        midi.spyMidiInput(port_index, simple_log=True)
        
    except ValueError:
        print("Veuillez entrer un nombre valide.")
    except IndexError:
        print("Index hors limite.")
    except KeyboardInterrupt:
        print("\nArrêt du sniffer.")

if __name__ == "__main__":
    run_sniffer()