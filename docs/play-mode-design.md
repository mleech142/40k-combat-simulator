# Play mode design

A planned expansion: friends play full games of 40k against each other on a top-down board inside the Combat Simulator. Games can be played live or asynchronously, chess-app style: you finish your move and the app tells your opponent it's their turn. This document records what has been decided and why, so later work can build on it without reopening settled questions.

Status (October 2026): **in progress and released as experimental** (2.3.0 onward, shown only when "Show Play in the menu" is ticked on the Setup page). Built: hot-seat games, the 11th edition missions and terrain, battle mats, and online games through the relay. Being tested now; what's left is in [Roadmap](#roadmap) (**Next**), from the owner's testing list ([testing-list.md](testing-list.md)).

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
- **Undo with the opponent's OK** (testing list item 11, not built): in a game against someone else, Undo should ask the other player to accept it. Today undo takes back your own last action at once, until a roll is made. To decide: what happens while they haven't answered, and whether it can cover actions with dice (overlapping the take-back setting above, which isn't built either).
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
| Polygon (a corner cut off: 8″ wide at one end, 2″ at the other; can be turned over) | 11.5″ × 8″ |
| Medium rectangle | 6″ × 4″ |
| Long line | 10″ × 2.5″ |
| Short line | 6″ × 2″ |

- No source publishes these layouts as data. They exist only as diagrams in Games Workshop mission packs, the official app and tournament PDFs.
- *Superseded by the missions plan (October 2026):* the app ships the 45 Warhammer Event Companion layouts as measurements (`play/layouts.py`, read from the free PDF's drawing data by `scripts/extract_event_layouts.py`, checked against each card in batches), kept in one file that can be updated each season or removed. Players can still make and share their own in the terrain editor.
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
- *Done (October 2026), the first part of step 2, with no server:* **seats and game files.** A game is either hot-seat (`SavedGame.seat` None) or this PC plays one side (0 or 1, chosen on the new-game form, changeable with `PUT /api/play/games/<id>/seat`). `play/seats.py` says whose each action is (a unit's actions are its player's, actions naming a `player` are theirs, mission choices the chooser's, moving the game on whoever it waits for) and `waiting_on` (both while each chooses secondaries); the API refuses another seat's actions (403), and each event records `by`. Undo only takes back your own last action. Games carry a `uid` (the same on both PCs) and `app_version`, the newest version that played them: an older app can open the game but not play it until it updates. `GET /api/play/games/<id>/file` saves the game as a file (`play/game_file.py`: frozen armies, events, battlefield, twist, `sent_by`); `POST /api/play/games/import` opens one: a new game plays the seat the sender doesn't, a known one is brought up to date when the file carries on from it, and copies that went different ways are refused unless the player chooses the file's. Secondaries and Tactical decks are in the file both players get, so hidden information is on trust, like the dice.
- *Done (October 2026):* **the relay** (`relay/`, a Cloudflare Worker with a Durable Object per game; how it works and how to deploy it: [relay.md](relay.md)). A game this PC plays one side of goes online with "Play online" (`POST /api/play/games/<id>/online`), which gives an invite code; the other player joins with it (`POST /api/play/games/join`) and gets the other seat. An action in an online game first fetches the other player's moves (if there are any, it's refused and the board brought up to date: 409), then is resolved, then appended on the relay, and only then saved here; undo takes it back on the relay too. Without the relay an online game can't be played (503). The open page fetches the other player's moves (`POST /api/play/games/<id>/sync`) every 5 seconds while it waits for them and every 20 on your move, and marks the tab "Your move". The relay's address is a setting (Setup page, else `RELAY_URL`, else `routes.DEFAULT_RELAY_URL`). Storage limits (an open question): 2 MB per request, 20,000 events per game, deleted 90 days after the last move. Online games take no game files. Not yet: email notices. *Security pass (October 2026):* games carry no rule text (stratagems and rules by `ref`, text from each PC's own data), everything coming from the other app is checked before it's saved, versions must be plausible, players' names show who joined, and "New invite code" moves a game to a fresh code and closes the old one ([relay.md](relay.md)). *Open seat (October 2026):* a game can start with your army alone ("The other player joins online with theirs" on the new-game form): it goes online at once, waiting for their army (`SavedGame.pending` holds the choices that need both armies; your own saved battlefield is copied into it). Joining asks for one of the joiner's saved armies; their app freezes it, makes the waiting choices (`routes.finish_setup`: the pairing's layout, the twist, the name), checks the whole game and joins with it, and the creator's app takes it on its next sync after checking its own army is unchanged (`take_completed`). Game files still need both armies.
- Peer-to-peer connections were rejected: they need both players online at once, which breaks async play. So was one player hosting the game: their PC would have to stay on.

## Battle mats

The board is drawn on a battle mat, like a tabletop game's (agreed October 2026). Each mat is a picture in
`static/img/mats`, listed in `Core.PLAY_MATS` (`core.js`). A game's mat is picked from its ID (`Core.playMat`), so
both players see the same one for the whole game with nothing stored, and it's shown at `Core.PLAY_MAT_STRENGTH`
(35%: dimmed in the field's colour) so the grid, zones, terrain, objectives and models stay readable on top.

- **Seven mats, to be replaced by improved versions later.** Twelve were designed as a procedural prototype
  (October 2026; the throwaway branch is deleted). The project owner kept seven: Hive Sector, Red Planet, Ruined
  Highway, Ash Wastes, Death World Jungle, Forge Yard and Tomb World, each shipped as the prototype's own drawing.
  Lava Fields, Green Fields, Fallen Colony, Frozen Wastes and Dune Sea were dropped. The owner refines the mats in
  another tool and swaps each picture in when it's ready (same file name, so nothing else changes); new mats may
  follow. The first refined Hive Sector waits until its aquila emblems are removed.
- **Format:** 15:11 (60" by 44"), at least 2400 by 1760 pixels (40 per inch); a JPG at about 85% quality (about
  0.5 to 1 MB) so builds stay small. Full strength: the app dims it. No grid, zones, objectives or text.
- **No Games Workshop art,** as everywhere in the app ([Goals and limits](#goals-and-limits)): no emblems (the
  aquila, faction symbols), logos or artwork, and nothing copied from commercial mats. The first refined Hive Sector
  had aquila emblems, so it waits until they're removed.
- **Readable on top:** avoid features that look like board information (circles the size of an objective's range,
  long straight lines like the divider, rectangles like terrain) and colours close to the players' (orange, blue).
- **Adding one:** put the picture in `static/img/mats` and its entry in `Core.PLAY_MATS`; the JavaScript tests
  check each listed file is there.

## Platforms

- **Desktop first:** the existing Windows and Linux builds.
- **Phones later,** as an installable web app running the same Python engine in the browser through **Pyodide**. This keeps game rules in Python with no second JavaScript engine. It also means not hosting the engine and BSData-derived content on a server.
- Board controls should be designed so they can work with touch later (large targets, unit-level dragging), without making phones a target for the first versions.
- **To reconsider: a hosted version** (testing list item 12, October 2026). The owner asked to scope a version people use in a browser, and later on a phone, with nothing to install (a Docker image or a hosted site). This reopens "not hosting the engine and BSData-derived content on a server" above, so it needs its own plan before anything is built: data licences (BSData and Wahapedia are downloaded by each user and never redistributed), accounts and per-user storage (the app is one user per install), hosting costs, upkeep and security, phone-sized pages, and Docker self-hosting as a possible first step.

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

Steps 1 to 3 are built (see [Version 1 build plan](#version-1-build-plan) and [Missions plan](#missions-plan-11th-edition)), apart from manual standing orders, take-backs and email notices.

5. **Next** (from testing, October 2026; numbers are the [testing list](testing-list.md)'s, where each item is described):
   - **Fixes:** model bases that fit through a gap are treated as crossing Dense terrain (5); phase changes and actions are slow, measure first (4).
   - **Board and controls:** drag to pan the camera (9); a Measure tool between two points (10); turning models and moving single models in the Movement phase (2); a unit's datasheet on hover (7); marking which model is the attached leader (8).
   - **Rules:** Strategic Reserves, Deep Strike, ingress and Rapid Ingress (1); standing orders for reactions in the other player's turn, with Fire Overwatch and Command Re-rolls (6); reading many more abilities, stratagems and rules into effects the game applies, so fewer manual effects (3).
   - **Game flow:** Undo asks the other player to accept (11).
   - **Fun:** notices such as how lucky or unlucky a roll was against the expected result (15).
6. **Bigger plans, each needing its own design first:** games with more than two players, 2v2 and free-for-all (13); computer-controlled armies and neutral units (14); a hosted or Docker version for browsers and phones (12, see [Platforms](#platforms)).

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
   - The page drops a picked unit in rows of up to 5, bases 0.1" apart, attached leaders in the back row for either player (`Core.playFormation`), shifted to fit the player's zone if it can (`Core.playFit`); a drag moves the unit, Shift-drag one model; the wheel zooms (`Core.playZoom`).
2. *Done:* **Movement**, with a simple turn order:
   - **Turn order** (`state['turn']`: phase, round, player). Deployment: players take turns setting up a unit (a player with nothing left to place sits out; a placed unit can be adjusted on your own deployment turn); on the page a click (or a drag) places a unit provisionally, which can be moved and pivoted (15° or 45°, or Q / E) until OK sends the `place` action (Escape or Cancel puts it back); `end_deployment` once every unit is placed. Then each player's turn runs Command, Movement, Shooting, Charge and Fight (`next_phase`); only the active player acts. Each unit's `activity` (what it did this turn) is cleared when a player's turn starts.
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
4. *Done:* **Charge and Fight** (11th edition core rules):
   - **Charge** (`charge`, then `charge_move` with `targets`): a unit within 12" of an enemy, unengaged, that didn't Advance or Fall Back, rolls 2D6 first; then its targets are enemy units within 12" and within the roll. Each model moves up to the roll in a straight line, not through enemy models, and each one that moves ends closer to a target; the unit ends engaged with every target and with no other enemy unit, in coherency. Not enforced yet: "a model that *can* end engaged must". The roll stands; the move can be redone (`reset_move`) until the phase ends. A unit that charged has Fights First and Lance until the end of the turn.
   - **Fight phase steps** (`turn.step`): `pile_in` (both players; up to 3" from where the unit started the step, each moved model ending closer to the closest target, the unit ending engaged; models in base contact stay put; for engaged units or units that charged), then `fight`, then `consolidate` (both players; units that fought; up to 3" closer to the units they're engaged with, or to enemy units within 3"). Pile-in and consolidation moves can be redone until their step ends.
   - **Fight order** (`turn.fight`: `stage`, `picker`): Fights First units first, players alternating from the active player; when none is left, the remaining units, alternating from the player who would have picked next; a player with nothing to pick is skipped. A unit can fight if it's engaged, hasn't fought and has melee weapons.
   - **Attacks:** models engaged with the target attack; each part of the unit uses one melee weapon plus all its Extra Attacks weapons; a part with no model engaged doesn't attack.
   - `game.charge_options` and `game.fight_options` (`GET /api/play/games/<id>/charge|fight/<unit>`) tell the page what a unit can do. The page: Declare charge, target chips (or click enemy units), the charge roll as the move ring; the Fight step's banner says whose pick; a weapon and target per part; fights and charge rolls in the dice log.
   - Not yet: overrun fights, splitting a weapon's attacks between targets, Fights First from datasheet abilities, objectives for consolidation.
5. *Done:* **Full game loop** (11th edition core rules):
   - **Command points:** both players start with 0 and gain 1 in every Command phase (`players[].cp`).
   - **Battle-shock:** as a player's turn starts, they roll 2D6 for each of their units that is battle-shocked or at or below half strength (half its starting models; a single model, half its wounds); below the unit's best Leadership it is battle-shocked (`units[].status.shocked`) until a later roll passes. Battle-shocked units can't be targeted by their own stratagems, and Fall Back by Desperate Escape: through enemy models, with a hazard roll per model when the Movement phase ends.
   - **Hazard rolls:** fail on 1-2; each failure costs the unit 1 mortal wound (3 if every model is a Monster or Vehicle), allocated like other wounds (Feel No Pain still rolls). Hazardous weapons make one per weapon fired.
   - **End and score:** the battle ends after round 5 (`turn.phase` `ended`); kill points (`game.score`) are the points (from the frozen army, enhancements included) of enemy units destroyed. Primary and secondary objectives will replace this.
   - **Stratagems and rules** are frozen with each army: core stratagems, and the faction's stratagems and rules for the whole faction or a chosen detachment. A stratagem (`stratagem`: player, key, unit) costs its CP, once per phase per player, on the player's own unit that isn't battle-shocked; if the app reads its effect, the effect lasts the phase, otherwise its text is logged to apply by hand. A rule (`rule`) is switched on for a unit at no cost for this phase, this turn or the game. The page offers those for the current phase (`Core.playUsableNow`: the app reads phases only for shooting and fighting, so others are offered in every phase).
   - **Manual effects** (`effect`, `remove_effect`; `game.MANUAL_EFFECTS`): ±1 to hit, ±1 to wound, re-rolls, +1 AP, ±1 Damage, Feel No Pain 4+/5+/6+, cover, Lethal Hits, Sustained Hits 1, Devastating Wounds, each for this phase, this turn or the game. Effects (`units[].effects`) reach the dice as attacker or target modifiers and end with their phase or turn.
   - **Corrections** (`correction`, then `accept` / `decline` by the other player): models' wounds, life and position, and players' CP; nothing else can happen while one waits (`state.pending`).
   - Units that can still act in the phase (`game.ready_units`, sent as `ready`) pulse on the board and are tagged Ready in the list; units that already acted this phase are dimmed.
   - A phase track above the board (`Core.playPhaseTrack`): a square with an icon for each phase of the turn, the current one lit in the active player's colour, those done ticked, and the Fight phase's step under it.
   - Not yet: Command Re-roll re-rolling a die (it's logged), reactive stratagems with their own timing (Fire Overwatch, Heroic Intervention), objectives. The faction's army rules include every sub-faction's (for example every Space Marine chapter's), as Wahapedia lists them.

### Rules decisions made while building

- **Wound allocation before the board exists:** a model already damaged first, then bodyguard models before leaders, then the last-listed model first (the first-listed models carry the special weapons, so they're removed last). When positions arrive, the last step becomes the "remove from the back" standing order.
- **Devastating Wounds don't carry over:** extra damage is lost at the model it hits, as in the Simulator.
- **Movement values** (11th edition core rules, checked against Wahapedia's copy in October 2026): engagement range 2" horizontally (height ignored); coherency: within 2" of at least one other model of the unit and within 9" of every other one; Advance adds a D6; only engaged units Fall Back; models move through friendly models but not enemy ones, except Monsters and Vehicles making Normal or Advance moves, which move through any model that isn't a Monster or Vehicle. Distance is a straight line per model for now; turning on the spot is free.
- **Shooting types** (11th edition): normal shooting (unengaged, didn't Advance); assault shooting (Advanced: Assault weapons only); close-quarters shooting (engaged, didn't Advance: Pistol / Close-Quarters weapons at units engaged with it, or anything for Monsters and Vehicles at -1 to hit unless it's a Close-Quarters weapon at a unit they're engaged with). Targets must be unengaged, except engaged Monsters and Vehicles (-1 to hit); Blast never targets engaged units. Outside Monsters and Vehicles, each model fires either its Close-Quarters weapons or its others. Heavy: +1 to hit if the unit is unengaged and no model moved more than 3". Fell Back: no shooting.

## Missions plan (11th edition)

Agreed October 2026, after reading the 11th edition core rules and the 2026-27 mission deck (Wahapedia's copies) and community guides.

### How 11th edition missions work

- **Force dispositions:** each detachment has one (Take and Hold, Purge the Foe, Disruption, Reconnaissance, Priority Assets; imported from BSData). Each player's **primary mission** comes from a 5×5 matrix of their disposition against the opponent's; outside mirror matchups the two players have different primaries.
- **Secondary missions:** each player plays **Fixed** (two of A Grievous Blow, Assassination, Bring It Down, Engage on All Fronts; up to 20 VP each) or **Tactical** (an 18-card deck: draw 2 in each Command phase, discard when achieved, redraw once per battle or discard at the end of the turn for 1 CP).
- **Caps:** 15 VP per battle round and 45 per game for primaries, the same for secondaries; end-of-battle VP aren't capped.
- **Objectives:** a 40mm marker, in range within 3" horizontally (5" vertically); a terrain objective's range is its terrain area. Control is the sum of OC in range at the end of every phase and turn; a tie is nobody's; not sticky unless a rule secures it; battle-shocked units add nothing. Home (one per player), expansion and central objectives (one, or two on a D6 roll of 6).
- **Mission sequence:** dispositions, map, battlefield (objectives, then terrain), roll-off for attacker/defender, Fixed or Tactical, deployment (defender first, alternating), roll-off for the first turn.
- **Actions:** started by units that aren't battle-shocked, engaged, Advanced or Fell Back, with OC above 0; while performing one a unit can't shoot or charge, and moving stops it.
- **Maps:** 6 deployment maps in the Chapter Approved deck; the Event Companion's layouts A, B and C per disposition pairing fix zones, objectives and terrain. They exist only as images.

### Decisions

- **Content:** the missions are built into the app (`play/missions.py`) as data and scoring logic in our own words: names, conditions, VP and caps; no card text or art. The repo and releases are public, so the content is kept in one file that can be updated each season or removed.
- **Force disposition** is chosen in the army builder from the army's detachments' dispositions and frozen into the game; older armies default to their first one and the page asks to confirm.
- **Scoring** is automatic (objectives held, units destroyed and where, actions, operation markers), with the caps; VP replace kill points on the scoreboard. "Correct the game" gets a VP field. Battle Ready (10 VP for painted armies) is left out.
- **Choices the missions give players** go through a Mission panel (above the stratagems): required ones (Punishment's targets, Beacon's unit, A Tempting Target's objective) hold the phase button and say who decides; optional ones (discards, redraws, starting actions) don't. End-of-turn scoring is summarised in the log.
- **Game setup** walks through the mission sequence as logged actions.
- **Battlefield editor** (a Battlefields pop-up, opened from the Games pop-up, which holds the saved games and the new-game form so the board takes the page): the 5 footprints with category (Exposed, Light, Dense) and height, deployment zone polygons, objectives by type; snapping, rotation, exact coordinates; built-in maps read-only, your own in the database, export and import as files.
- **Terrain rules (11th edition, ground level):** Light and Dense areas are obscuring (lines of sight through them are blocked, except for models inside); Dense counts the same as Light for seeing (Solid's low gaps can't be modelled from above); cover, Hidden (15" detection, lost after shooting) and Gone to ground are automatic; models that aren't Infantry, Beasts (BSData's keyword is `Beast`), Swarm or Mobile climb Dense areas over 2" high (core rules 13.06: each such area a move goes into or out of adds twice its height to the distance); a Fly unit can take to the skies for a Normal, Advance, Fall Back or Charge move (21.03: 2" less, through all terrain and models; the page's Take to the skies switch, action `skies`); moves can go via waypoints.
- **Assumption:** tactical cards are public once drawn.

### Order

1. *Done:* **Objectives** (`state.objectives`: id, kind home/central/expansion, owner for home objectives, x, y, controller). Range is 3" from the 40mm marker's edge (`OBJECTIVE_RANGE`, `in_range`); `update_control` settles control at the end of every phase (so every turn): the player with more OC in range, a tie is nobody's, battle-shocked units add nothing, nothing is sticky. Events record controller changes (`event.objectives`). The page draws each marker with its range ring, coloured by controller and lettered H/C/E, with the live OC in range on hover (`objective_oc`). Until battlefields exist, every game uses `DEFAULT_OBJECTIVES` (a home objective in each deployment zone, one central and two expansion objectives on the centre line). Terrain objectives come with terrain areas in step 2.
2. **Battlefields and terrain:** the format and editor, the 6 deployment maps, then the Event Companion layouts in batches (each checked against its picture); the 11th edition terrain rules. *In progress:* done are the format (`play/battlefields.py`), the 6 maps, the terrain rules (visibility, hidden, cover, Dense terrain, terrain objectives), saved battlefields (`/api/play/battlefields`), the battlefield editor (`static/js/play-editor.js`), and the setup sequence: new games start in a `setup` phase (`SavedGame.setup`; older games skip it) with logged actions `roll_objectives` (D6: two central objectives along the divider on a 6, skipped when the battlefield fixes them), `roll_off` and `choose_role` (the winner picks attacker or defender; home objectives follow); the defender deploys first and players alternate; `end_deployment` rolls off for the first turn (`roles.first`), and each round starts with that player. Waypoint moves: a Movement-phase `move` with `waypoint` carries on from where the unit stands (kept in activity `via`); the whole path counts against the Move (and for Heavy) and every leg is checked; the page's Waypoint button does this and draws the route. The Event Companion layouts (version 1.2): all 45 read from the PDF's drawing data (terrain areas fitted to the footprints by their straight edges, dense or light by their features' colour, objectives by their icons, zones matched to the six maps) into `play/layouts.py`; built-in battlefields (`BUILTIN`, after the maps; `kind` 'layout', with their dispositions and missions) whose central objectives are fixed (`fixed_objectives`: no central roll). The fifth footprint is a polygon, not a triangle (an old `triangle` reads as it). Being checked against the cards in batches: 1 Take and Hold (15 layouts), 2 Purge the Foe, 3 the rest.
3. *Done:* **Force dispositions and primary missions,** VP and caps (`play/missions.py` the cards, `play/mission_state.py` the game side; the Mission panel and VP log lines on the page). Agreed October 2026, from the mission deck's own wording (Wahapedia's copy of the 2026-27 deck):
   - **All 25 primaries** go in `play/missions.py` (names, timings, conditions and VP, in our own words); every line that needs no action or operation marker scores now, and action lines show as "comes with actions" until step 5.
   - **The matrix** is each player's disposition against the opponent's; the Event Companion layouts' pairings give the same matrix (a test checks).
   - **Timings** as printed: almost every line is "end of your turn" or "end of your Command phase from round 2 (end of your turn in round 5)"; Punishment's kill line is "end of a turn" (either player's). End-of-battle lines score once after the last turn.
   - **Meatgrinder's** "you control your opponent's home objective (5VP)" scores at the end of the battle (Wahapedia prints it under round 2+ end of turn; every sibling card has it at the end of the battle).
   - **Caps:** primary VP up to 15 per battle round and 45 per game per player; end-of-battle VP don't count towards the 15 but do towards the 45. Battle Ready is left out.
   - **Disposition** is chosen in the army builder from the ticked detachments' dispositions (filled in when there is only one), checked when the list is priced, saved with the list and frozen into the game; older lists default to their first and the page asks to confirm. Army share codes carry it once this work meets them on master.
   - **No disposition** (custom factions, games started before missions): no primaries, kill points as before.
   - **New game:** the form shows both dispositions and primaries, and the battlefield defaults to that pairing's Event Companion layouts (random, or A, B or C); any map or saved battlefield can still be chosen.
   - **Punishment:** at the start of your turn a required choice in the Mission panel (1-3 eligible enemy units, else any one); the phase button waits; condemned units are marked until the start of your next turn.
   - **Scoring** is automatic at each line's timing, logged line by line with any VP a cap cut; the scoreboard shows VP; "Correct the game" gets a VP field per player.
   - **Mission panel** (above the stratagems): both primaries, each line with its timing, whether it would score now and VP so far; primary VP this round against 15 and in all against 45.
   - **Tracking:** at the start of each turn the game records objective control and where each unit stands (in range of objectives, of central objectives, within terrain areas); each destroyed unit is logged with what destroyed it and where that unit stood; replays rebuild it from events.
4. *Done:* **Fixed secondaries.** Agreed and built October 2026, from the deck's wording:
   - **Choosing:** after attacker and defender are decided, a setup step (`choose_secondaries`): each player picks Fixed or Tactical, and for Fixed two of the four Fixed cards (A Grievous Blow, Assassination, Bring It Down, Engage on All Fronts), active all battle. A choice shows only as chosen until both have chosen, then both are revealed in the log; deployment follows, the defender first. Tactical (two cards drawn at the start of each of your Command phases) is offered as "comes later" until step 6. Games without missions have no secondaries.
   - **Scoring** (`missions.SECONDARIES`, Fixed values): A Grievous Blow 4VP per enemy unit of 13+ models (starting strength) destroyed and Bring It Down 4VP per enemy model with 10+ wounds destroyed, each at most 5VP a turn; Assassination 3VP per enemy Character model destroyed, +1VP each with 4+ wounds; all at the end of every turn. Engage on All Fronts 2VP for a presence in three table quarters, 4VP in four (units wholly within, not within 6" of the centre, not Aircraft or battle-shocked), at the end of your turn.
   - **Destroyed models** are logged as they die, whatever destroys them (`mission['models_destroyed']`, cleared each turn), so a leader killed inside a unit counts.
   - **Caps:** secondaries 15VP a battle round and 45VP a game, and each Fixed card 20VP; the player whose turn it is scores first. VP totals are primary + secondary + corrections.
   - **Page:** the setup bar has each player's choice; the Mission panel shows each player's cards under their primary, with VP against 20, what each line would score now and the secondary caps.
5. *Done:* **Actions and operation markers** (unlocking the action-based primaries; `missions.ACTIONS`, `mission_state.start_action` / `complete_actions`, every primary line now scores). Agreed October 2026, from the core rules (16) and the primary cards:
   - **Starting:** in your Shooting phase, by a unit on the battlefield that isn't Aircraft or a Fortification, battle-shocked, OC 0, engaged (unless Titanic), and didn't Advance, Fall Back, shoot or start another action this turn (a unit that has shot can't start one unless a rule says so; no such rules are modelled yet). Until the end of the turn it can't shoot (unless Titanic) or declare a charge.
   - **Completing:** a unit that leaves the battlefield or becomes battle-shocked doesn't complete its action. "If your unit controls that objective" means you control it and that unit is within its range. Removing an operation marker removes its status.
   - **Booby Trap** traps the terrain area the unit is within (not in your deployment zone), or the terrain area of a terrain objective it is in range of; plain objectives can't be trapped.
   - **Sensor Sweep:** the marker to remove is chosen when the action starts and removed when it completes.
   - **Locate and Deny:** when deployment ends, a required Mission panel choice of five terrain areas outside your deployment zone (or all of them if fewer).
   - **Surveil the Foe's** marker removal applies when moves lock (the end of each phase or Fight step), not on trial moves.
   - **Consecrate** is automatic at the end of your turn: each consecration unit marks an unconsecrated objective in range (not your home), your opponent's home objective first.
   - **Page:** a "Mission actions" section for the selected unit in your Shooting phase (each action, its target, or why not); markers on the board in their owner's colour with a letter; starts, completions and removed markers in the log.
   - The Tactical cards' actions (Plunder, Cleanse) come with the Tactical deck (step 6).
6. *Done:* **Tactical secondaries** (`missions.TACTICAL`, `play/tactical.py`). Agreed and built October 2026, from the deck's wording:
   - **The deck:** choosing Tactical shuffles the 18 cards (game dice' generator; the order is kept in the game). At the start of each of your Command phases you draw two; there is no hand limit and cards stay active until scored or discarded. Scoring a Tactical card discards it (no 20VP card limit; the 15-a-round and 45-a-game secondary limits apply).
   - **Hold:** scoring is automatic, but each active card has a Hold toggle (a held card isn't scored or discarded), for cards worth more later (Display of Might: 2VP at the end of your turn, 5VP at the end of your opponent's).
   - **When drawn:** Plunder / Cleanse (the other active), A Grievous Blow (no enemy unit of 13+), Bring It Down (no enemy model of 10+ wounds), Forward Position and Behind Enemy Lines (round 1) offer a Redraw for that Command phase; Defend Stronghold drawn in round 1 is redrawn without asking. Beacon (your unit) and A Tempting Target (your opponent picks an objective in No Man's Land) are required choices (`mission['choice']` with a queue); Burden of Trust's guards start as a unit in range of each objective and can be changed.
   - **CP:** cards marked during your turn are discarded at the end of it, after scoring, for 1CP in all; once per battle in your Command phase, 1CP discards a card and draws another.
   - **Timings** added: the end of your opponent's turn, and "that or the end of round 5" (the second player's own turn in round 5). At the end of a turn the player whose turn it is scores first.
   - **Readings:** No Man's Land is outside both deployment zones; "not within your territory" means no part within it; Forward Position is your opponent's home objective or every expansion objective; Assassination (Tactical) also scores once every enemy Character is dead. Plunder and Cleanse are actions while their card is active.
7. *Done:* **Twists** (`missions.TWISTS`). Agreed and built October 2026: chosen on the new-game form (None by default, any of the six, or Random), frozen into the game (`SavedGame.twist`), shown beside its name and logged. Scrambled Communications swaps the primaries; Mirrored World gives both players one of its five primaries, chosen on the form or rolled during setup (a D6, rolling a 6 again; `roll_mirrored`). Those two need both armies to have a force disposition; the rest work in any game: Martial Pride (Battleline units can start an action after Advancing and shoot after starting one), Ruinscape (Normal and Advance moves cross tall dense terrain), Night Fighting (units can't be seen from more than 18" away; Indirect Fire isn't modelled, so it needs sight like any weapon), Nowhere to Hide (no change on this flat board: Solid isn't modelled).

## Open questions

These are for later stages and are not yet decided:

- The exact list of manual effects, and how each maps onto engine modifiers.
- Which standing orders exist, and their defaults.
- Terrain categories and how they're stored, once the 11th edition terrain rules are written down in detail.
- How reserves, Deep Strike and other set-up rules appear on the board.
- How Undo with the opponent's OK works with the take-back setting and "correct the game".
- Multiplayer (2v2, 1v1v1v1): which rules to follow, turn order, seats on the relay, board sizes.
- A computer player: how it decides (moves, targets, stratagems, reactions), how good it should be, and how neutral units fit in (a third side).
- Whether to host the app at all (see [Platforms](#platforms)).
