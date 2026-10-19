# Releasing

Users download a zipped Windows build from GitHub Releases and run `40kCombatSimulator.exe`; no Python needed.

## Publish a release

1. Bump `__version__` in `src/combat_sim/__init__.py` (e.g. `1.0.0` → `1.1.0`) and commit.
2. Tag and push:

   ```bash
   git tag v1.1.0
   git push origin master v1.1.0
   ```

3. The **Release** workflow (`.github/workflows/release.yml`) checks the tag matches `__version__`, runs the full test suite, builds the exe with PyInstaller, zips it and creates the GitHub release with the zip attached and notes generated from the commits. If any step fails, no release is created; fix, delete the tag (`git tag -d v1.1.0 && git push origin :v1.1.0`) and tag again.

## Build locally

```bash
pip install -r requirements-dev.txt
pyinstaller --noconfirm --clean combat_sim.spec
```

The app is in `dist/40kCombatSimulator/` (about 66 MB). To try it without touching your own `%APPDATA%` data, point `APPDATA` at a scratch folder first (PowerShell: `$env:APPDATA = "C:\temp\appdata"; .\dist\40kCombatSimulator\40kCombatSimulator.exe`).

## How the build works

- `combat_sim.spec` bundles `run.py`, the `combat_sim` package, its `templates/` and `static/` folders, and `data/40k_data.json`. Anything new the app reads from disk must be added to its `datas`.
- Pages must not load anything from the internet (Plotly is in `static/vendor`), so the exe works offline.
- The exe runs `run.py`: waitress on `127.0.0.1`, port 5000 or the next free port, browser opened automatically, console window shows the address and closing it stops the app.
- `src/combat_sim/paths.py` keeps user data in `%APPDATA%\40K Combat Simulator\`. The shipped data JSON only seeds that folder on first run; a newer release's data does **not** replace a user's existing copy. Users can pick up new shipped data with **Data → Import JSON**, or by deleting their copy before starting the app.

## Known limits

- **Unsigned exe.** Windows SmartScreen shows "Windows protected your PC" on first run (More info → Run anyway). A code-signing certificate removes this; it costs money and is not set up.
- **Windows only.** Mac and Linux users run from source (`launcher.sh`). PyInstaller can't cross-compile; a macOS build would need a `macos-latest` job.
- **Antivirus false positives** occasionally hit PyInstaller apps. The one-folder build (rather than one-file) makes them rarer.
