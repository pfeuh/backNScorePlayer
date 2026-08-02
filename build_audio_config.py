#!/usr/bin/python
# -*- coding: utf-8 -*-

import json
from pathlib import Path
import os

cfg = {
    "auto_start": True,
    "auto_error_msg": True,
    "auto_start_delay": 5,
    "rewind_time": 2,
    "fast_forward_time": "",
}

def get_unique_mp3_names(root_dir):
    mp3_names = []
    seen = set()
    for path in Path(root_dir).rglob('*.mp3'):
        name = path.stem
        if name not in seen:
            mp3_names.append(name)
            seen.add(name)
    return mp3_names

def generate_playlist_div(json_path):
    with open(json_path, 'r', encoding='utf-8') as f:
        mp3_list = json.load(f)

    div_items = []
    for filename in mp3_list:
        stem = Path(filename).stem
        
        # Structure épurée sans poignée
        item = (
            f'<div class="mp3-item" data-filename="{filename}">\n'
            f'    <div class="details">\n'
            f'        <span class="stem">{stem}</span>\n'
            f'    </div>\n'
            f'</div>'
        )
        div_items.append(item)

    return "\n".join(div_items)

if __name__ == "__main__":

    DATABASE = "/mnt/Data1/Documents/backNScoreData/database"
    SORTED_MP3_JSON_FNAME = "mp3_sorted.json"
    SKELETON = "sort_mp3_skeleton.htm"
    HTML_PAGE = "sort_mp3.htm"

    mp3_types = get_unique_mp3_names(DATABASE)

    with open(SORTED_MP3_JSON_FNAME, 'w', encoding='utf-8') as f:
        json.dump(mp3_types, f, ensure_ascii=False, indent=4)

    print(f"Nombre de fichiers : {len(mp3_types)}")

    with open(SKELETON, "r", encoding="utf-8") as fp:
        page = fp.read()
        
    html_playlist = generate_playlist_div(SORTED_MP3_JSON_FNAME)
    
    # On remplace ton marqueur dans le squelette
    page = page.replace("#MP3_TYPES#", html_playlist)
    
    with open(HTML_PAGE, "w", encoding="utf-8") as fp:
        fp.write(page)