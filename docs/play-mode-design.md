# Play mode design

A planned expansion: friends play full games of 40k against each other on a top-down board inside the Combat Simulator. Games can be played live or asynchronously, chess-app style: you finish your move and the app tells your opponent it's their turn. Nothing here is built yet. This document records what has been decided and why, so later work can build on it without reopening settled questions.

Status: agreed design, October 2026. No code yet.

## Goals and limits

- **For friends, free, non-commercial.** No charges, ads or donations tied to the game. There is no public matchmaking or ladder.
- **No Games Workshop art or content shipped.** Units are plain shapes. Data comes from what users already supply (BSData, Wahapedia) or create themselves (terrain layouts). This matches how the app handles data today and keeps it low-profile. A Games Workshop cease-and-desist shut down the Vassal 40K module's maintainer in 2008.
- **Representative of the tabletop game.** The board uses continuous inches and real base sizes, not a grid.
- **Built up slowly.** Each stage below is playable on its own.

### Why build it

Existing options each cover only part of this:

- Vassal's 40K module has a top-down board and play-by-email, but doesn't enforce any rules.
- Tabletop Simulator is 3D, needs both players at once, and doesn't enforce any rules.
- The official video games (Battlesector, Tacticus, Combat Cards) use their own simplified rules.

Nothing found combines real army lists, rules worked out automatically, and asynchronous play.

## What it looks like

- A new **Play** page in this app. It reuses armies (`SavedArmy`, `armies.py`), wargear, leaders, enhancements, rule parsing (`rules_text.py`) and the dice engine (`simulation.py`).
- A 44″×60″ board seen from above, drawn as **SVG**. SVG makes clicking and selecting easy and handles about 100 bases without trouble.
- Every model is a circle or oval at its true base size, coloured by player, with a short label. A unit is drawn as its bases with a faint outline around them, so it reads as one shape.
- Units **move as a group by default**: drag the unit and its models keep their formation. Single models can be moved when it matters (coherency, pile-in, casualty choice). Each model's base is kept because many rules depend on where each model is: range from any model, coherency, engagement range, charges, auras and "wholly within", and cover for each model.
- Selecting a unit shows its datasheet, range rings and coherency lines.
- Terrain is drawn as plain shapes using the 11th edition footprint set (see [Terrain](#terrain)).

## Rules automation

The app assists with the rules but doesn't automate everything. The split:

| The app controls (no override) | Players control |
| --- | --- |
| Every dice roll. Rolls are random, using `simulation.resolve()` so keywords and modifiers set what each roll needs, and shown as dice in hit / wound / save / damage groups. Game dice are the game's own: the simulator's analytical and Monte Carlo functions never use them. | Choices the rules give them: what to move, shoot, charge, which stratagem to use. |
| Measuring: distances, ranges, engagement range, coherency. | **Manual effects** for abilities the engine can't read (see below). |
| Line of sight and cover, from terrain footprints. | Standing orders for decisions made during the opponent's turn. |
| Phase order and CP tracking. | |

- **Manual effects.** Datasheet abilities that `rules_text` can't read into modifiers (for example "once per battle, re-roll a charge") appear as manual effects. Activating one means picking its effect from a fixed list (+1 to hit, re-roll wounds, +2″ move, and so on). The opponent doesn't need to approve it, but it's logged and highlighted for them. Players can never edit dice results or distances directly.
- **Game rules stay in Python**, as everywhere else in the app (see `CLAUDE.md`). The board page asks the API about distances, line of sight and attack resolution. It never decides them itself.
- **Attached units** must work as they do in the simulator. Attacks go at the bodyguard models first, then the leader (`app.leader_target`).

### Mistakes and undo

- **No take-backs** by default. Movement and setup can be undone freely until a roll is made. Rolling locks in the action and adds it to the log.
- The game creator can **turn take-backs on** when starting a game. A take-back then needs the opponent's approval.
- **"Correct the game" request**, always available whatever the take-back setting. A player proposes a fix (move a model, restore wounds, re-run one attack), and it only happens if the opponent accepts. It's logged as a correction. This is the safety net for engine bugs, since line of sight and dice have no override.

## Decisions during the opponent's turn

The defender makes decisions during the attacker's turn: which models die, which save to use, Overwatch, reactive stratagems, fights-first order. If every one of these waited for a reply, one asynchronous shooting phase could take days.

- **Standing orders** (async games). Each player sets defaults, for example:
  - remove casualties from the back
  - always use the best save
  - Overwatch with unit X if it's charged
  - never spend CP automatically

  The game only stops to ask the opponent when a decision isn't covered by a standing order.
- **Prompts** (live games). The app asks the opponent each time.

## Game state

- **Frozen armies.** When a game starts, each player's army is frozen into the game as resolved profiles: stats, keywords, weapons, chosen wargear, leader, enhancements, and the parsed rules and stratagems, all referenced by BSData ID and never by database ID. Database IDs differ between installs, and the two players may have different BSData versions. The snapshot means both play from the same data for the whole game.
- **Frozen layout.** The terrain layout is frozen into the game the same way (see [Terrain](#terrain)).
- **An action log, not just a final state.** The game is a list of actions. The **acting player's app** rolls and resolves each action, and the log stores the *results*. The opponent's app replays the log and never works results out again, so a different engine version can't change what already happened. Trusting the acting player's app is fine for a friends-only game.
- **Version check.** Each game records the app version it started on. The relay refuses moves from a version that would resolve rules differently, and asks that player to update.
- **Plain-data format.** Actions and game state are plain JSON with no dependency on Flask or the database. This keeps the phone path (Pyodide) open and lets games be exported as files.
- **Storage.** Local games are saved like `SavedArmy`: in the database only, not in the app data JSON.

## Terrain

11th edition matched play missions each come with three recommended layouts, chosen by the two armies' force dispositions. The layouts are built from a fixed set of 16 footprints in 5 shapes:

| Footprint | Size |
| --- | --- |
| Large rectangle | 11.5″ × 7″ |
| Right-angle triangle | 11.5″ × 8″ |
| Medium rectangle | 6″ × 4″ |
| Long line | 10″ × 2.5″ |
| Short line | 6″ × 2″ |

- No source publishes these layouts as data. They exist only as diagrams in Games Workshop mission packs, the official app and tournament PDFs.
- **The app ships the footprint shapes** (generic dimensions) **but not Games Workshop's layouts.** A player recreates a layout from their own mission pack in the terrain editor (snapping, exact coordinates) and saves it as a named **layout file** they can share with friends. Users bring the Games Workshop-derived content and the app reads it, as with BSData.
- The game creator picks the layout, and the opponent accepts it before deployment.
- **Ground level only at first.** Each footprint has a terrain category and a height. Each category stores the current edition's visibility and cover rules. The app checks a straight line from each model to each target model against the footprints. Upper floors, models standing on terrain, and the exceptions for towering and aircraft models come later.

## Missions

The Wahapedia export only covers datasheets, abilities and stratagems. It has no missions, deployment maps or layouts. Copying them from Wahapedia's pages would go beyond what the export offers, and would also copy Games Workshop content.

- First playable version: **no missions**. The score is kill points.
- Later: deployment zones drawn in the editor, with mission scoring entered by players and shared as files, the same way as layouts. Force dispositions are already imported from BSData (`bsdata.FORCE_DISPOSITIONS`) for choosing between layouts.

## Connection

- **Relay server.** A small relay stores each game's action log and tells the other player it's their turn. It never stores BSData, Wahapedia text or rule text. It only holds frozen army snapshots inside game logs passed between the two friends.
  - Hosted by the project owner on a free serverless tier, for example Cloudflare Workers with a small store per game.
  - Its address is a **setting** in the app, so a friend can host their own.
  - Its name and web address don't use "Warhammer" or "40K".
- **No accounts (for now).** Creating a game gives an invite code. Each player's app keeps a secret token for each game, and the relay only checks that token. There are no emails or passwords to store. Accounts may come later.
- **"Your turn" notices** by email for now, set per game, and shown in the app when it's opened. Push notifications come with the phone version.
- **Backup: game files.** Any game can be exported or imported as a file and swapped by hand (Discord, email), so play doesn't depend on the relay being up.
- **Live and async use the same system.** A live game is an async game where turns go back and forth quickly. Neither depends on both players staying connected.
- Peer-to-peer connections were rejected: they need both players online at once, which breaks async play. So was one player hosting the game: their PC would have to stay on.

## Platforms

- **Desktop first:** the existing Windows and Linux builds.
- **Phones later,** as an installable web app running the same Python engine in the browser through **Pyodide**. This keeps game rules in Python with no second JavaScript engine. It also means not hosting the engine and BSData-derived content on a server.
- Board controls should be designed so they can work with touch later (large targets, unit-level dragging), without making phones a target for the first versions.

## Roadmap

1. **Hot-seat** (two players on one PC):
   - board and deployment by dragging
   - Command phase CP, then Movement, Shooting, Charge and Fight, with app dice, measuring and line of sight
   - manual effects, standing orders, the "correct the game" request
   - kill points as the score
   - ground-level terrain placed by hand

   This version tests the action-log format in real play before anything goes over a network.
2. **Relay:** invite codes, tokens, the version check, email notices, and game file export / import.
3. **Terrain and layout editor:** the five footprint shapes, layout files, the creator picks and the opponent accepts. Then missions: deployment zones and scoring.
4. **Later:** terrain elevation, accounts, the phone version with push notifications.

## Version 1 build plan

Version 1 is hot-seat play (two players on one PC).

### Starting point (October 2026)

- **Move stat.** Units already have it (`Unit.move`).
- **Base sizes.** BSData has them in each model profile's `nr-base-size` characteristic (about 1,800 entries). *Done:* the importer reads them into `Unit.base` (`bsdata.parse_base`), from the most numerous model's stat line, as with its stats. Of 3,277 imported units, 41 have no readable base, and those are listed in the import notes.
  - Most are clean values: `32mm`, `40mm`, `120x92mm` (ovals), `60mm Flying`.
  - Some are approximate (`~40mm`).
  - About 230 are `Hull`: vehicles with no base and no dimensions in BSData.
- **Dice.** The Monte Carlo engine rolls with the global `random` and doesn't keep individual dice. It stays that way: game dice are separate (see step 0).
- **Damage.** The engine treats the target as identical models with damage spread across them, not as individual models.
- **Geometry.** There is no geometry code yet.

### Scope decisions

- **Hull vehicles:** a `hull_size` (length × width) in app data, keyed by BSData ID like `unit_modifiers`, editable on the Data page. Until it's set, the app guesses a rectangle from keywords and marks the size as estimated.
- **Not in v1:** reserves, Deep Strike, transports (no embarking) and aircraft movement. Every unit starts on the board, and aircraft move like other units, marked as not supported yet.
- **Stats:** each model has its own wounds and base size but uses the unit's stats. An attached leader keeps its own profile.
- **Stratagems:**
  - Core stratagems, plus those whose effect `rules_text` reads into a modifier.
  - The rest are manual effects.
  - Reactive stratagems (Overwatch, Heroic Intervention) wait for standing orders.
- **Workflow:** each step goes to master as it's finished, with the Play link hidden behind a "Show Play (experimental)" setting until it's ready.
- **Testing:** the Python core (geometry, game state, dice recorder) is written test-first. Pure page logic goes in `core.js` with Node tests. The board drawing is checked by hand.
- **Game state lives in Python.** The page sends actions ("move unit 3 along this path", "shoot unit 7 with these weapons") and draws the state it gets back.

### Steps

0. **Groundwork, with no page yet:**
   - ~~The importer reads base sizes~~ (done: `Unit.base`, also returned by `/api/units/<id>`).
   - *Done:* game dice in the `play` package (`play/dice.py`, `play/attack.py`), **not** in `simulation.py`. The game asks `simulation.resolve()` what each roll needs, then rolls and records its own dice step by step (hit, wound, save, damage), stopping where the defender decides. The existing analytical and Monte Carlo functions are not changed and never use game dice.
   - *Done:* the game state in the `play` package.
     - `snapshot.freeze_army(army)` freezes a saved army through `armies.price_army`, the Armies page's own pricing. Each unit's parts are the unit itself, then its leaders and support units, each with stats, keywords, base, weapons (how many models carry each), leader buffs, enhancement and Warlord flag. Units with no wargear choices scale their weapon counts to the unit size, as Analytics does.
     - `game.new_game(armies)` creates every model with full wounds. The first `qty` models of a part carry each weapon.
     - `game.resolve_action(state, action, dice)` returns an event (the action, every dice group, each target model's wounds and whether it's alive, a summary per weapon) without changing the state. `game.apply_event` applies it, and `game.replay(armies, events)` rebuilds a game with no dice. The first action is `shoot`, with no range or line-of-sight checks yet.
     - Leader and enhancement effects come from `effects.py`, shared with the Simulator's routes.
   - *Done:* `play/geometry.py`, pure maths in inches with (0, 0) at the board's bottom-left and models placed by their base's centre.
     - `base_shape` (round and oval bases, ovals turned by a facing angle) and `hull_shape` (a rectangle).
     - `base_distance`: closest edge to edge, 0 when touching or overlapping. Exact for round bases and rectangles; ovals within 0.01″.
     - `footprint`: the five 11th edition footprints (`FOOTPRINTS`), placed by a corner (the triangle's right angle) and turned about it.
     - `clear_line`: a line is blocked only by passing through a shape's inside, not by grazing a corner or running along an edge.
     - `can_see`: any clear line between 16 points around each base, plus the centres.

     Which terrain blocks sight or gives cover is decided with terrain categories later. Shooting doesn't check range or sight until the board places models (step 3).
1. *Done:* **Board:** the Play page (`/play`, shown in the menu when "Show Play in the menu" is ticked on the Setup page, saved as `show_play` in `settings.json`).
   - A game is a `SavedGame` row (database only): its two frozen armies and its event log. The API (`play/routes.py`) always answers with the replayed state, the board size and each part's outline (`game.outline`: inches, `estimated` for guessed sizes).
   - Units are placed with a `place` action: every living model at once, wholly on the board and in the player's deployment zone (12" from their own long edge: player 1 at the bottom, player 2 at the top), not overlapping another model (touching is fine). No dice, so `POST .../undo` can take it back.
   - Size estimates (decided for step 1): a hull by the model's wounds (up to 8: 4" x 2.5"; up to 14: 5" x 3.5"; more: 7" x 4.5"), a missing base as 32mm; both drawn dashed.
   - The page drops a picked unit in rows of up to 5, bases 0.1" apart (`Core.playFormation`), shifted to fit the player's zone if it can (`Core.playFit`); a drag moves the unit, Shift-drag one model; the wheel zooms (`Core.playZoom`).
2. *Done:* **Movement**, with a simple turn order:
   - **Turn order** (`state['turn']`: phase, round, player). Deployment: players take turns setting up a unit (a player with nothing left to place sits out; a placed unit can be adjusted on your own deployment turn); `end_deployment` once every unit is placed. Then each player's turn runs Command, Movement, Shooting, Charge and Fight (`next_phase`); only the active player acts. Each unit's `activity` (what it did this turn) is cleared when a player's turn starts.
   - **Moves** (`move` with `mode` normal / advance / fall_back; `advance`; `stationary`; `reset_move`). During its Movement phase a unit can be moved, reset and moved again as often as its player likes; moves lock when the phase ends. Every move is measured from where the unit started the phase (`activity.start`, kept from its first move), and whether it must Fall Back is judged there too; Remain stationary and `reset_move` put it back there. Each model moves in a straight line up to its Move (+ the Advance roll), checked every 0.2" so it can't pass through enemy models; it ends wholly on the board, overlapping no model, in coherency (within 2" of another model of its unit, 2 others with 7+ models) and out of engagement range (1"). A unit in engagement range can only Fall Back, and must end out of it. The Advance roll (`activity.advance_roll`) is a roll, so it can't be undone; the move after it can.
   - Events now also record changed units' `activity` and the `turn`.
   - The page: a turn banner and phase button, move choices (and Reset move) on the selected unit, dashed rings for each model's reach from where it started, faint ghosts of the starting spots, and a live distance readout from the start while dragging (`Core.playMoveLabel`). The server sends each part's Move (`moves`).
   - Still to come with terrain: moving around obstacles (move to one point, then another) and terrain movement rules.
3. *Done:* **Shooting** (`shoot`: a target for every weapon, or one per weapon in `weapons`), once per unit per Shooting phase.
   - **Who can shoot (v1 rules, to revisit when issues come up):** not after Falling Back; after Advancing, only Assault weapons; in engagement range, only Pistols at the unit it's engaged with, except Monsters and Vehicles, which fire everything at -1 to hit (Big Guns Never Tire). Units in engagement range of your own units can't be targeted unless they're Monsters or Vehicles.
   - **Per model:** a model fires a weapon if a target model it can see is within range, base to base (models don't block sight; no terrain yet). Models within half range roll as their own group (Rapid Fire, Melta). A unit that didn't move or remained stationary counts as stationary (Heavy).
   - **Wounds** go, one at a time: a damaged model; bodyguard before leaders; common-weapon models before special-weapon carriers; then farthest from the attacking unit, or closest by the player's standing order (`orders` action, `players[].orders.casualties`); then the last-listed model.
   - **Hazardous:** after the attacks, a D6 for each model that fired one; on a 1 that model suffers 3 mortal wounds.
   - The event lists each `attacks` entry (weapon, target, models, half range, the wound pool, models killed, and its slice of `rolls`). `game.shooting_options` (`GET /api/play/games/<id>/shooting/<unit>`) tells the page which weapons can fire at which units, and why not.
   - The page: weapons with a target each (click an enemy unit to target it with everything), Fire, a dice log under the board (`Core.playDiceGroups`, `Core.playAttackSummary`) and each player's casualty order.
   - On the board: dashed arrows from shooter to target, red while targets are being chosen and faint for shots fired this phase (`Core.playShotLines`); a small badge with the wounds left on any damaged model.
   - Cover from terrain comes with terrain.
4. **Charge and Fight:** charge roll, engagement, fights-first order, pile-in and consolidate.
5. **Full game loop:** Command phase CP, battle rounds, kill points, manual effects, the "correct the game" request, stratagems.

### Rules decisions made while building

- **Wound allocation before the board exists:** a model already damaged first, then bodyguard models before leaders, then the last-listed model first (the first-listed models carry the special weapons, so they're removed last). When positions arrive, the last step becomes the "remove from the back" standing order.
- **Devastating Wounds don't carry over:** extra damage is lost at the model it hits, as in the Simulator.
- **Movement values** (confirmed for 11th edition): engagement range 1" horizontally; coherency 2" (two other models for units of 7+); Advance adds a D6; only engaged units Fall Back; moving through friendly models is allowed, through enemy models isn't. Distance is a straight line per model for now; turning on the spot is free.

## Open questions

These are for later stages and are not yet decided:

- The exact list of manual effects, and how each maps onto engine modifiers.
- Which standing orders exist, and their defaults.
- Terrain categories and how they're stored, once the 11th edition terrain rules are written down in detail.
- How reserves, Deep Strike and other set-up rules appear on the board.
- The relay's storage limits and how long finished games are kept.
