# 40K Combat Simulator

A local web application for simulating Warhammer 40,000 (11th edition) combat. It works out the expected damage of a unit's weapons against a target, compares whole armies in a matchup grid, and analyses where an army is strong or weak — with both fast analytical averages and Monte Carlo dice simulations.

It replaces the `40KCombatSimulator_v2.xlsx` spreadsheet: its maths is reproduced formula for formula (and checked against the spreadsheet's own results in the tests). See [docs/excel-parity.md](docs/excel-parity.md) for the feature-by-feature comparison.

## Features

- **Simulator** — one attacking unit against one target: tick any of the unit's weapons (with how many models carry each), set battle conditions, tick faction rules and stratagems for either side, and add one-off *extra modifiers* to the target. Shows per-weapon results, totals and (in Monte Carlo mode) a damage distribution with kill and wipe chances.
- **Matchups** — your army against an opponent's real units or generic benchmark profiles (MEQ, TEQ, vehicles…): a heat-map grid per unit and for the whole army, unit sizes with correct points, army-wide rules and stratagems, and an **Army Analysis** panel (summary, gaps, role scores with a spider chart, Toughness/Save coverage, reverse durability).
- **Data** — edit factions (with their rules and stratagems), units (datasheet stats, points per unit size, loadout with quantities) and weapons. Read-only reference tabs list every rules keyword and the benchmark target profiles. Every confirmed change is saved straight to the data file.
- **Scenarios** — save Simulator setups, compare and re-run them.
- **Rules coverage** — every keyword and stat modifier the spreadsheet models: Lethal/Sustained Hits, Devastating Wounds, Anti-X, Blast, Cleave, Melta, Rapid Fire, Torrent, Twin-Linked, Lance, Heavy, Psychic, Ignores Cover, Stealth, Feel No Pain, Critical Hits/Wounds X+, Snap Shooting, re-rolls, and Hit/Wound/AP/Damage/Attacks/Strength/Toughness/Save/BS/WS modifiers. The **Data → Keywords** tab lists them all with the exact format to write.

## Installation

### Download (Windows, no Python needed)

1. Download `40kCombatSimulator-vX.Y.Z-windows.zip` from the [Releases page](https://github.com/mleech142/40k-combat-simulator/releases).
2. Unzip it anywhere and double-click `40kCombatSimulator.exe`. Windows may warn about an unknown publisher (the exe isn't code-signed): choose **More info → Run anyway**.
3. Your browser opens the app. Close the console window to stop it.

Your data (the data JSON with your edits, plus saved scenarios and comparisons) lives in `%APPDATA%\40K Combat Simulator\`, not in the unzipped folder, so you can replace the folder with a newer release without losing anything. On first run that folder is filled with the data shipped in the release; later releases never overwrite it.

### From source

#### Prerequisites

- Python 3.10+
- pip

#### Setup

```bash
pip install -r requirements.txt
python run.py              # add --debug for Flask's debugger while developing (never in a release)
```

The browser opens <http://127.0.0.1:5000> (or another free port if 5000 is taken; the console shows which). The app only listens on this computer. Run from source, the app reads and writes `data/` in the repository.

#### Quick start

- Windows: double-click `launcher.bat`
- Linux/Mac: `chmod +x launcher.sh && ./launcher.sh`

The launchers create a virtual environment, install the requirements, warn if `data/bsdata` holds no BSData catalogues, and start the app.

## Using the app

### Simulator

1. Pick an attacking unit; its weapons appear with **Use** ticks and a **Qty** (models carrying each weapon). Alternative profiles of one gun ("Plasma Gun - Standard / Supercharged") start with only the first ticked.
2. Choose **Shooting** or **Melee**, pick a defender and its model count.
3. Set conditions (half range, stationary, cover, charged, attacker damaged, re-rolls), tick rules and stratagems, and optionally type **Extra modifiers** for the target (e.g. `Stealth, Feel No Pain 5+`).
4. **Run Simulation**. Tick **Monte Carlo** for a dice-rolled distribution.

### Matchups

1. **+ Add Unit** to the Attackers table (all of its weapons are added; a unit can be added more than once). Set each unit's **Size** — points and weapon quantities follow.
2. Switch Defenders between **Real units** (with per-unit model counts and extra modifiers) and **Generic profiles**.
3. Tick army-wide rules and stratagems, set conditions, then **Run Matrix**.
4. Change the **Display** metric (damage, models killed, % destroyed, points efficiency…), toggle **Show weapons**, click any cell for a Monte Carlo breakdown, and read the **Army Analysis** tabs.
5. **Save / Load Comparison** to keep a setup.

Both pages remember their setup while you move between tabs; it is cleared when the app restarts.

## Data

| File | What it holds |
| --- | --- |
| `data/bsdata/*.json` | **Unit data** — the BSData catalogues from [github.com/BSData/wh40k-11e](https://github.com/BSData/wh40k-11e): factions, units, weapons, loadouts and points. Download them yourself; they are never committed or shipped, and are read-only in the app. |
| `data/app_data.json` | **The app's own data** — your custom factions, units and weapons, faction rules and stratagems, modifiers added to BSData units, keywords and target profiles. Committed and distributed; the app writes every edit back to it. |
| `data/bsdata_cache.json` | The last BSData import, reused until the catalogue files change. Not committed. |
| `data/combat_sim.db` | SQLite working copy (rebuilt on start) plus your saved scenarios and comparisons. Not committed. |
| `40KCombatSimulator_v2.xlsx` | The original spreadsheet; now only the simulation engine's reference. Frozen. |

The Data page shows whether the BSData folder loaded. BSData units keep their IDs when the catalogues are updated, so saved scenarios and comparisons keep pointing at the right units.

**Export JSON / Import JSON** on the Data page download or replace the app data file (with a review screen before replacing); BSData is not part of it.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests                                         # 94 tests (Python + JavaScript)
node --test "tests/js/*.test.js"                               # JavaScript only (Node 18+)
python -m coverage run --source=src/combat_sim -m pytest tests && python -m coverage report
```

The tests never touch `data/` — each starts the app on its own temporary data file, with a small made-up BSData set (`tests/fixtures/bsdata`) where needed. They include the spreadsheet's own cached results as parity checks and an analytical-vs-Monte-Carlo agreement check. The JavaScript tests cover the pure page helpers in `static/js/core.js`; they run inside the pytest run when Node.js is installed.

## Project structure

```text
40k-combat-simulator/
├── src/combat_sim/
│   ├── app.py            # Flask app factory and all routes (pages + JSON API)
│   ├── models.py         # SQLAlchemy models; points-by-size logic
│   ├── bsdata.py         # Imports the BSData catalogues
│   ├── data_store.py     # Merges BSData with data/app_data.json; reads/validates/writes the app data
│   ├── simulation.py     # Combat engine: resolve() + analytical and Monte Carlo engines
│   ├── paths.py          # Where data lives: repository data/ from source, %APPDATA% in the exe
│   ├── templates/        # Jinja2 pages (Simulator, Matchups, Scenarios, Data)
│   └── static/           # CSS and JavaScript (core.js + app.js are loaded on every page)
├── data/                 # app_data.json (committed); bsdata/, the import cache and combat_sim.db (local)
├── archive/              # Version 1: the v1 workbook, 40k_data_v1.json and migrate_excel.py
├── tests/                # pytest suite (conftest.py provides a temporary-data app); tests/js: Node tests
├── docs/
│   ├── architecture.md   # How the pieces fit, conventions, maintainability notes
│   ├── excel-parity.md   # Feature-by-feature comparison with the spreadsheet
│   └── releasing.md      # Building the exe and publishing a release
├── archive/              # Superseded v1 workbook
├── .github/workflows/    # release.yml: builds and publishes the Windows release on a version tag
├── run.py                # Entry point (waitress server; --debug for development)
├── combat_sim.spec       # PyInstaller build of the Windows exe
├── requirements.txt      # Runtime dependencies
└── requirements-dev.txt  # Test and build tools
```

## Tech stack

- **Backend**: Python, Flask, Flask-SQLAlchemy, SQLite, NumPy, waitress
- **Frontend**: vanilla JavaScript, Plotly.js (charts; bundled in `static/vendor`, so the app works offline)
- **Packaging**: PyInstaller, GitHub Actions
- **Data import**: openpyxl

## License

MIT License - feel free to use and modify for your own gaming needs.

---

*This is a fan project and is not affiliated with Games Workshop. Warhammer 40,000 is a trademark of Games Workshop Ltd.*
