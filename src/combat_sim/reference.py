"""
The reference data (architecture review, candidate 4): BSData's catalogues, Wahapedia's rules and stratagems and
the app data JSON, merged into the reference tables. One module owns loading them, rebuilding the tables and
saving the app data, and publishes what was loaded as one `Reference` snapshot:

- `load(app, bsdata=..., wahapedia=..., data=...)`: (re)import what's asked, merge it with the app data, rebuild the
  tables in one transaction (data_store.rebuild_reference_tables) and publish the new snapshot. Other players'
  requests see the old tables and snapshot until it's done; a failed rebuild changes neither.
- `current(app)`: the snapshot now (the status of each import, the folder in use, what was merged).
- `save_app_data(app)`: commit a Data-tab change and rewrite the app data JSON from the tables.

Loads and saves hold data_store.REFERENCE_LOCK, so a save never exports half-built tables and two loads never mix.
"""

from dataclasses import dataclass, field, replace
from pathlib import Path

from . import wahapedia
from .data_store import (REFERENCE_LOCK, export_data, load_bsdata, merge_data, read_data_file, rebuild_reference_tables,
                         write_data_file)
from .models import db
from .settings import load_settings


@dataclass(frozen=True)
class Reference:
    bsdata_dir: str = None
    bsdata_result: object = None
    bsdata_status: dict = field(default_factory=dict)
    wahapedia_result: object = None
    wahapedia_status: dict = field(default_factory=dict)
    data_source: object = None  # the app data JSON's 'source'
    unmatched: object = None  # app data records that matched nothing imported (kept in the file)


EMPTY = Reference()


def current(app):
    return app.extensions.get('reference', EMPTY)


def bsdata_folder(app):
    """The BSData folder in use: chosen on the Setup page, else the default."""
    chosen = load_settings(app.config['SETTINGS_JSON']).get('bsdata_dir')
    return chosen or app.config['DEFAULT_BSDATA_DIR']


def load(app, bsdata=False, wahapedia_too=False, data=None):
    """
    Rebuild the reference tables and publish the new snapshot. `bsdata`: True to import the BSData folder again
    (from the cache when its files haven't changed), 'fresh' to ignore the cache; `wahapedia_too`: import the
    Wahapedia folder again; `data`: the app data to merge (else the app data JSON). Answers the new snapshot; on a
    failure the tables and the published snapshot stay as they were and the error is raised.
    """
    with REFERENCE_LOCK:
        now = current(app)
        if data is None:
            data = read_data_file(app.config['DATA_JSON'])
        if wahapedia_too:
            result, status = wahapedia.load_folder(app.config['WAHAPEDIA_DIR'])
            now = replace(now, wahapedia_result=result, wahapedia_status=status)
        if bsdata:
            if bsdata == 'fresh':
                Path(app.config['BSDATA_CACHE']).unlink(missing_ok=True)
            folder = bsdata_folder(app)
            known = [k['name'] for k in data.get('keywords', [])] or None
            result, status = load_bsdata(folder, app.config['BSDATA_CACHE'], known)
            now = replace(now, bsdata_dir=folder, bsdata_result=result, bsdata_status=status)
        report = []
        merged, unmatched = merge_data(data, now.bsdata_result, now.wahapedia_result, report)
        try:
            rebuild_reference_tables(merged)
        except Exception:
            db.session.rollback()
            raise
        now = replace(now, data_source=data.get('source'), unmatched=unmatched,
                      wahapedia_status=dict(now.wahapedia_status, report=report))
        app.extensions['reference'] = now  # one assignment: readers see the old snapshot or this one
        return now


def save_app_data(app):
    """Commit the session's Data-tab change and rewrite the app data JSON (never while the tables are rebuilt)."""
    with REFERENCE_LOCK:
        db.session.commit()
        now = current(app)
        write_data_file(app.config['DATA_JSON'], export_data(now.data_source, now.unmatched))


def export(app):
    """The app data as the app data JSON would hold it now."""
    now = current(app)
    return export_data(now.data_source, now.unmatched)


def label(app):
    """Data version for the footer: the downloaded commit, else the game system revision."""
    now = current(app)
    if not now.bsdata_status.get('loaded'):
        return None
    download = load_settings(app.config['SETTINGS_JSON']).get('download') or {}
    if download.get('sha') and download.get('folder') == now.bsdata_dir:
        return f"@ {download['sha'][:7]}, {(download.get('date') or '')[:10]}".rstrip(', ')
    revision = ((now.bsdata_status.get('source') or {}).get('game_system') or {}).get('revision')
    return f'game system revision {revision}' if revision is not None else None


def wahapedia_label(app):
    """Wahapedia's last update for the footer, or None when it isn't loaded."""
    status = current(app).wahapedia_status
    if not status.get('loaded'):
        return None
    return ((status.get('source') or {}).get('last_update') or '')[:10] or 'loaded'
