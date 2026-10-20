"""
Where the app's files live.

Run from source (`python run.py`), everything stays in the repository's `data/` folder, so
Data-tab edits go straight into the committed app data JSON.

Run as the packaged exe (PyInstaller sets `sys.frozen`), the bundled files are read-only and
replaced on every update, so the app data JSON and database live in a per-user folder instead.
The bundled app data JSON is copied there on first run and never overwritten afterwards.

The BSData catalogues are never shipped: users put them in the `bsdata` folder inside the
data folder (BSDATA_DIR overrides it). The cached import sits next to the app data JSON, and
so does the `wahapedia` folder of rules and stratagems the user downloads (WAHAPEDIA_DIR overrides it).

Version 1 kept everything in `40k_data.json`; on the first start of version 2 that file and
the database (whose saved scenarios refer to its IDs) are moved into `archive-v1`.
"""

import os
import shutil
import sys
from pathlib import Path

APP_FOLDER = '40K Combat Simulator'
DATA_FILE = 'app_data.json'
DATABASE_FILE = 'combat_sim.db'
BSDATA_FOLDER = 'bsdata'
BSDATA_CACHE_FILE = 'bsdata_cache.json'
WAHAPEDIA_FOLDER = 'wahapedia'
V1_DATA_FILE = '40k_data.json'
V1_ARCHIVE_FOLDER = 'archive-v1'


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
    archive_v1(folder)
    data_file = folder / DATA_FILE
    shipped = bundle_root() / 'data' / DATA_FILE
    if not data_file.exists() and shipped.exists() and shipped.resolve() != data_file.resolve():
        shutil.copyfile(shipped, data_file)
    return folder


def archive_v1(folder):
    """Move a version 1 data JSON and its database into archive-v1 (once)."""
    old = folder / V1_DATA_FILE
    if not old.exists():
        return
    archive = folder / V1_ARCHIVE_FOLDER
    archive.mkdir(exist_ok=True)
    for name in (V1_DATA_FILE, DATABASE_FILE):
        if (folder / name).exists() and not (archive / name).exists():
            shutil.move(str(folder / name), str(archive / name))
