# Architecture

How the 40K Combat Simulator is put together, the rules the code relies on, how it is tested, and where it could be made easier to maintain. For what the app does see the [README](../README.md); for how it compares with the spreadsheet see [excel-parity.md](excel-parity.md).

## Overview

A single-process Flask app serving four server-rendered pages, each driven by vanilla JavaScript that calls a JSON API. All game data lives in one JSON file; SQLite is a working copy plus the user's saved scenarios and comparisons.

```text
 40KCombatSimulator_v2.xlsx ──(scripts/migrate_excel.py, occasional)──┐
                                                                      ▼
                                                        data/40k_data.json  ◄── every confirmed Data-page edit
                                                                      │           (save_data → write_data_file)
                                               on every start:        │
                                               rebuild_reference_tables
                                                                      ▼
 Browser pages ──fetch──► Flask routes (app.py) ──► SQLAlchemy models (models.py) ──► data/combat_sim.db
   index / matchups /            │                                                   (reference tables +
   scenarios / data              ▼                                                    saved scenarios/comparisons)
                         simulation.py  (resolve → analytical / Monte Carlo)
```

## Modules

| Module | Responsibility |
| --- | --- |
| `src/combat_sim/app.py` | `create_app()` (config, DB init, load the data JSON) and `register_routes()`: the four pages plus every JSON endpoint — data editing, simulate, rules lists, matrix, cell detail, army analysis, scenarios and comparisons. Module-level helpers: `conditions_from_request`, `selected_rules` / `rule_modifiers` (ticked rules → modifier text), `keyword_base` / `modifier_has_effect`, `default_used` (default weapon ticks), `weighted_average`. |
| `src/combat_sim/models.py` | SQLAlchemy models. Reference data: `Faction`, `Unit`, `RangedWeapon`, `MeleeWeapon`, the loadout link tables (with `qty`), `FactionRule`, `Stratagem` (core when `faction_id` is null), `Keyword`, `TargetProfile`. User data: `SavedScenario`, `SavedComparison`. `Unit.points_for(models)` implements points by unit size; `Unit.loadout()` / `set_loadout()` read and write loadouts. |
| `src/combat_sim/data_store.py` | The data JSON format (`schema_version` 1): read and validate (`read_data_file`, `check_data`), rebuild the reference tables (`rebuild_reference_tables`), export (`export_data`) and atomic write (`write_data_file`). |
| `src/combat_sim/simulation.py` | The combat engine. `resolve()` turns one weapon (abilities, type, models) and one target (stats, keywords/modifiers) into the rolls needed — the equivalent of the spreadsheet's helper columns. `simulate_analytical()` (averages, the spreadsheet's method) and `simulate_monte_carlo_combined()` (dice, several weapons rolled together, model-by-model damage allocation) both consume it. |
| `scripts/migrate_excel.py` | Builds the data JSON from the frozen v2 workbook: fixed column layout checked against headers, stable IDs, typo report, `--force`, `--output`, `--reference-only`. |
| `templates/*.html` | Jinja pages. `data.html`, `index.html`, `matchups.html`, `scenarios.html` each carry an inline script or load one from `static/js`. |
| `static/js/app.js` | Loaded on **every** page. Shared: `PageState` (tab-switch persistence tagged with the server's boot ID) and `KeywordHints` (as-you-type keyword warnings). The rest is the Simulator page (returns early elsewhere). |
| `static/js/matchups.js` | The Matchups page (IIFE): attacker/defender tables, unit sizes and points, generic profiles, rules panels, matrix, cell detail, army analysis, saved comparisons. |
| `static/css/style.css` | All styles, one file, dark theme via CSS variables. |

## Data model and the JSON file

- **Source of truth:** `data/40k_data.json`. Record keys are the database column names, so loading and exporting are generic (`SECTIONS` in `data_store.py`).
- **Reference vs user data:** the tables in `REFERENCE_TABLES` are dropped and rebuilt from the JSON on every start, so their schema can change freely. `saved_scenarios` and `saved_comparisons` are *not* rebuilt; changing their columns needs a manual `ALTER TABLE`.
- **Write-through:** every data-editing route ends with `save_data()` (commit, then export the reference tables to the JSON). A new write route must call it.
- **Read-only sections:** `keywords` and `target_profiles` have no editing routes; they change only through `migrate_excel.py --reference-only`.
- **Stable IDs:** saved scenarios/comparisons refer to units and weapons by ID; the migration script reuses IDs by name so rebuilds don't break them.
- **Weapons belong to a faction**; a unit's loadout links its own faction's weapons with a quantity (models carrying it).

## Simulation engine

- **Excel parity is the contract.** `resolve()` mirrors the Sim sheets' helper columns: keywords and modifiers come from the weapon's abilities plus ticked attacker rules (attack side) and the target's keywords, modifiers, ticked target rules and extra modifiers (target side). Hit and wound modifiers are capped at ±1; BS/WS modifiers are not; cover worsens BS (11th edition); Lance/Cleave are melee-only, Heavy/Rapid Fire/Melta/cover/Stealth shooting-only.
- **Models killed** (analytical): each attack's damage is capped at the target's wounds; attacks per kill = ⌈W ÷ capped damage⌉ adjusted for Feel No Pain. A unit's weapons' kills add up, capped at the target's size; army totals add units' kills (uncapped, for "% destroyed").
- **Monte Carlo** rolls the same resolved rolls, caps each damage roll at W, and allocates damage model by model, giving exact kill and wipe chances.
- **Adding a keyword:** add it to `resolve()` (and both engines if it changes how rolls are made), add a case to `tests/test_simulation.py` and to the Monte Carlo agreement cases, and make sure the workbook's Keywords table (or `--reference-only` data) lists it.

## Conventions

- Say **"modifier"**, never "mod", in UI text and identifiers.
- Tests and harnesses must point `DATA_JSON` and `DATABASE_URL` at temporary copies (`tests/conftest.py` does this).
- Names from the workbook are imported exactly as written; likely typos are reported, not corrected.
- The same rule is sometimes implemented on both sides of the wire (see *Duplicated logic* below) — change both together.

## Testing

`python -m pytest tests` (83 tests, about 30–80 seconds). Every API test starts the app on its own temporary data file via the `make_client` fixture.

| File | Covers |
| --- | --- |
| `test_simulation.py` | Engine: the spreadsheet's own cached Sim results (parity fixtures), every keyword and modifier, damage cap and models killed, analytical vs Monte Carlo agreement |
| `test_rules_api.py` | Rules/stratagems lists and application (side, faction, phase); multi-weapon `/api/simulate` |
| `test_roster.py` | Points by unit size, generic profiles, army analysis (summary, gaps, roles, coverage, reverse durability), extra modifiers |
| `test_data_api.py` | Data editing routes, write-through to the JSON, reload round trip, import/export, cascades, pages render |
| `test_scenarios_api.py` | Saved scenarios (including re-running them) and comparisons |
| `test_migrate_excel.py` | The migration script against the real workbook: contents, normalisation, stable IDs, overwrite protection, `--reference-only` |

Line coverage (`python -m coverage run --source=src/combat_sim,scripts -m pytest tests && python -m coverage report`), October 2026:

| Module | Coverage |
| --- | --- |
| `app.py` | 97% |
| `models.py` | 96% |
| `simulation.py` | 96% |
| `data_store.py` | 94% |
| `scripts/migrate_excel.py` | 90% |
| **Total** | **95%** |

**Not covered by automated tests:** the browser JavaScript (about 2,500 lines across `app.js`, `matchups.js` and the inline page scripts). During development it was checked with headless-browser harnesses and Node checks of individual functions, but none of that is in the repo.

## Maintainability review

### Fixed in this review

- **Scenarios page bug:** `GET /api/scenarios` was defined twice; the first, short version won, so the Scenarios page received no attacker/defender setup (names showed "Unknown" and loading a scenario lost its weapons). The duplicate is removed and a regression test added.
- **Dead endpoint:** `/api/weapons` (search weapons by unit) was no longer used by any page and has been removed.
- **Deprecated SQLAlchemy calls:** `Model.query.get()` / `get_or_404()` replaced with `db.session.get()` / `db.get_or_404()`.
- **Unused dependencies:** `pandas` and the Python `plotly` package removed from `requirements.txt` (charts use Plotly.js from a CDN); test tools moved to `requirements-dev.txt`.
- **Test gaps:** data editing, scenarios/comparisons and the migration script had little or no coverage (total was 65%); new test files bring it to 95%, and all API tests share one temporary-data fixture.

### Recommendations (not done)

In rough priority order:

1. **Split `app.py` into blueprints** (about 1,300 lines in one `register_routes` closure). Natural seams: `pages`, `data_api` (editing + export/import), `simulate` (Simulator + rules lists), `matchups` (matrix, cell detail, army analysis), `saved` (scenarios, comparisons). The helpers that close over `app` (`save_data`, `attacker_profile`, `defender_profile`) would move to a small service module.
2. **Add JavaScript tests.** The highest-value targets are pure functions that mirror server logic (`unitPoints`, `defaultIncluded` / `applyDefaultUse`, `KeywordHints.base`) and the request builders (`buildMatrixRequest`). Node's built-in test runner needs no dependencies.
3. **Move the Data page script out of the template.** `data.html` is about 1,350 lines, most of it inline JavaScript; a `static/js/data.js` would match the other pages and make it testable.
4. **Remove duplicated logic** (see below), or at least keep it covered by tests on both sides.
5. **Add a browser smoke test** (e.g. Playwright) for the main flows once a headless browser is reliably available; the Edge-based harnesses used during development were not kept.
6. **Split `style.css`** (about 2,700 lines) per page or component.

### Duplicated logic

These rules exist in more than one place and must change together:

| Rule | Python | JavaScript |
| --- | --- | --- |
| Default weapon ticks (only the first "Weapon - Profile" variant) | `app.default_used` | `applyDefaultUse` (app.js), `defaultIncluded` (matchups.js) |
| Points for a unit size (brackets, round half up) | `Unit.points_for` | `unitPoints` (matchups.js) |
| Keyword name from an entry (`Anti-…`, trailing values) | `app.keyword_base`, `migrate_excel.keyword_base` | `KeywordHints.base` (app.js), `keywordBase` (data.html) |
