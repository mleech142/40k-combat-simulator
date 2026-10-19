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

### Prerequisites

- Python 3.10+
- pip

### Setup

```bash
pip install -r requirements.txt
python run.py
```

Then open <http://localhost:5000> (the launcher opens it for you).

### Quick start

- Windows: double-click `launcher.bat`
- Linux/Mac: `chmod +x launcher.sh && ./launcher.sh`

The launchers create a virtual environment, install the requirements, build `data/40k_data.json` from the workbook if it is missing, and start the app.

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
| `data/40k_data.json` | **The data** — factions, units, weapons, loadouts, rules, stratagems, keywords and target profiles. Committed and distributed; the app reads it on every start and writes every edit back to it. |
| `data/combat_sim.db` | SQLite working copy (rebuilt from the JSON on start) plus your saved scenarios and comparisons. Not committed. |
| `40KCombatSimulator_v2.xlsx` | The original spreadsheet the data was imported from. Frozen — edit data in the app. |

Building or refreshing the JSON from the spreadsheet:

```bash
python scripts/migrate_excel.py                   # first build (refuses if data/40k_data.json already exists)
python scripts/migrate_excel.py --force           # rebuild from Excel, discarding edits made in the app
python scripts/migrate_excel.py --output x.json   # write elsewhere, e.g. to compare
python scripts/migrate_excel.py --reference-only  # refresh only keywords and target profiles; keeps everything else
```

IDs stay stable across rebuilds, so saved scenarios and comparisons keep pointing at the right units. The script prints a report of likely typos and skipped rows.

**Export JSON / Import JSON** on the Data page download or replace the whole data file (with a review screen before replacing).

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests                                         # 88 tests (Python + JavaScript)
node --test "tests/js/*.test.js"                               # JavaScript only (Node 18+)
python -m coverage run --source=src/combat_sim,scripts -m pytest tests && python -m coverage report
```

The tests never touch `data/` — each starts the app on its own temporary data file. They include the spreadsheet's own cached results as parity checks and an analytical-vs-Monte-Carlo agreement check. The JavaScript tests cover the pure page helpers in `static/js/core.js`; they run inside the pytest run when Node.js is installed.

## Project structure

```text
40k-combat-simulator/
├── src/combat_sim/
│   ├── app.py            # Flask app factory and all routes (pages + JSON API)
│   ├── models.py         # SQLAlchemy models; points-by-size logic
│   ├── data_store.py     # Reads/validates/writes data/40k_data.json
│   ├── simulation.py     # Combat engine: resolve() + analytical and Monte Carlo engines
│   ├── templates/        # Jinja2 pages (Simulator, Matchups, Scenarios, Data)
│   └── static/           # CSS and JavaScript (core.js + app.js are loaded on every page)
├── data/                 # 40k_data.json (committed) and combat_sim.db (local)
├── scripts/migrate_excel.py
├── tests/                # pytest suite (conftest.py provides a temporary-data app); tests/js: Node tests
├── docs/
│   ├── architecture.md   # How the pieces fit, conventions, maintainability notes
│   └── excel-parity.md   # Feature-by-feature comparison with the spreadsheet
├── archive/              # Superseded v1 workbook
├── run.py                # Entry point
├── requirements.txt      # Runtime dependencies
└── requirements-dev.txt  # Test tools
```

## Tech stack

- **Backend**: Python, Flask, Flask-SQLAlchemy, SQLite, NumPy
- **Frontend**: vanilla JavaScript, Plotly.js (charts, loaded from a CDN)
- **Data import**: openpyxl

## License

MIT License - feel free to use and modify for your own gaming needs.

---

*This is a fan project and is not affiliated with Games Workshop. Warhammer 40,000 is a trademark of Games Workshop Ltd.*
