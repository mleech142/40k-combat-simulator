"""
Where the app's files live.

Run from source (`python run.py`), everything stays in the repository's `data/` folder, so
Data-tab edits go straight into the committed data JSON.

Run as the packaged exe (PyInstaller sets `sys.frozen`), the bundled files are read-only and
replaced on every update, so the data JSON and database live in a per-user folder instead.
The bundled data JSON is copied there on first run and never overwritten afterwards.
"""

import os
import shutil
import sys
from pathlib import Path

APP_FOLDER = '40K Combat Simulator'
DATA_FILE = '40k_data.json'
DATABASE_FILE = 'combat_sim.db'


def is_frozen():
    return bool(getattr(sys, 'frozen', False))


def bundle_root():
    """Folder holding the shipped files: the PyInstaller bundle, or the repository root."""
    if is_frozen():
        return Path(getattr(sys, '_MEIPASS', Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent.parent


def user_data_dir():
    """Per-user folder for the packaged app's data JSON and database."""
    if sys.platform == 'win32':
        base = Path(os.environ.get('APPDATA') or Path.home() / 'AppData' / 'Roaming')
    elif sys.platform == 'darwin':
        base = Path.home() / 'Library' / 'Application Support'
    else:
        base = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share')
    return base / APP_FOLDER


def data_dir():
    """Folder the app reads and writes its data JSON and database in."""
    return user_data_dir() if is_frozen() else bundle_root() / 'data'


def prepare_data_dir():
    """Create the data folder and, on first run, seed it with the shipped data JSON. Returns the folder."""
    folder = data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    data_file = folder / DATA_FILE
    shipped = bundle_root() / 'data' / DATA_FILE
    if not data_file.exists() and shipped.exists() and shipped.resolve() != data_file.resolve():
        shutil.copyfile(shipped, data_file)
    return folder
