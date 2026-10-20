# Releasing

Users download a zipped build for Windows or Linux from GitHub Releases and run `40kCombatSimulator` (`.exe` on Windows); no Python needed.

## Publish a release

1. Bump `__version__` in `src/combat_sim/__init__.py` (e.g. `1.0.0` → `1.1.0`). Optionally write `docs/release-notes/v1.1.0.md`; the release uses it as its notes (otherwise notes are generated from the commits). Commit.
2. Tag and push:

   ```bash
   git tag v1.1.0
   git push origin master v1.1.0
   ```

3. The **Release** workflow (`.github/workflows/release.yml`) builds on Windows and on Linux (Ubuntu 22.04) side by side: each checks the tag matches `__version__`, runs the full test suite, builds with PyInstaller and zips the result (`40kCombatSimulator-v1.1.0-windows.zip`, `…-linux.zip`). When both succeed, it creates the GitHub release with both zips and the notes from `docs/release-notes/<tag>.md` (or generated from the commits). If any step fails, no release is created; fix, delete the tag (`git tag -d v1.1.0 && git push origin :v1.1.0`) and tag again.

**Trial build:** Actions → Release → *Run workflow* (on any branch) builds and tests both zips and keeps them as workflow artifacts (`…-vX.Y.Z-test-…`), without publishing a release.

**Tests on every push:** the **Tests** workflow (`.github/workflows/tests.yml`) runs the suite on Linux for every push and pull request.

## Build locally

```bash
pip install -r requirements-dev.txt
pyinstaller --noconfirm --clean combat_sim.spec
```

The app is in `dist/40kCombatSimulator/` (about 66 MB). PyInstaller builds for the system it runs on: build on Windows for the Windows zip, on Linux for the Linux one. To try a build without touching your own data, point it at a scratch folder first: PowerShell `$env:APPDATA = "C:\temp\appdata"; .\dist\40kCombatSimulator\40kCombatSimulator.exe`; Linux `XDG_DATA_HOME=/tmp/appdata ./dist/40kCombatSimulator/40kCombatSimulator`.

## How the build works

- `combat_sim.spec` bundles `run.py`, the `combat_sim` package, its `templates/` and `static/` folders, and `data/app_data.json` (never the BSData catalogues: users download those). Anything new the app reads from disk must be added to its `datas`.
- Pages must not load anything from the internet (Plotly and Font Awesome are in `static/vendor`), so the exe works offline.
- The exe runs `run.py`: waitress on `127.0.0.1`, port 5000 or the next free port, browser opened automatically, console window shows the address and closing it stops the app.
- `src/combat_sim/paths.py` keeps user data in `%APPDATA%\40K Combat Simulator\`. The shipped app data JSON only seeds that folder on first run; a newer release's app data does **not** replace a user's existing copy. Users can pick up new shipped app data with **Data → Import JSON**, or by deleting their copy before starting the app.
- The BSData catalogues are never in the build: each user downloads them on the Setup page (GitHub API and zipball, `bsdata_download.py`) into their data folder. The exe needs internet access only for that.
- tkinter stays in the build for the Setup page's folder dialog.

## Known limits

- **Unsigned exe.** Windows SmartScreen shows "Windows protected your PC" on first run (More info → Run anyway). A code-signing certificate removes this; it costs money and is not set up.
- **Windows only.** Mac and Linux users run from source (`launcher.sh`). PyInstaller can't cross-compile; a macOS build would need a `macos-latest` job.
- **Antivirus false positives** occasionally hit PyInstaller apps. The one-folder build (rather than one-file) makes them rarer.
