# Architecture

How the 40K Combat Simulator is put together, the rules the code relies on, how it is tested, and where it could be made easier to maintain. For what the app does see the [README](../README.md); for how it compares with the spreadsheet see [excel-parity.md](excel-parity.md).

## Overview

A single-process Flask app serving four server-rendered pages, each driven by vanilla JavaScript that calls a JSON API. Game data comes from three places: the BSData catalogues the user downloads (read-only units, weapons, points), the Wahapedia export the user downloads (stratagems, detachment and army rules, with their text) and the app's own data JSON (custom units, rules and stratagems, edits to Wahapedia's, references). SQLite is a working copy of all three, plus the user's saved scenarios and comparisons and the map of stable BSData IDs.

```text
 BSData wh40k-11e *.json ──(bsdata.import_folder; cached in bsdata_cache.json)──┐
   (user downloads into the bsdata folder)                                      │
 Wahapedia export *.csv ──(wahapedia.import_folder; rules_text.parse_effects)───┤
   (user downloads into the wahapedia folder)                                   │
                                                                                ▼
 data/app_data.json ──────────────────────────────────────────────► data_store.merge_data ──► rebuild_reference_tables
   ▲ every confirmed Data-page edit (save_data → export_data → write_data_file)  (stable IDs)     (on every start)
   │                                                                                                  ▼
 Browser pages ──fetch──► Flask routes (app.py) ──► SQLAlchemy models (models.py) ──► data/combat_sim.db
   index / analytics /           │                                                   (reference tables, saved
   scenarios / data              ▼                                                    scenarios/comparisons, stable_ids)
                         simulation.py  (resolve → analytical / Monte Carlo)
```

## Modules

| Module | Responsibility |
| --- | --- |
| `src/combat_sim/app.py` | `create_app()` (config, DB init, load BSData and the app data JSON) and `register_routes()`: the four pages plus every JSON endpoint — data editing, simulate, rules lists, keyword checks, matrix, cell detail, army analysis, scenarios and comparisons. Module-level helpers: `conditions_from_request`, `selected_rules` / `rule_modifiers` (ticked rules → modifier text), `modifier_has_effect`, `default_used` (default weapon ticks), `weighted_average`. |
| `src/combat_sim/models.py` | SQLAlchemy models. Reference data: `Faction` (`source` custom/bsdata/missing; BSData `army_rules` text), `Detachment` (BSData, text only), `Unit`, `RangedWeapon`, `MeleeWeapon`, the loadout link tables (with `qty` and `position`), `FactionRule`, `Stratagem` (core when `faction_id` is null), `Keyword`, `TargetProfile`. Persistent: `SavedScenario`, `SavedComparison`, `SavedArmy`, `StableId` (BSData key → database ID). `Unit.points_for(models)` implements points by unit size; `Unit.loadout()` / `set_loadout()` read and write loadouts. |
| `src/combat_sim/data_store.py` | The app data JSON format (`schema_version` 2): read and validate (`read_data_file`, `check_data`); load BSData with its cache (`load_bsdata`); merge both with stable IDs, placeholder factions for rules whose BSData faction is missing, and added unit modifiers (`merge_data`); rebuild the reference tables (`rebuild_reference_tables`); export the app's own records (`export_data`) and atomic write (`write_data_file`). |
| `src/combat_sim/effects.py` | Which leader and enhancement effect rows apply to an attack (side, phase, bearer). Used by `app.py`'s `leader_modifiers` / `enhancement_modifiers` and by Play mode. |
| `src/combat_sim/play/` | Play mode (planned feature, see [play-mode-design.md](play-mode-design.md)). `dice.py` (game dice, every roll recorded), `attack.py` (a weapon's attacks to a wound pool, then each wound's save and damage, using `resolve()`), `snapshot.py` (an army frozen into a game; needs an app context), `game.py` (game state as plain data, actions to events, replay), `geometry.py` (inches: base and hull shapes, edge-to-edge distance, overlaps, terrain footprints, lines of sight), `routes.py` (`/play` and `/api/play/...`; games are `SavedGame` rows). The page is `templates/play.html` + `static/js/play.js`. Never used by the Simulator or Analytics. |
| `src/combat_sim/simulation.py` | The combat engine, plus the keyword language helpers (`keyword_name`, `check_keywords`) used by the app and the migration script. `resolve()` turns one weapon (abilities, type, models) and one target (stats, keywords/modifiers) into the rolls needed — the equivalent of the spreadsheet's helper columns. `simulate_analytical()` (averages, the spreadsheet's method) and `simulate_monte_carlo_combined()` (dice, several weapons rolled together, model-by-model damage allocation) both consume it. |
| `src/combat_sim/bsdata.py` | Importer for [BSData](https://github.com/BSData/wh40k-11e) catalogues (not wired into the app yet). `import_folder(folder)` reads the game system and catalogue JSONs and returns the reference sections of the data JSON (factions, units with default loadouts, weapon options and points for every unit size, ranged and melee weapons), each record with its `bsdata_id`, plus an import report. The module docstring sets out how BattleScribe entries map onto the app. |
| `src/combat_sim/rules_text.py` | Rule text: tidying it (`tidy_text`, `plain_text`) and reading the effects the maths models into rows like faction rules (`applies_to` / `phase` / `modifier`). `parse_leader_buffs` reads a leader's "while leading" text; `parse_effects` reads any rule (a stratagem's EFFECT, a detachment or army rule), returning the rows, the sentences it couldn't read and the conditions it dropped. |
| `src/combat_sim/wahapedia.py` | Importer for the [Wahapedia data export](https://wahapedia.ru/wh40k11ed/the-rules/data-export) (`FILES`): its `|`-separated files with unquoted line breaks (`read_table`), HTML turned into BSData-style markup (`markup`), stratagems and rules with one side, phase and modifier each (`interpret`), and `link()` matching Wahapedia's factions and detachments to BSData's by name. The module docstring sets out the choices. |
| `src/combat_sim/wahapedia_download.py` | "Download latest" for rules and stratagems: every export file fetched into a staging folder, checked by importing it, then swapped in (the old folder kept on any failure). |
| `src/combat_sim/armies.py` | Army lists: `clean_army` (request → army), `price_army` (points per unit and in total, the listed sizes, which units each leader can join; the chosen detachments' Detachment Points against the battle size's budget (`BATTLE_SIZES`: Incursion 2 or a single 3 DP detachment, Strike Force 3, Onslaught 4 as BSData has it) and shared Unique Tags (`check_detachments`); problems: points and DP limits, unlisted sizes, other factions' or missing units and detachments, leaders attached where they can't lead) and its `roster` for Analytics (one entry per unit, its leader folded in), `army_text` (plain-text export). Phase B: `check_copies` (BSData's `max_in_army`, else one Epic Hero / six Battleline or Dedicated Transport / three), `check_attachments` (leaders and support units, one of each per unit), `check_warlord`, `check_enhancements` (2 at Incursion, 4 otherwise; each once; Upgrades up to three and uncounted) and `enhancement_allowed` / `enhancement_hidden` (BSData's own conditions for an enhancement, evaluated for the bearer and chosen detachments). Wargear choices are not modelled yet. |
| `src/combat_sim/wargear.py` | Wargear choices: `FrozenEntry` (a `bsdata.Entry` over a frozen option tree, so the importer's selection, points and condition code runs on it), `build` (defaults plus `{k: count}` choices, fitted to a size by `_fit`: one model at a time, the first move that adds no problem), `check` (min/max under each parent with BSData's limit modifiers, BSData's error messages), `loadout`, `form` (the Armies popup's sections: pick one / how many / optional), `evaluate` (all of it for one unit), `default_used`. |
| `src/combat_sim/settings.py` | `settings.json` next to the app data JSON: the BSData folder chosen on the Setup page, the last downloads (BSData commit SHA and date; Wahapedia's last update) and whether setup was skipped. |
| `src/combat_sim/bsdata_download.py` | "Download latest": the newest commit of BSData/wh40k-11e from the GitHub API, its zipball, and a safe install (top-level `.json` only, staged then swapped in, the old folder kept on any failure). |
| `src/combat_sim/folder_dialog.py` | The native folder dialog (tkinter) behind the Setup page's Browse button; the server opens it because a browser can't hand over a path. |
| `src/combat_sim/paths.py` | Where files live. From source: the repository's `data/`. Packaged (`sys.frozen`): `%APPDATA%\40K Combat Simulator\`, seeded with the shipped app data JSON on first run and never overwritten; a version 1 `40k_data.json` and database there are moved to `archive-v1`. The BSData folder (`bsdata`) and import cache sit next to the app data JSON. `create_app()` uses it unless `DATA_JSON` / `DATABASE_URL` / `BSDATA_DIR` are set. |
| `run.py` | Entry point for both source and the exe: waitress on 127.0.0.1, falls back to a free port if 5000 is taken, opens the browser. `--debug` switches to Flask's debugger for development only. |
| `templates/*.html` | Jinja pages. `data.html`, `index.html`, `analytics.html`, `scenarios.html`, `setup.html` each carry an inline script or load one from `static/js`; `base.html` has the footer with the BSData credit and data version. The Setup page downloads, chooses or re-imports the BSData folder without a restart (`reload_bsdata`) and lists the import notes. The Data page shows the BSData import status, works one faction at a time on the Units and Weapons tabs, and opens BSData units read-only (only added modifiers can change). |
| `static/js/core.js` | Loaded first on **every** page (`window.Core`; `require()`-able in Node). Pure helpers with no DOM or network access: formatting, BSData rule text as safe HTML (`formatRuleText`), copy labels, unit-size scaling, the Simulator and Analytics request builders, rules filtering, the Army Analysis HTML, and page-state persistence. Unit-tested in `tests/js`. |
| `static/js/app.js` | Loaded on every page. Shared: `PageState` (from `Core.createPageState`) and `KeywordHints` (as-you-type keyword warnings, checked by the server). The rest is the Simulator page (returns early elsewhere). |
| `static/js/armies.js` | The Armies page (IIFE): the list being built, priced by the server on every change, the saved armies, the faction's units to add, text export and "Analyse as attackers/defenders" (saves, then opens Analytics with `?army=`). |
| `static/js/analytics.js` | The Analytics page (IIFE): DOM and state for the attacker/defender tables, generic profiles, rules panels, matrix, cell detail, army analysis tabs and saved comparisons; calculations and HTML building come from `Core`. |
| `static/vendor/` | Bundled third-party files: Plotly.js and Font Awesome Free 7.3.1 (solid web font, its CSS and licence). Icons are `<i class="fa-solid fa-NAME" aria-hidden="true"></i>`; a packaging test checks every icon used exists in the bundle. |
| `static/css/style.css` | All styles, one file, dark theme via CSS variables. Colours are tokens on `:root` (the "Carbon & Blaze" theme); charts read them through `themeColor()` in `app.js`, so a colour change happens there only. |

## Data model: BSData and the app data JSON

- **BSData** (read-only): the catalogues in the `bsdata` folder, imported by `bsdata.py` (see its module docstring for how BattleScribe entries map onto factions, units, loadouts and points). The import is cached in `bsdata_cache.json`, keyed by a hash of the files, `IMPORTER_VERSION` and the known keywords. A missing folder is not an error (the app runs on its own data); an unreadable one is reported on the Data page.
- **App data JSON** (`data/app_data.json`): custom factions with their units and weapons, all faction rules and stratagems, `unit_modifiers` added to BSData units, keywords and target profiles. Record keys are the database column names, so loading and exporting are generic (`SECTIONS` in `data_store.py`). Rules and stratagems of a BSData faction store its catalogue ID (`faction_key`) and name, not a database ID; added modifiers store the unit's BSData entry ID and apply to that datasheet in every faction that has it.
- **Reference vs persistent tables:** the tables in `REFERENCE_TABLES` are dropped and rebuilt on every start, so their schema can change freely. `saved_scenarios`, `saved_comparisons`, `saved_armies` and `stable_ids` are *not* rebuilt: on start `create_all` adds missing tables and `models.add_missing_columns` adds new nullable columns; any other change needs a manual `ALTER TABLE`.
- **Write-through:** every data-editing route ends with `save_data()` (commit, then export the app's own records to the JSON). A new write route must call it.
- **Read-only:** BSData factions' units and weapons (routes return 403; a BSData unit only accepts `added_modifiers`), keywords and target profiles (no editing routes).
- **Stable IDs:** BSData records get database IDs from `BSDATA_ID_BASE` up, recorded in `stable_ids` by key (`unit:<catalogue>/<entry>` and so on), so the same datasheet keeps its ID across re-imports and saved scenarios/comparisons keep working. Custom records keep their own IDs, below the base (`next_custom_id`). A rule whose BSData faction isn't loaded keeps a placeholder faction (`source` missing) with the same ID.
- **Weapons belong to a faction**; a unit's loadout links its own faction's weapons with a quantity (models carrying it) in datasheet order (`position`). BSData units also list wargear options, offered unticked on the Simulator and Analytics pages. A custom weapon can be copied from a BSData weapon and stay linked to it (`linked_bsdata_id`): its stats are refreshed from BSData on every start.
- **Leaders:** a leader's buffs (`leader_buffs`, rows like faction rules) are read from its BSData Leader text by `bsdata.parse_leader_buffs` and can be overridden in the app (`leader_buffs_override`, saved by BSData ID). `/api/units/<id>` lists the leaders that can join a unit (`Unit.leaders()`, by the leaders' `can_lead` names); requests carry a `leader_id` and the server adds that leader's buffs to the attacking weapons or the target (`leader_modifiers`), and its points in Analytics. The leader's weapons are sent like the unit's own; resizing a unit never scales them. A defender with a leader is an **attached unit**: every attack goes to the bodyguard models until they are all destroyed, then to the leader, each stage with its own profile (Toughness, saves, wounds, Feel No Pain, keywords). The analytical engine (`simulate_attached_analytical`) spends each weapon's expected kills on the bodyguards and resolves the overflowing share of its attacks against the leader; Monte Carlo (`simulate_monte_carlo_attached`) rolls attack by attack and switches profile the moment the last bodyguard falls. Results carry `bodyguards_killed`, `leader_damage` and `leader_killed` (and `leader_kill_probability` in Monte Carlo). The pages show the leader as expected damage in analytical mode and as the chance it dies in Monte Carlo, never as analytical "kills". Matrix cells for an attached defender resolve the row's weapons in order; percentages count all its models. Reverse durability treats my units with leaders the same way. Precision and the "majority Toughness" rule are not modelled.
- **Detachments** come from BSData with their rule text, Detachment Points cost and categories (read-only): a category in `bsdata.FORCE_DISPOSITIONS` is a force disposition, "3DP Detachment" is a marker, any other is a Unique Tag. Rules and stratagems can be limited to one (`detachment_key`, its BSData ID, plus its name for when it isn't loaded); the Simulator and Analytics have a detachment checklist per side (`Core.detachmentPickerHtml`; several can be chosen), and `/api/rules/available?detachment=` (repeatable) offers faction-wide entries plus those of the chosen detachments. The Data tab shows each faction's BSData army rules and detachment rules as reference text for writing rules and stratagems.
- **Wahapedia** (read-only text): the export in the `wahapedia` folder, imported on every start (no cache; it takes well under a second). Its stratagems and rules become `Stratagem` / `FactionRule` rows with `source` wahapedia, their `text` and `parse` (status, sentences not read, conditions dropped, and what was parsed), copied into every BSData faction they match (`wahapedia.link`: by detachment name, so Space Marine chapters get Space Marines' rules) with stable IDs (`wahapedia:<kind>:<id>/<catalogue>`). Detachments or factions BSData doesn't have are listed as import notes. Only how the maths reads one (applies to, phase, modifier) can be edited, in every faction at once; edits are saved in the app data JSON's `wahapedia_overrides` by Wahapedia ID, and Reset goes back to the parsed values. Wahapedia's records are never written to the data JSON. The app data's own rules and stratagems that Wahapedia has too (same faction or core, same name) are `superseded`: hidden from the pages and ignored in saved ticks, but kept in the file. Stratagems used outside the Shooting and Fight phases (no phase) are listed on the Data tab but not offered on the pages; ticking a rule is the player saying its conditions hold, so the tick lists show the conditions that were dropped.

## Simulation engine

- **Excel parity is the contract.** `resolve()` mirrors the Sim sheets' helper columns: keywords and modifiers come from the weapon's abilities plus ticked attacker rules (attack side) and the target's keywords, modifiers, ticked target rules and extra modifiers (target side). Hit and wound modifiers are capped at ±1; BS/WS modifiers are not; cover worsens BS (11th edition); Lance/Cleave are melee-only, Heavy/Rapid Fire/Melta/cover/Stealth shooting-only.
- **Models killed** (analytical): each attack's damage is capped at the target's wounds; attacks per kill = ⌈W ÷ capped damage⌉ adjusted for Feel No Pain. A unit's weapons' kills add up, capped at the target's size; army totals add units' kills (uncapped, for "% destroyed").
- **Monte Carlo** rolls the same resolved rolls, caps each damage roll at W, and allocates damage model by model, giving exact kill and wipe chances. It rolls one attack at a time (`_roll_attack`), which is what lets an attached unit's target change mid-weapon.
- **Attached units** (leader + bodyguard): see *Leaders* above. The analytical engine's `leader_killed` is expected kills capped at the leader's models, as Excel does for units, so it overstates the leader's death when the bodyguards are only just wiped; the pages therefore show `leader_damage` instead (the two engines agree on it) and leave the chance of death to Monte Carlo.
- **Adding a keyword:** add it to `resolve()` (and both engines if it changes how rolls are made), add a case to `tests/test_simulation.py` and to the Monte Carlo agreement cases, and make sure the workbook's Keywords table (or `--reference-only` data) lists it.

## Conventions

- Say **"modifier"**, never "mod", in UI text and identifiers.
- Tests and harnesses must point `DATA_JSON`, `DATABASE_URL`, `BSDATA_DIR` and `WAHAPEDIA_DIR` at temporary copies (`tests/conftest.py` does this). Real BSData or Wahapedia files never go into the repository or the tests; `tests/fixtures/bsdata` and `tests/fixtures/wahapedia` are made up (from the `make_*_fixtures.py` scripts next to them).
- Names from BSData are imported exactly as written; likely problems are reported, not corrected.
- **Game rules live only in Python.** The pages get them from the server: `/api/units/<id>` sends each weapon's `default_use` and a `points_by_model_count` table, and `/api/keywords/check` checks keyword text. Don't re-implement a rule in JavaScript; add a field or endpoint instead.
- Pure page logic goes in `static/js/core.js` with a test in `tests/js/core.test.js`; the page scripts keep DOM and fetch code.

## Testing

`python -m pytest tests` (88 tests, about 30–80 seconds) runs the Python tests and, through `test_javascript.py`, the JavaScript tests (`node --test "tests/js/*.test.js"`, skipped if Node isn't installed). Every API test starts the app on its own temporary data file via the `make_client` fixture.

| File | Covers |
| --- | --- |
| `test_simulation.py` | Engine: the spreadsheet's own cached Sim results (parity fixtures), every keyword and modifier, damage cap and models killed, analytical vs Monte Carlo agreement, keyword parsing and checking |
| `test_rules_api.py` | Rules/stratagems lists and application (side, faction, phase); multi-weapon `/api/simulate` |
| `test_wahapedia.py` | Wahapedia export: reading the files, HTML to markup, reading effects from rule text (`parse_effects`), matching to BSData, the tick lists, replacing the app data's own, attached targets, editing and resetting how the maths reads a record, Download latest (faked) |
| `test_roster.py` | Points by unit size, generic profiles, army analysis (summary, gaps, roles, coverage, reverse durability), extra modifiers |
| `test_data_api.py` | Data editing routes, write-through to the JSON, reload round trip, import/export, cascades, pages render, default ticks and points tables sent to the pages, keyword check endpoint |
| `test_scenarios_api.py` | Saved scenarios (including re-running them) and comparisons |
| `test_wargear.py` | Option trees: defaults match the importer's points, choices change points/loadout/summary, variants replace default models, size fitting, limits, the popup's form and endpoint, army entries and Analytics loadouts |
| `test_armies.py` | Army lists: pricing and sizes, the checks, leaders and support units joining units, detachments (DP, Unique Tags), copies, the Warlord, enhancements (offered, checked, limits, Upgrades, effects in the matrix), the Analytics roster, saving/copying/deleting across a restart, text export |
| `test_bsdata.py` | The BSData importer against hand-made catalogues in `tests/fixtures/bsdata` (regenerate with `tests/fixtures/make_bsdata_fixtures.py`): faction membership and allies, stats and defensive abilities, default loadouts and options, points per size and cost modifiers, weapons, the report, bad folders, and loading the result into the app |
| `test_bsdata.py` (detachments) / `test_app_data.py` (detachments, linked weapons) | Detachments and army rules imported without allies' ones, detachment rules offered only with their detachment and kept when BSData is missing, BSData weapon search, linked custom weapons following BSData |
| `test_bsdata.py` / `test_app_data.py` (leaders) | Leader buff parsing on real BSData phrasings (and what is reported as not modelled), the leaders offered for a unit, buffs applied in the Simulator, the matrix and cell detail (phase-specific, attacker and target side), leader points, editing and resetting a BSData leader's buffs, custom leaders |
| `test_simulation.py` / `test_app_data.py` (attached units) | Bodyguard-then-leader sequencing in both engines (no overflow, worked overflow example, weapon order, leader dies last), Monte Carlo vs analytical on damage and bodyguards, attached defenders in the Simulator, matrix, cell detail and reverse durability |
| `test_setup.py` | The Setup page: choosing a folder (validation, keeping the last good one), Download latest with GitHub faked, safe zip installs, re-import, skipping setup, settings, footer version |
| `test_app_data.py` | BSData in the app: merged factions, read-only BSData records, stable IDs across re-imports and a missing folder, rules and added modifiers saved by BSData ID, Legends filter, import cache and status, broken folder |
| `js/core.test.js` (Node) | Every `core.js` helper: formatting and escaping, keyword hint HTML, copy labels, points lookup, unit-size scaling, Simulator payloads, rules filtering and selection, the Analytics request builder, the Army Analysis HTML, page-state persistence |

Line coverage (`python -m coverage run --source=src/combat_sim,scripts -m pytest tests && python -m coverage report`), October 2026:

| Module | Coverage |
| --- | --- |
| `app.py` | 97% |
| `models.py` | 96% |
| `simulation.py` | 96% |
| `data_store.py` | 94% |
| **Total** | **95%** |

**Not covered by automated tests:** the DOM and event-handling code in `app.js`, `analytics.js` and the Data page's inline script (rendering tables, wiring inputs, calling the API). Its logic is in `core.js` and the server, both tested; what remains is checked by using the pages.

## Planned: Play mode

A Play page for full games between friends, on a top-down board, played live or asynchronously. It isn't built yet. The agreed design is in [play-mode-design.md](play-mode-design.md). Several decisions there affect the existing code:

- Game rules stay in Python. The board page asks the API about distances, line of sight and attack resolution.
- Game dice are separate from the engine: they use `simulation.resolve()` for what each roll needs, and the analytical and Monte Carlo functions stay unchanged and never use them. Attached units must work as in the simulator.
- Armies are frozen into each game by BSData ID, never by database ID.
- Game state is plain JSON that doesn't depend on Flask, so it can later run under Pyodide.

## Maintainability review

### Fixed in this review

- **Scenarios page bug:** `GET /api/scenarios` was defined twice; the first, short version won, so the Scenarios page received no attacker/defender setup (names showed "Unknown" and loading a scenario lost its weapons). The duplicate is removed and a regression test added.
- **Dead endpoint:** `/api/weapons` (search weapons by unit) was no longer used by any page and has been removed.
- **Deprecated SQLAlchemy calls:** `Model.query.get()` / `get_or_404()` replaced with `db.session.get()` / `db.get_or_404()`.
- **Unused dependencies:** `pandas` and the Python `plotly` package removed from `requirements.txt` (charts use Plotly.js from a CDN); test tools moved to `requirements-dev.txt`.
- **Test gaps:** data editing, scenarios/comparisons and the migration script had little or no coverage (total was 65%); new test files bring it to 95%, and all API tests share one temporary-data fixture.
- **Duplicated rules removed and JavaScript tests added:** see *Duplicated logic* below and `tests/js`. Writing the tests found one more bug: resizing a unit restored from an older saved state drifted its weapon counts; fixed in `Core.scaleUnitSize`.

### Recommendations (not done)

In rough priority order:

1. **Split `app.py` into blueprints** (about 1,300 lines in one `register_routes` closure). Natural seams: `pages`, `data_api` (editing + export/import), `simulate` (Simulator + rules lists), `analytics` (matrix, cell detail, army analysis), `saved` (scenarios, comparisons). The helpers that close over `app` (`save_data`, `attacker_profile`, `defender_profile`) would move to a small service module.
2. **Move the Data page script out of the template.** `data.html` is about 1,350 lines, most of it inline JavaScript; a `static/js/data.js` would match the other pages and make it testable.
3. **Add a browser smoke test** (e.g. Playwright) for the main flows once a headless browser is reliably available; the Edge-based harnesses used during development were not kept.
4. **Split `style.css`** (about 2,700 lines) per page or component.

### Duplicated logic (resolved)

Earlier versions implemented these rules in both Python and JavaScript (and the points rounding once disagreed). Each now has a single Python implementation that the pages use through the API:

| Rule | Implementation | How the pages get it |
| --- | --- | --- |
| Default weapon ticks (only the first "Weapon - Profile" variant) | `app.default_used` | `default_use` on each weapon from `/api/units/<id>` (and used directly for reverse durability) |
| Points for a unit size (brackets, round half up) | `Unit.points_for` | `points_by_model_count` table from `/api/units/<id>`; the matrix also returns points |
| Keyword parsing and checking | `simulation.keyword_name` / `check_keywords` (also used by the BSData importer's report) | `POST /api/keywords/check` (KeywordHints) |
