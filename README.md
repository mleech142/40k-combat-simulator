# 40K Combat Simulator

A local web application for simulating Warhammer 40,000 (11th edition) combat. It works out the expected damage of a unit's weapons against a target, compares whole armies in a matchup grid, and analyses where an army is strong or weak — with both fast analytical averages and Monte Carlo dice simulations.

It replaces the `40KCombatSimulator_v2.xlsx` spreadsheet: its maths is reproduced formula for formula (and checked against the spreadsheet's own results in the tests). See [docs/excel-parity.md](docs/excel-parity.md) for the feature-by-feature comparison.

**Unit data comes from [BSData](https://github.com/BSData/wh40k-11e)**, the community-maintained catalogues behind BattleScribe and New Recruit: every faction, datasheet, weapon, wargear option and points cost. The app doesn't include it; you download your own copy from inside the app (one click). BSData is not endorsed by Games Workshop, and neither is this app.

## Features

- **Simulator** — one attacking unit against one target: tick any of the unit's weapons (with how many models carry each), swap in wargear options, attach a leader to either unit (its weapons join the attacker; its buffs apply to the unit it leads), set battle conditions, pick each side's detachment and tick faction rules and stratagems, and add one-off *extra modifiers* to the target. Shows per-weapon results, totals and (in Monte Carlo mode) a damage distribution with kill and wipe chances.
- **Analytics** — your army against an opponent's real units or generic benchmark profiles (MEQ, TEQ, vehicles…): a heat-map grid per unit and for the whole army, unit sizes with correct points, army-wide rules and stratagems, and an **Army Analysis** panel (summary, gaps, role scores with a spider chart, Toughness/Save coverage, reverse durability).
- **Data** — browse every BSData faction, unit and weapon (read-only), with each faction's army and detachment rules as reference text. Write the faction rules and stratagems the maths uses (for the whole faction or one detachment), add modifiers to BSData units, and build your own units and weapons in custom factions (copying BSData weapons if you like). Read-only reference tabs list every rules keyword and the benchmark target profiles. Every confirmed change is saved straight to the app data file.
- **Setup** — download the latest BSData catalogues, point the app at a folder of them, or re-import; see what the import couldn't read.
- **Scenarios** — save Simulator setups, compare and re-run them.
- **Rules coverage** — every keyword and stat modifier the spreadsheet models: Lethal/Sustained Hits, Devastating Wounds, Anti-X, Blast, Cleave, Melta, Rapid Fire, Torrent, Twin-Linked, Lance, Heavy, Psychic, Ignores Cover, Stealth, Feel No Pain, Critical Hits/Wounds X+, Snap Shooting, re-rolls, and Hit/Wound/AP/Damage/Attacks/Strength/Toughness/Save/BS/WS modifiers. The **Data → Keywords** tab lists them all with the exact format to write.

## Installation

### Download (Windows, no Python needed)

1. Download `40kCombatSimulator-vX.Y.Z-windows.zip` from the [Releases page](https://github.com/mleech142/40k-combat-simulator/releases).
2. Unzip it anywhere and double-click `40kCombatSimulator.exe`. Windows may warn about an unknown publisher (the exe isn't code-signed): choose **More info → Run anyway**.
3. Your browser opens the app on its **Setup** page. Click **Download latest** to fetch the BSData catalogues (about 50 MB, a few seconds), or choose a folder of them you already have. Then **Continue to the Simulator**.
4. Close the console window to stop the app.

Your data lives in `%APPDATA%\40K Combat Simulator\`, not in the unzipped folder, so you can replace the folder with a newer release without losing anything: the downloaded catalogues (`bsdata\`), the app data file with your rules, stratagems and custom units, your settings, and your saved scenarios and comparisons. On first run that folder is filled with the app data shipped in the release; later releases never overwrite it. To update the unit data, open **Setup** and click **Download latest** again: units keep their identities, so saved scenarios and comparisons keep working.

**Upgrading from 1.x:** version 2 no longer uses the 1.x data file. On first start the old `40k_data.json` and its database (with the 1.x saved scenarios and comparisons, whose units no longer exist) are moved to `%APPDATA%\40K Combat Simulator\archive-v1\`.

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

The launchers create a virtual environment, install the requirements and start the app. On first run the app opens its **Setup** page: click **Download latest** to fetch the BSData catalogues from GitHub, or choose a folder of them you already have.

## Using the app

### Simulator

1. Pick an attacking unit (tick **Show Legends** to include Legends units); its loadout appears with **Use** ticks and a **Qty** (models carrying each weapon), followed by its **wargear options**, unticked. Alternative profiles of one gun ("Plasma gun - standard / supercharge") start with only the first ticked.
2. Choose **Shooting** or **Melee**, pick a defender and its model count.
3. Set conditions (half range, stationary, cover, charged, attacker damaged, re-rolls), choose each side's **detachment** and tick rules and stratagems, and optionally type **Extra modifiers** for the target (e.g. `Stealth, Feel No Pain 5+`).
4. **Run Simulation**. Tick **Monte Carlo** for a dice-rolled distribution.

### Analytics

1. **+ Add Unit** to the Attackers table (its loadout is added, plus its wargear options, excluded until you tick them; a unit can be added more than once). Set each unit's **Size** — points and weapon quantities follow. Pick a **leader** for a unit to add its weapons, points and buffs; defenders can have a leader too (its defensive buffs apply).
2. Switch Defenders between **Real units** (with per-unit model counts and extra modifiers) and **Generic profiles**.
3. Choose each side's detachment, tick army-wide rules and stratagems, set conditions, then **Run Matrix**.
4. Change the **Display** metric (damage, models killed, % destroyed, points efficiency…), toggle **Show weapons**, click any cell for a Monte Carlo breakdown, and read the **Army Analysis** tabs.
5. **Save / Load Comparison** to keep a setup.

Both pages remember their setup while you move between tabs; it is cleared when the app restarts.

### Rules and stratagems

**Leaders:** the "While this model is leading a unit, …" text of each BSData leader is read into buffs where it says something the maths models (weapon abilities, +1 to hit or wound, re-rolls, critical hits on 5+, invulnerable saves, Feel No Pain, -1 to be hit…); check or change them under **Leader buffs** in the unit's view on the Data tab. A defender with a leader is attacked as one attached unit: every attack goes to the bodyguard models until they are all destroyed, then to the leader, each with its own stats; results show the bodyguards killed and, for the leader, the expected damage to it (analytical) or how often it dies (Monte Carlo). Precision isn't modelled.

BSData describes rules in words; it has no stratagems and nothing the maths can use. The calculations use the faction rules and stratagems written on the **Data** tab, in the keyword format the **Keywords** tab lists (e.g. `Sustained Hits 1, Lance`). The app ships with the core stratagems; add your armies' rules and stratagems there, each for the whole faction or for one detachment (BSData's own text for each army rule and detachment is shown alongside to write from).

## Data

| File | What it holds |
| --- | --- |
| `data/bsdata/*.json` | **Unit data** — the BSData catalogues from [github.com/BSData/wh40k-11e](https://github.com/BSData/wh40k-11e): factions, units, weapons, loadouts and points. Get them on the Setup page (or use another folder chosen there); they are never committed or shipped, and are read-only in the app. |
| `data/settings.json` | The BSData folder chosen on the Setup page and the last download. Not committed. |
| `data/app_data.json` | **The app's own data** — your custom factions, units and weapons, faction rules and stratagems, modifiers added to BSData units, keywords and target profiles. Committed and distributed; the app writes every edit back to it. |
| `data/bsdata_cache.json` | The last BSData import, reused until the catalogue files change. Not committed. |
| `data/combat_sim.db` | SQLite working copy (rebuilt on start) plus your saved scenarios and comparisons. Not committed. |
| `40KCombatSimulator_v2.xlsx` | The original spreadsheet; now only the simulation engine's reference. Frozen. |

The Data page shows whether the BSData folder loaded. BSData units keep their IDs when the catalogues are updated, so saved scenarios and comparisons keep pointing at the right units.

**Export JSON / Import JSON** on the Data page download or replace the app data file (with a review screen before replacing); BSData is not part of it.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests                                         # 207 tests (Python + JavaScript)
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
│   ├── templates/        # Jinja2 pages (Simulator, Analytics, Scenarios, Data)
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
