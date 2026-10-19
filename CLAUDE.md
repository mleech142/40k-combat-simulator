# 40K Combat Simulator

Flask app in `src/combat_sim/`. Entry point: `python run.py` (waitress on 127.0.0.1:5000, or a free port; `--debug` for Flask's debugger — never ship debug on).

**Terminology:** always write "modifier", never "mod", in UI text and code identifiers (e.g. `extra_modifiers`, "Extra modifiers").

## Data

Two sources, merged into the reference tables on every start (`create_app()` → `data_store.merge_data` → `rebuild_reference_tables`):

- **BSData catalogues** ([BSData/wh40k-11e](https://github.com/BSData/wh40k-11e)): factions, units, weapons, loadouts, points. Users get the `.json` files themselves on the Setup page (`/setup`): "Download latest" (`bsdata_download.py`, GitHub zipball into the `bsdata` folder next to the app data JSON) or a folder they choose (native dialog in `folder_dialog.py`). The folder in use is the one saved in `settings.json` (`settings.py`), else `BSDATA_DIR`, else that `bsdata` folder. **Never commit or ship them** (`data/bsdata/` is gitignored). `src/combat_sim/bsdata.py` imports them; the result is cached in `bsdata_cache.json` until the files change (bump `bsdata.IMPORTER_VERSION` when the importer's output changes). Open import findings: `docs/bsdata-import-findings.md`. BSData text carries markup (`**bold**`, `^^keyword^^`, bullets): keep it in stored text, parse with `bsdata.plain_text`, render with `Core.formatRuleText` (never insert it as raw HTML).
- **App data JSON** (`data/app_data.json`, committed and shipped): everything the app owns — custom factions with their units and weapons, faction rules and stratagems, modifiers added to BSData units (`unit_modifiers`), the keyword glossary and target profiles. Format and loading live in `src/combat_sim/data_store.py` (`schema_version` 2, record keys = DB column names; version 1 files load as all-custom data).

Rules:

- BSData factions' units and weapons are **read-only** (the routes return 403); their rules, stratagems and added modifiers are app data.
- **Detachments** (and factions' army rules) come from BSData as text only (`Detachment` table, `/api/detachments`). A rule or stratagem with a `detachment_key` (the detachment's BSData ID) is offered by `/api/rules/available` only when that detachment is chosen (`?detachment=`); ticking still decides what applies.
- A custom weapon with `linked_bsdata_id` takes its stats from that BSData profile on every start (`data_store.LINKED_FIELDS`); its name and faction are its own. Units and weapons can only be created in custom factions (`Faction.source == 'custom'`).
- App data refers to BSData by BSData ID (`faction_key` = catalogue ID, `unit_modifiers[].bsdata_id`), never by database ID. BSData records get database IDs from `BSDATA_ID_BASE` (1,000,000) up via the persistent `stable_ids` table, so saved scenarios survive re-imports; custom records keep IDs below it (`next_custom_id`).
- Reference tables are dropped and rebuilt on every start, so their schema changes need no DB migration. Saved scenarios, comparisons and `stable_ids` are *not* rebuilt; changing their columns still needs a manual `ALTER TABLE`.
- Every Data-tab write commits and then rewrites the app data JSON (`save_data()` in `app.py`). Any new write route must call it.
- Tests/harnesses must set `DATA_JSON` and `DATABASE_URL` (and `BSDATA_DIR`) to scratch copies, or edits will overwrite the real files. With `DATA_JSON` set, the BSData folder defaults to its folder, never the repo's.

**Weapons belong to a faction** (`faction_id`). A unit's loadout is the `unit_ranged_weapons` / `unit_melee_weapons` link tables with a `qty` = models in the unit carrying that weapon and a `position` (datasheet order); read it with `Unit.loadout(type)`, write with `Unit.set_loadout(type, entries)` (the relationships are view-only). BSData units also list wargear options (`ranged_options` / `melee_options`), which the Simulator and Matchups show unticked under the loadout (`Core.simulatorWeapons` / `Core.matchupWeapons`). Matchup attackers carry the attacking `unit_id` alongside `weapon_id`.

## Packaging and releases

Users get a Windows exe built by PyInstaller (`combat_sim.spec`) and published by `.github/workflows/release.yml` when a `vX.Y.Z` tag matching `__version__` in `src/combat_sim/__init__.py` is pushed. In the exe, data lives in `%APPDATA%\40K Combat Simulator\` (`src/combat_sim/paths.py`); the shipped app data JSON only seeds it on first run, and a version 1 `40k_data.json` and database found there are moved to `archive-v1`. Anything new the pages load must be bundled locally (no CDNs) and anything new on disk must be added to the spec's `datas`. Steps: `docs/releasing.md`.

## Excel Source

The v2 workbook `40KCombatSimulator_v2.xlsx` is frozen; it is now only the simulation engine's parity reference. The v1 data JSON, its database and the old `migrate_excel.py` are in `archive/`.

## Simulation engine

`src/combat_sim/simulation.py` mirrors the v2 sheet's formulas: `resolve()` turns a weapon (abilities + type) and target (keywords/modifiers) into rolls needed, then the analytical and Monte Carlo engines both consume it. Any new keyword must go in `resolve()` and be covered by both engines. Tests: `python -m pytest tests` (includes Excel's own cached results as parity fixtures and an analytical/Monte Carlo agreement check). Feature-by-feature status against the workbook: `docs/excel-parity.md`.

## Routes

- `/` - Single combat simulator
- `/matchups` - Attacker units × defender units matrix + Army Analysis
- `/scenarios` - Saved scenarios
- `/setup` - Choose, download or re-import the BSData folder; import notes. `/` redirects here until BSData loads or setup is skipped
- `/data` - BSData import status; factions (with rules and stratagems), units and weapons (BSData read-only, custom editable); read-only Keywords and Target Profiles

API routes in `app.py` under `register_routes()`. `static/js/app.js` is loaded on every page (shared `PageState`, `KeywordHints`). `/api/units` and `/api/data/units` leave Legends units out unless `?legends=1`.

## Testing

`pip install -r requirements-dev.txt`, then `python -m pytest tests` (also runs the Node tests in `tests/js` via `test_javascript.py`). API tests use the `make_client(data, bsdata=None)` fixture in `tests/conftest.py`, which runs the app on a temporary data JSON and database — use it for any new API test. Pass `bsdata=BSDATA_FIXTURES` for the made-up catalogues in `tests/fixtures/bsdata` (regenerate them with `tests/fixtures/make_bsdata_fixtures.py`; never put real BSData in tests).

**Game rules live only in Python.** Pages get them via the API (`default_use` and `points_by_model_count` on `/api/units/<id>`, `POST /api/keywords/check`); never re-implement them in JavaScript. Pure page logic belongs in `static/js/core.js` (loaded first on every page, `require()`-able in Node) with tests in `tests/js/core.test.js`.

Module map, invariants, coverage and the maintainability backlog: `docs/architecture.md`.
