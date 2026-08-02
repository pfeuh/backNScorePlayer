#!/usr/bin/python
# -*- coding: utf-8 -*-

from gtts import gTTS
import os

tts = gTTS(text="le player audio est installé", lang='fr')
tts.save("mp3/audioSplash.mp3")
#~ os.system("start salut.mp3") # Pour Windows