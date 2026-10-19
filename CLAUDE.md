# 40K Combat Simulator

Flask app in `src/combat_sim/`. Entry point: `python run.py` (waitress on 127.0.0.1:5000, or a free port; `--debug` for Flask's debugger — never ship debug on).

**Terminology:** always write "modifier", never "mod", in UI text and code identifiers (e.g. `extra_modifiers`, "Extra modifiers").

## Data

**`data/40k_data.json` is the source of truth** for factions, units, weapons, loadouts, faction rules and stratagems. It is committed and distributed. Format and loading live in `src/combat_sim/data_store.py` (`schema_version`, record keys = DB column names).

- On every start, `create_app()` drops and rebuilds the reference tables (`REFERENCE_TABLES` in `models.py`) from the JSON. Reference-table schema changes therefore need no DB migration; bump `SCHEMA_VERSION` if the JSON format changes incompatibly.
- Every Data-tab write commits and then rewrites the JSON (`save_data()` in `app.py`). Any new write route must call it.
- SQLite (`data/combat_sim.db`) otherwise only keeps saved scenarios and comparisons. Those tables are *not* rebuilt; changing their columns still needs a manual `ALTER TABLE`.
- Tests/harnesses must set `DATA_JSON` (and `DATABASE_URL`) to scratch copies, or edits will overwrite the real data file.

**Weapons belong to a faction** (`faction_id`). A unit's loadout is the `unit_ranged_weapons` / `unit_melee_weapons` link tables with a `qty` = models in the unit carrying that weapon; read it with `Unit.loadout(type)`, write with `Unit.set_loadout(type, entries)` (the relationships are view-only). Matchup attackers carry the attacking `unit_id` alongside `weapon_id`.

## Packaging and releases

Users get a Windows exe built by PyInstaller (`combat_sim.spec`) and published by `.github/workflows/release.yml` when a `vX.Y.Z` tag matching `__version__` in `src/combat_sim/__init__.py` is pushed. In the exe, data lives in `%APPDATA%\40K Combat Simulator\` (`src/combat_sim/paths.py`); the shipped JSON only seeds it on first run. Anything new the pages load must be bundled locally (no CDNs) and anything new on disk must be added to the spec's `datas`. Steps: `docs/releasing.md`.

## Excel Source

`scripts/migrate_excel.py` builds the JSON from `40KCombatSimulator_v2.xlsx` (fixed v2 layout, checked against row 3 headers). The workbook is frozen; the app is now the place to edit data. The script refuses to overwrite an existing JSON without `--force`, keeps IDs stable by name, and prints a report of likely typos and skipped loadout rows. The v1 workbook is in `archive/`.

`--reference-only` refreshes just the read-only reference sections (keywords, generic target profiles, role benchmarks) of an existing JSON without touching app edits; the app cannot edit those sections.

## Simulation engine

`src/combat_sim/simulation.py` mirrors the v2 sheet's formulas: `resolve()` turns a weapon (abilities + type) and target (keywords/modifiers) into rolls needed, then the analytical and Monte Carlo engines both consume it. Any new keyword must go in `resolve()` and be covered by both engines. Tests: `python -m pytest tests` (includes Excel's own cached results as parity fixtures and an analytical/Monte Carlo agreement check). Feature-by-feature status against the workbook: `docs/excel-parity.md`.

## Routes

- `/` - Single combat simulator
- `/matchups` - Attacker units × defender units matrix + Army Analysis
- `/scenarios` - Saved scenarios
- `/data` - Edit factions (with rules and stratagems), units and weapons; read-only Keywords and Target Profiles

API routes in `app.py` under `register_routes()`. `static/js/app.js` is loaded on every page (shared `PageState`, `KeywordHints`).

## Testing

`pip install -r requirements-dev.txt`, then `python -m pytest tests` (also runs the Node tests in `tests/js` via `test_javascript.py`). API tests use the `make_client(data)` fixture in `tests/conftest.py`, which runs the app on a temporary data JSON and database — use it for any new API test.

**Game rules live only in Python.** Pages get them via the API (`default_use` and `points_by_model_count` on `/api/units/<id>`, `POST /api/keywords/check`); never re-implement them in JavaScript. Pure page logic belongs in `static/js/core.js` (loaded first on every page, `require()`-able in Node) with tests in `tests/js/core.test.js`.

Module map, invariants, coverage and the maintainability backlog: `docs/architecture.md`.
