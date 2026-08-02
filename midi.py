#!/usr/bin/python3
# -*- coding: utf-8 -*-

import rtmidi

# === Statuts des messages MIDI de canal ===
NOTE_OFF           = 0x80
NOTE_ON            = 0x90
POLY_AFTERTOUCH    = 0xA0
CONTROL_CHANGE     = 0xB0
PROGRAM_CHANGE     = 0xC0
CHANNEL_AFTERTOUCH = 0xD0
PITCH_BEND         = 0xE0

# === Messages système ===
MIDI_TIME_CODE_QF         = 0xF1
SONG_POSITION_POINTER     = 0xF2
SONG_SELECT               = 0xF3
TUNE_REQUEST              = 0xF6
START_OF_SYSEX            = 0xF0
END_OF_SYSEX              = 0xF7
TIMING_CLOCK              = 0xF8
START                     = 0xFA
CONTINUE                  = 0xFB
STOP                      = 0xFC
ACTIVE_SENSING            = 0xFE
SYSTEM_RESET              = 0xFF

_current_midiout_labels = []
_current_midiin_labels = []

def getMidioutLabels():
    """Retourne la liste des libellés des ports de sortie MIDI disponibles sous forme de liste de chaînes.
    Filtre les ports fantômes 'RtMidiOut Client'.
    """
    global _current_midiout_labels
    midi = rtmidi.MidiOut()
    # on récupère tous les ports
    labels = midi.get_ports()
    del midi  # détruit l'instance pour éviter la prolifération de clients fantômes
    # on filtre les ports virtuels RtMidiOut Client
    #~ _current_midiout_labels = [label for label in labels if not label.startswith("RtMidiOut Client")]
    _current_midiout_labels = [label for label in labels]
    return _current_midiout_labels


def getMidioutLabelTexts():
    """Retourne les ports de sortie MIDI disponibles sous forme d'une chaîne multi-lignes.
    avec index et label sur chaque ligne. cet index est demandé par self.open """
    return "\n".join(f"{i:2d} {label}" for i, label in enumerate(getMidioutLabels()))

def getMidioutLabelFromIndex(index):
    """Retourne le libellé d'un port de sortie MIDI par son index."""
    global _current_midiout_labels
    # recharge si nécessaire
    if not _current_midiout_labels:
        getMidioutLabels()
    return _current_midiout_labels[index]

def getIndexFromMidioutLabel(label):
    """Retourne l'index d'un port de sortie MIDI par son label
    (ignore l'ID final qui peut changer si on branche/débranche
    des sortie midi sur le PC si introuvable retourne None."""
    global _current_midiout_labels
    if not _current_midiout_labels:
        getMidioutLabels()
    # normaliser le label à chercher : enlever le dernier "x:y"
    norm_label = " ".join(label.split()[:-1])

    for i, port_label in enumerate(_current_midiout_labels):
        norm_port = " ".join(port_label.split()[:-1])
        if norm_port.startswith(norm_label):
            return i
    return None

def getMidiinLabels():
    """Retourne la liste des libellés des ports d'entrée MIDI disponibles sous forme de liste de chaînes.
    Filtre les ports fantômes 'RtMidiIn Client' si nécessaire.
    """
    global _current_midiin_labels
    midi = rtmidi.MidiIn()
    labels = midi.get_ports()
    del midi  # détruit l'instance pour éviter la prolifération de clients fantômes
    # On peut filtrer les ports fantômes si besoin :
    # _current_midiin_labels = [label for label in labels if not label.startswith("RtMidiIn Client")]
    _current_midiin_labels = [label for label in labels]
    return _current_midiin_labels

def getMidiinLabelTexts():
    """Retourne les ports d'entrée MIDI disponibles sous forme d'une chaîne multi-lignes
    avec index et label sur chaque ligne, utile pour choisir un port.
    """
    return "\n".join(f"{i:2d} {label}" for i, label in enumerate(getMidiinLabels()))

def getMidiinLabelFromIndex(index):
    """Retourne le libellé d'un port d'entrée MIDI par son index."""
    global _current_midiin_labels
    if not _current_midiin_labels:
        getMidiinLabels()
    return _current_midiin_labels[index]

def getIndexFromMidiinLabel(label):
    """Retourne l'index d'un port d'entrée MIDI par son label.
    Ignore l'ID final si présent, retourne None si introuvable.
    """
    global _current_midiin_labels
    if not _current_midiin_labels:
        getMidiinLabels()

    norm_label = " ".join(label.split()[:-1])

    for i, port_label in enumerate(_current_midiin_labels):
        norm_port = " ".join(port_label.split()[:-1])
        if norm_port.startswith(norm_label):
            return i
    return None

class MIDI_OUT:
    """Classe pour envoyer tous les messages MIDI (canal et système)."""

    def __init__(self):
        self.midiout = rtmidi.MidiOut()
        self.__current_port = None
        self.__last_status = None
        self.__running_status = True # on utilise le running_status (messages plus courts)
        self.__merge_note_off = True # on utilise le merge_note_off (messages plus courts)
        self.__muted = False         # on envoie les notes sur le midi_out
        self.__spy_output = None     # on n'envoie de log ni sur l'écran ni ailleurs
        self.__index = None          # on n'aura un index qu'après le self.open()
        self.__label = None          # on n'aura un label qu'après le self.open()

    def __repr__(self):
        text  = f"MIDI_OUT()\n"
        text += f'  index:          {self.__index}\n'
        text += f'  label:          "{self.__label}"\n'
        text += f'  running_status: {self.__running_status}\n'
        text += f'  merge_note_off: {self.__merge_note_off}\n'
        text += f'  muted:          {self.__muted}\n'
        text += f'  spy_output:     {self.__spy_output}\n'
        return text

    # --- Gestion port ---
    def openByLabel(self, label):
        """ opening by port's label, you're supposed to know it """
        index = getIndexFromMidioutLabel(label)
        if index is None:
            print(getMidioutLabelTexts())
            raise TypeError(f"can't find {label} in MIDI output ports")
        self.open(index)

    def open(self, port_index: int):
        """ opening by index, you're supposed to know it """
        if self.__current_port is not None:
            self.close()
        self.midiout.open_port(port_index)
        self.__current_port = port_index
        self.__index = port_index
        self.__label = getMidioutLabelFromIndex(self.__index)

    def close(self):
        if self.__current_port is not None:
            self.midiout.close_port()
            self.__current_port = None
        self.__last_status = None
        if self.__spy_output:
            self.__spy_output.write("\n")
            self.__spy_output.flush()

    # --- Propriétés ---
    @property
    def running_status(self): return self.__running_status
    @running_status.setter
    def running_status(self, value: bool): self.__running_status = bool(value)

    @property
    def merge_note_off(self): return self.__merge_note_off
    @merge_note_off.setter
    def merge_note_off(self, value: bool): self.__merge_note_off = bool(value)

    @property
    def muted(self): return self.__muted
    @muted.setter
    def muted(self, value: bool): self.__muted = bool(value)

    @property
    def spy_output(self): return self.__spy_output
    @spy_output.setter
    def spy_output(self, value):
        if value is not None and not hasattr(value, "write"):
            raise TypeError("spy_output doit être None ou un objet fichier avec write()")
        self.__spy_output = value

    # --- Envoi bas niveau ---
    def send_raw(self, data: list[int]):
        if self.__current_port is None:
            raise RuntimeError("Port MIDI non ouvert")
        if not self.__muted:
            self.midiout.send_message(data)
        if self.__spy_output:
            if data[0] >= 0x80: self.__spy_output.write("\n")
            for b in data: self.__spy_output.write(f"{b:02x} ")
            self.__spy_output.flush()

    def __send_message(self, status: int, data: list[int]):
        if self.__running_status and self.__last_status == status:
            self.send_raw(data)
        else:
            self.send_raw([status] + data)
            self.__last_status = status

    # --- Messages canal ---
    def note_on(self, note: int, velocity: int = 64, channel: int = 0):
        status = NOTE_ON | (channel & 0x0F)
        self.__send_message(status, [note & 0x7F, velocity & 0x7F])

    def note_off(self, note: int, velocity: int = 0, channel: int = 0):
        if self.__merge_note_off:
            self.note_on(note, 0, channel)
        else:
            status = NOTE_OFF | (channel & 0x0F)
            self.__send_message(status, [note & 0x7F, velocity & 0x7F])

    def control_change(self, control: int, value: int, channel: int = 0):
        status = CONTROL_CHANGE | (channel & 0x0F)
        self.__send_message(status, [control & 0x7F, value & 0x7F])

    def program_change(self, program: int, channel: int = 0):
        status = PROGRAM_CHANGE | (channel & 0x0F)
        self.__send_message(status, [program & 0x7F])

    def channel_aftertouch(self, pressure: int, channel: int = 0):
        status = CHANNEL_AFTERTOUCH | (channel & 0x0F)
        self.__send_message(status, [pressure & 0x7F])

    def poly_aftertouch(self, note: int, pressure: int, channel: int = 0):
        status = POLY_AFTERTOUCH | (channel & 0x0F)
        self.__send_message(status, [note & 0x7F, pressure & 0x7F])

    def pitch_bend(self, value: int, channel: int = 0):
        lsb = value & 0x7F
        msb = (value >> 7) & 0x7F
        status = PITCH_BEND | (channel & 0x0F)
        self.__send_message(status, [lsb, msb])

    def all_notes_off(self):
        for ch in range(16):
            self.control_change(123, 0, ch)  # CC123 All Notes Off

    # --- Messages système ---
    def sysex(self, data: list[int]):
        """Envoie SysEx (sans F0/F7)"""
        if not all(0 <= b <= 127 for b in data):
            raise ValueError("SysEx data doit être 7 bits")
        self.send_raw([START_OF_SYSEX] + list(data) + [END_OF_SYSEX])

    def timing_clock(self): self.send_raw([TIMING_CLOCK])
    def start(self): self.send_raw([START])
    def stop(self): self.send_raw([STOP])
    def continue_(self): self.send_raw([CONTINUE])
    def active_sensing(self): self.send_raw([ACTIVE_SENSING])
    def reset(self): self.send_raw([SYSTEM_RESET])
    def midi_time_code_qf(self, value: int): self.send_raw([MIDI_TIME_CODE_QF, value & 0x7F])
    def song_position_pointer(self, value: int):
        lsb = value & 0x7F
        msb = (value >> 7) & 0x7F
        self.send_raw([SONG_POSITION_POINTER, lsb, msb])
    def song_select(self, song: int): self.send_raw([SONG_SELECT, song & 0x7F])
    def tune_request(self): self.send_raw([TUNE_REQUEST])

class MIDI_IN:
    """Classe pour recevoir tous les messages MIDI (canal et système) avec callbacks et spy output."""

    def __init__(self):
        self.midiin = rtmidi.MidiIn()
        self.__current_port = None
        self.__muted = False
        self.__spy_output = None
        self.__index = None
        self.__label = None

        # Dictionnaire de callbacks
        self.callbacks = {
            NOTE_ON: None,
            NOTE_OFF: None,
            POLY_AFTERTOUCH: None,
            CONTROL_CHANGE: None,
            PROGRAM_CHANGE: None,
            CHANNEL_AFTERTOUCH: None,
            PITCH_BEND: None,
            MIDI_TIME_CODE_QF: None,
            SONG_POSITION_POINTER: None,
            SONG_SELECT: None,
            TUNE_REQUEST: None,
            START_OF_SYSEX: None,
            END_OF_SYSEX: None,
            TIMING_CLOCK: None,
            START: None,
            STOP: None,
            CONTINUE: None,
            ACTIVE_SENSING: None,
            SYSTEM_RESET: None,
            "other": None
        }

    def __repr__(self):
        text  = f"MIDI_IN()\n"
        text += f'  index:      {self.__index}\n'
        text += f'  label:      "{self.__label}"\n'
        text += f'  muted:      {self.__muted}\n'
        text += f'  spy_output: {self.__spy_output}\n'
        return text

    # --- Gestion port ---
    def openByLabel(self, label):
        """Ouvre le port MIDI d'entrée par son label"""
        index = getIndexFromMidioutLabel(label)  # Réutilisation fonction getIndexFromMidioutLabel
        if index is None:
            print(getMidiinLabelTexts())
            raise TypeError(f"can't find {label} in MIDI input ports")
        self.open(index)

    def open(self, port_index: int):
        """Ouvre le port MIDI d'entrée par index"""
        if self.__current_port is not None:
            self.close()
        ports = self.midiin.get_ports()
        if port_index >= len(ports):
            raise ValueError(f"Port index {port_index} invalide, max={len(ports)-1}")
        self.midiin.open_port(port_index)
        self.midiin.set_callback(self.__rtmidi_callback)
        self.__current_port = port_index
        self.__index = port_index
        self.__label = getMidiinLabelFromIndex(port_index)

    def close(self):
        if self.__current_port is not None:
            self.midiin.close_port()
            self.__current_port = None
        if self.__spy_output:
            self.__spy_output.write("\n")
            self.__spy_output.flush()

    # --- Propriétés ---
    @property
    def spy_output(self):
        return self.__spy_output

    @spy_output.setter
    def spy_output(self, value):
        if value is not None and not hasattr(value, "write"):
            raise TypeError("spy_output doit être None ou un objet fichier avec write()")
        self.__spy_output = value

    @property
    def muted(self):
        return self.__muted

    @muted.setter
    def muted(self, value: bool):
        self.__muted = bool(value)

    # --- Callback interne ---
    def __rtmidi_callback(self, message_data, timestamp):
        if not message_data:
            return
        msg, ts = message_data
        if not msg:
            return

        # Affichage spy output
        if self.__spy_output:
            self.__spy_output.write(" ".join(f"{b:02X}" for b in msg) + "\n")
            self.__spy_output.flush()

        if self.__muted:
            return

        status = msg[0] & 0xF0
        channel = msg[0] & 0x0F

        # Messages de canal
        if status in (NOTE_ON, NOTE_OFF, POLY_AFTERTOUCH, CONTROL_CHANGE,
                      PROGRAM_CHANGE, CHANNEL_AFTERTOUCH, PITCH_BEND):
            cb = self.callbacks[status]
            if cb:
                if status == PITCH_BEND:
                    value = msg[1] | (msg[2] << 7) if len(msg) > 2 else 0
                    cb(channel, value, ts)
                elif status in (PROGRAM_CHANGE, CHANNEL_AFTERTOUCH):
                    cb(channel, msg[1], ts)
                else:
                    if len(msg) > 2:
                        cb(channel, msg[1], msg[2], ts)

        # Messages système et autres
        else:
            code = msg[0]
            cb = self.callbacks.get(code) or self.callbacks["other"]
            if cb:
                if code == SONG_POSITION_POINTER:
                    value = msg[1] | (msg[2] << 7) if len(msg) > 2 else 0
                    cb(value, ts)
                elif code in (MIDI_TIME_CODE_QF, SONG_SELECT):
                    cb(msg[1] if len(msg) > 1 else 0, ts)
                else:
                    cb(msg, ts)

def spyMidiInput(port_index: int, poll_interval=0.001, simple_log=False, spy_output=None):
    """ Écoute le port MIDI spécifié en polling et affiche tous les messages reçus en hexadécimal."""
    """ Par défaut on imprime sur l'écran, mais on peut le changer."""
    global _current_midiin_labels
    import time
    midi_in = rtmidi.MidiIn()
    midi_in.open_port(port_index)

    print("<<< Spying %s >>>"%_current_midiin_labels[port_index])
    print("En attente de messages MIDI (Ctrl+C pour quitter)...")
    try:
        while True:
            msg = midi_in.get_message()
            if msg:
                message_data, timestamp = msg
                if simple_log:
                    text = f"[{timestamp:.6f}] {' '.join(f'{b:02X}' for b in message_data)}"
                else:
                    text = ' '.join(['%02x'%byte for byte in message_data])

                if spy_output is None:
                    print(text)
                else:
                    spy_output.write(' '.join(['%02x'%byte for byte in message_data]))
                    spy_output.write('\n')
                    spy_output.flush()
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        print("\nFermeture du port MIDI.")
        midi_in.close_port()

if __name__ == "__main__":

    print("\nMIDI OUT")
    print(getMidioutLabelTexts())
    
    print("\nMIDI IN")
    print(getMidiinLabelTexts())
    
