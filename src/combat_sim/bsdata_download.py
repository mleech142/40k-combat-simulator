"""
"Download latest": the newest BSData wh40k-11e catalogues from GitHub.

latest_commit() asks GitHub for the default branch's newest commit; download_zip() fetches
that commit's zip; install_zip() extracts it into the BSData folder. Only top-level `.json`
files are kept, nothing outside the target folder is ever written, and the old folder is
replaced only once the new one is complete, so a failed download leaves the old data in
place. The app never redistributes the files: each user downloads their own copy.
"""

import io
import json
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

REPO = 'BSData/wh40k-11e'
API = f'https://api.github.com/repos/{REPO}'
TIMEOUT = 60  # seconds per request
MAX_ZIP_BYTES = 200 * 1024 * 1024
USER_AGENT = '40k-combat-simulator'


class DownloadError(Exception):
    """GitHub couldn't be reached, or the download isn't a usable BSData zip."""


def _get(url):
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'application/vnd.github+json'})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            data = response.read(MAX_ZIP_BYTES + 1)
    except OSError as err:  # URLError, HTTPError, timeouts
        raise DownloadError(f'Could not reach GitHub: {err}') from err
    if len(data) > MAX_ZIP_BYTES:
        raise DownloadError('The download is larger than expected')
    return data


def latest_commit():
    """{'sha', 'date'} of the newest commit on the default branch."""
    try:
        info = json.loads(_get(f'{API}/commits/HEAD'))
        return {'sha': info['sha'], 'date': info['commit']['committer']['date']}
    except (ValueError, KeyError, TypeError) as err:
        raise DownloadError('GitHub sent an unexpected answer about the latest commit') from err


def download_zip(sha):
    return _get(f'{API}/zipball/{sha}')


def install_zip(data, target):
    """
    Extract the catalogues from a GitHub zipball into `target`, replacing what's there.
    Returns the number of files installed.
    """
    target = Path(target)
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as err:
        raise DownloadError('The download is not a zip file') from err

    # GitHub zips hold one folder ("BSData-wh40k-11e-<sha>/"); keep only the .json files directly in it
    wanted = []
    for info in archive.infolist():
        parts = PurePosixPath(info.filename).parts
        if info.is_dir() or len(parts) != 2 or '..' in parts or info.filename.startswith('/'):
            continue
        name = parts[1]
        if name.lower().endswith('.json') and Path(name).name == name:
            wanted.append((info, name))
    if not wanted:
        raise DownloadError('The download holds no catalogue .json files')

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='bsdata-new-', dir=target.parent))
    try:
        for info, name in wanted:
            with archive.open(info) as src, open(staging / name, 'wb') as dst:
                shutil.copyfileobj(src, dst)
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
        raise DownloadError(f'Could not write the catalogues: {err}') from err
    return len(wanted)
