"""
Settings, in `settings.json` next to the app data JSON. Two kinds:

The install's, which only its host changes (the Setup page):

{
  "bsdata_dir": "C:/...",               # BSData folder chosen on the Setup page (absent = default)
  "download": {"sha", "date", "downloaded_at", "folder"},   # Last "Download latest"
  "wahapedia_download": {...},          # Last Wahapedia download
  "setup_skipped": true,                # "Continue without BSData" on first run
  "relay_url": "https://..."            # Play's relay (desktop installs; a self-hosted copy uses RELAY_URL)
}

A person's own preferences (PREFERENCES): "show_play" (Play in the menu) and "player_name" (the name they play
under online). Read and change them only through access.principal(): on a desktop install they are in this file
too; on a self-hosted copy each player's are in their account (users.preferences), a missing one falling back to
this file's.

Settings are per install and never shipped. A missing or unreadable file gives empty settings.
"""

import json
from pathlib import Path

from .data_store import write_json

SETTINGS_FILE = 'settings.json'
PREFERENCES = ('show_play', 'player_name')  # A person's own (access.Principal.preferences)


def settings_path(data_json):
    return Path(data_json).parent / SETTINGS_FILE


def load_settings(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(path, settings):
    write_json(path, settings)


def update_settings(path, **changes):
    """Merge `changes` into the saved settings (None removes a key) and return them."""
    settings = load_settings(path)
    for key, value in changes.items():
        if value is None:
            settings.pop(key, None)
        else:
            settings[key] = value
    save_settings(path, settings)
    return settings
