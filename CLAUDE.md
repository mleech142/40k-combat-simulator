# 40K Combat Simulator

Flask app in `src/combat_sim/`. Entry point: `python run.py` (port 5000).

**Terminology:** always write "modifier", never "mod", in UI text and code identifiers (e.g. `extra_modifiers`, "Extra modifiers").

## Data

**`data/40k_data.json` is the source of truth** for factions, units, weapons, loadouts, faction rules and stratagems. It is committed and distributed. Format and loading live in `src/combat_sim/data_store.py` (`schema_version`, record keys = DB column names).

- On every start, `create_app()` drops and rebuilds the reference tables (`REFERENCE_TABLES` in `models.py`) from the JSON. Reference-table schema changes therefore need no DB migration; bump `SCHEMA_VERSION` if the JSON format changes incompatibly.
- Every Data-tab write commits and then rewrites the JSON (`save_data()` in `app.py`). Any new write route must call it.
- SQLite (`data/combat_sim.db`) otherwise only keeps saved scenarios and comparisons. Those tables are *not* rebuilt; changing their columns still needs a manual `ALTER TABLE`.
- Tests/harnesses must set `DATA_JSON` (and `DATABASE_URL`) to scratch copies, or edits will overwrite the real data file.

**Weapons belong to a faction** (`faction_id`). A unit's loadout is the `unit_ranged_weapons` / `unit_melee_weapons` link tables with a `qty` = models in the unit carrying that weapon; read it with `Unit.loadout(type)`, write with `Unit.set_loadout(type, entries)` (the relationships are view-only). Matchup attackers carry the attacking `unit_id` alongside `weapon_id`.

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

`pip install -r requirements-dev.txt`, then `python -m pytest tests`. API tests use the `make_client(data)` fixture in `tests/conftest.py`, which runs the app on a temporary data JSON and database — use it for any new API test. The browser JavaScript has no automated tests.

Module map, invariants, coverage and the maintainability backlog (including rules duplicated between Python and JavaScript that must change together): `docs/architecture.md`.
