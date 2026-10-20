# Technical overview

For anyone working on the code: how to run and test it, where the data comes from, how the pieces fit, and how releases are made. For using the app, see the [user guide](user-guide.md).

**Contents:** [At a glance](#at-a-glance) · [Run from source](#run-from-source) · [Tests](#tests) · [Data](#data) · [Architecture](#architecture) · [Project structure](#project-structure) · [Conventions](#conventions) · [Releases](#releases)

---

## At a glance

| | |
| --- | --- |
| **What** | A local web app (Flask) for Warhammer 40,000 11th edition combat maths, army lists and army analysis |
| **Backend** | Python 3.10+, Flask, Flask-SQLAlchemy, SQLite, NumPy, waitress |
| **Frontend** | Server-rendered Jinja pages with vanilla JavaScript; Plotly.js and Font Awesome Free 7.3.1 bundled in `static/vendor` (works offline) |
| **Game data** | [BSData wh40k-11e](https://github.com/BSData/wh40k-11e) (units, weapons, points, wargear, enhancements) and the [Wahapedia data export](https://wahapedia.ru/wh40k11ed/the-rules/data-export) (stratagems and rules), each downloaded by the user, never committed or shipped |
| **Maths** | Mirrors the original `40KCombatSimulator_v2.xlsx` formula for formula ([parity notes](excel-parity.md)); analytical and Monte Carlo engines |
| **Shipping** | Windows exe built by PyInstaller, published by GitHub Actions on a version tag |

---

## Run from source

```bash
pip install -r requirements.txt
python run.py              # --debug for Flask's debugger while developing (never in a release)
```

The browser opens <http://127.0.0.1:5000> (or the next free port; the console says which). The app only listens on this computer. From source it reads and writes `data/` in the repository; the exe uses `%APPDATA%\40K Combat Simulator\` instead (`paths.py`).

Shortcuts: `launcher.bat` (Windows) or `./launcher.sh` (Linux/Mac) create a virtual environment, install the requirements and start the app.

On first run the app opens **Setup**: download the BSData catalogues and the Wahapedia export there.

---

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests                       # Python and (with Node 18+) the JavaScript tests
node --test "tests/js/*.test.js"             # JavaScript only
python -m coverage run --source=src/combat_sim -m pytest tests && python -m coverage report
```

- **Isolated data:** tests never touch `data/`. Each starts the app on its own temporary data file (`make_client` in `tests/conftest.py`).
- **Fixtures:** made-up BSData catalogues and Wahapedia files, from `tests/fixtures/make_bsdata_fixtures.py` and `make_wahapedia_fixtures.py`. Never put real game data in tests.
- **Parity:** the spreadsheet's cached results are fixtures, and analytical and Monte Carlo are checked against each other.
- **JavaScript:** tests cover the pure page helpers in `static/js/core.js`.

---

## Data

| Source | Holds | Where |
| --- | --- | --- |
| **BSData catalogues** | Factions, datasheets, weapons, loadouts, points by unit size, wargear option trees, detachments (with Detachment Points, dispositions, Unique Tags), enhancements | Downloaded on Setup into `bsdata/`; imported by `bsdata.py`, cached in `bsdata_cache.json` |
| **Wahapedia export** | Stratagems, detachment rules, army rules (text) | Downloaded on Setup into `wahapedia/`; imported by `wahapedia.py` on every start |
| **App data JSON** | Custom factions, units and weapons; hand-written rules; edits to how Wahapedia's rules are read; modifiers added to BSData units; leader-buff edits; keywords; benchmark target profiles | `data/app_data.json`: committed and shipped; every Data-tab edit is written back |
| **Database** | A working copy of all of the above (rebuilt on start) plus saved scenarios, comparisons, armies and stable BSData IDs | `combat_sim.db` (SQLite) |

BSData records keep stable database IDs across re-imports (the `stable_ids` table), so saved scenarios and armies survive updates. Reference tables are rebuilt on every start, so their schema can change freely; persistent tables only gain new nullable columns automatically.

---

## Architecture

```text
 BSData *.json ─► bsdata.py ──────────┐
 Wahapedia *.csv ─► wahapedia.py ─────┼─► data_store.merge_data ─► SQLite (reference tables)
 data/app_data.json ──────────────────┘                                │
                                                                       ▼
 Browser pages ──fetch──► Flask routes (app.py) ──► simulation.py (resolve → analytical / Monte Carlo)
                                   │
                                   ├─► armies.py   (army lists: pricing and checks)
                                   ├─► wargear.py  (wargear choices on BSData's option trees)
                                   └─► rules_text.py (rule text → modifiers)
```

| Module | Does |
| --- | --- |
| `app.py` | App factory and every route (pages and JSON API) |
| `simulation.py` | The combat engine: `resolve()` turns a weapon and target into rolls; analytical and Monte Carlo engines, attached units |
| `bsdata.py` | BSData importer: units, points, loadouts, leaders, detachments, enhancements, frozen wargear trees |
| `wahapedia.py` | Wahapedia importer and matching to BSData factions and detachments |
| `rules_text.py` | Reads rule text into modifiers (leaders, stratagems, detachment and army rules, enhancements) |
| `armies.py` | Army lists: points, Detachment Points, unit limits, leaders and support units, Warlord, enhancements, Analytics roster |
| `wargear.py` | Wargear choices evaluated with the importer's own selection, points and condition code |
| `data_store.py` | App data JSON format; merging BSData, Wahapedia and app data; rebuilding tables; export |
| `models.py` | SQLAlchemy models |
| `static/js/core.js` | Pure page helpers (tested in Node); `app.js`, `analytics.js`, `armies.js` drive the pages |

The full module map, invariants and maintainability notes are in [architecture.md](architecture.md).

---

## Project structure

```text
40k-combat-simulator/
├── src/combat_sim/          # The app (modules above), templates/ and static/
├── data/                    # app_data.json (committed); downloads, cache and database (local only)
├── tests/                   # pytest suite, fixtures/ (made-up game data), js/ (Node tests)
├── docs/                    # This overview, the user guide, architecture, parity, releasing, images/
├── archive/                 # Version 1 workbook, data and migration script
├── .github/workflows/       # release.yml: builds and publishes the Windows release on a version tag
├── run.py                   # Entry point (waitress; --debug for development)
├── combat_sim.spec          # PyInstaller build
└── requirements*.txt        # Runtime / test and build dependencies
```

---

## Conventions

- **Game rules live in Python only.** Pages get them from the API (points tables, keyword checks, army pricing, wargear forms); never re-implement a rule in JavaScript.
- **Pure page logic** goes in `static/js/core.js`, with a test in `tests/js/core.test.js`.
- **Every Data-tab write** commits and then rewrites the app data JSON (`save_data()`); any new write route must call it.
- **New engine features** go through `resolve()`, both engines, and must work for attached units.
- **Never commit or ship game data:** `data/bsdata/` and `data/wahapedia/` are gitignored. Keep the Wahapedia credit on the pages.
- **Wording:** say "modifier", never "mod", in UI text and identifiers.
- **Bundling:** anything new the pages load must be bundled locally (no CDNs), and anything new on disk added to `combat_sim.spec`.

More detail for contributors is in [`CLAUDE.md`](../CLAUDE.md).

---

## Releases

1. Bump `__version__` in `src/combat_sim/__init__.py`.
2. Add release notes in `docs/release-notes/vX.Y.Z.md`.
3. Commit, tag `vX.Y.Z` and push.

GitHub Actions then runs the tests, builds the exe and publishes the release. Full steps and how the build works: [releasing.md](releasing.md).

---

*MIT licensed. A fan project, not affiliated with Games Workshop. Warhammer 40,000 is a trademark of Games Workshop Ltd.*
