# Back'n Score - Audio Player


Player de mp3 associé à Back'n Score pour jouer les démo et autres playbacks 

## 🚀 Fonctionnalités

* **Serveur de synchronisation Flask** : Gestion des pistes et des chemins de lecture.
* **Lecteur audio robuste (`playerAudio.py`)** :
  * Utilisation de **python-vlc** pour une lecture fluide.
  * Support du contrôle **MIDI** (contrôleurs type Korg NanoKontrol, Yamaha Genos, etc.) avec assistant de configuration interactif (`-config`).
  * Écouteur **UDP** intégré pour recevoir des commandes et des chemins de fichiers instantanés.
  * Gestion des boucles et des repères (Locators `a`, `b`, `c`, `d`).
* **Sélecteur graphique (`sendMp3.py`)** : Interface Tkinter pour choisir et envoyer un MP3 à distance au lecteur.
* **Intégration Ubuntu (`openMp3With.py`)** : Intégration dans le menu contextuel "Ouvrir avec" de l'explorateur de fichiers pour lancer un MP3 directement dans le player via un clic droit.

---

## 🛠️ Installation & Prérequis

### 1. Cloner le dépôt
```bash
git clone [https://github.com/ton-vrai-pseudo/backNScorePlayer.git](https://github.com/ton-vrai-pseudo/backNScorePlayer.git)
cd backNScorePlayer
