"""
"Download latest" for rules and stratagems: the Wahapedia 11th edition data export.

download() fetches every file wahapedia.py reads (wahapedia.FILES) into a new folder, checks
that it imports, and only then replaces the Wahapedia folder, so a failed download leaves
the old data in place. Only those files are written, nothing outside the target folder. The
app never redistributes the files: each user downloads their own copy, and the pages credit
Wahapedia as its export page asks.
"""

import shutil
import tempfile
import urllib.request
from pathlib import Path

from . import web
from .wahapedia import FILES, WahapediaError, import_folder

BASE_URL = 'https://wahapedia.ru/wh40k11ed/'
TIMEOUT = 60  # seconds per request
MAX_FILE_BYTES = 50 * 1024 * 1024
USER_AGENT = '40k-combat-simulator'


class DownloadError(Exception):
    """Wahapedia couldn't be reached, or sent files that don't import."""


def fetch(name):
    """One export file's bytes."""
    request = urllib.request.Request(BASE_URL + name, headers={'User-Agent': USER_AGENT})
    try:
        with web.urlopen(request, TIMEOUT) as response:
            data = response.read(MAX_FILE_BYTES + 1)
    except OSError as err:  # URLError, HTTPError, timeouts
        raise DownloadError(f'Could not reach Wahapedia: {err}') from err
    if len(data) > MAX_FILE_BYTES:
        raise DownloadError(f'{name} is larger than expected')
    return data


def download(target):
    """
    Download the export into `target`, replacing what's there. Returns {'files', 'last_update'}.
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='wahapedia-new-', dir=target.parent))
    try:
        for name in FILES:
            (staging / name).write_bytes(fetch(name))
        try:
            result = import_folder(staging)
        except WahapediaError as err:
            raise DownloadError(f'The downloaded files could not be read: {err}') from err
        old = target.with_name(target.name + '.old')
        if old.exists():
            shutil.rmtree(old)
        if target.exists():
            target.rename(old)
        staging.rename(target)
        if old.exists():
            shutil.rmtree(old, ignore_errors=True)
    except OSError as err:
        shutil.rmtree(staging, ignore_errors=True)
        raise DownloadError(f'Could not write the files: {err}') from err
    except DownloadError:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {'files': len(FILES), 'last_update': result.source.get('last_update')}
