# Testing list

Things found while testing release 2.3.2 (October 2026) that still need work. Each is noted here when it's
reported; work starts only when the project owner picks it up. The Play items are planned into releases in
`play-mode-design.md` (Roadmap, steps 5 and 6), which refers to them by these numbers: keep the numbers, and mark an
item done there and here when it's built.

Release plan (agreed October 2026): **2.4.1** items 5 and 4; **2.5** items 9, 10, 8, 7, 2, 11 and 15; **2.6** item 6
and the first batch of 3; **2.7** item 1 with Scouts, Infiltrators and Transports, and the next batch of 3; then a
written scope for 12. Items 13 and 14 are parked.

## To do

1. **Play: reserves and Deep Strike.** Add Tactical Reserves / Strategic Reserves and Deep Strike, with all the rules
   around them (ingress and Rapid Ingress, set-up restrictions, when units must arrive). Not built yet: every unit
   starts on the board (`play-mode-design.md`, "Not in v1"; `rules-audit-11e.md`, "Not modelled").
2. **Play: rotate and rearrange models when moving.** In the Movement phase, let the player turn models (and the
   unit's formation) and move individual models within the unit, not only drag the unit as a block. Deployment
   already has turning (the 15° and 45° buttons, `Core.playPivot`) and Shift-drag for one model; movement should
   offer the same. Beyond the original plan, but the owner thinks it adds to it.
   *Done (2.5):* the movement panel's Turn buttons (and Q / E) pivot the unit, each model charged its arc (sent as its
   route, `Core.playTurn`); Shift + mouse wheel spins one model on the spot for free; Shift-drag moves one model.
   Turns show at once and go as one move after a short pause.
3. **Play: work out effects automatically.** Apply abilities', stratagems' and rules' effects to units from their
   text, so the player doesn't have to set them. Today an effect the app can't read becomes a manual effect the
   player picks from a fixed list (`game.MANUAL_EFFECTS`: ±1 to hit, re-rolls, cover and so on), or a stratagem's
   text is logged to apply by hand. Means reading much more rule text (`rules_text.py`) into effects the game
   applies, including when and to whom they apply.
   *Ongoing:* batch 1 in 2.6 (`scripts/effects_report.py`; play-mode-design.md, Effects batches).
4. **Play: phase changes and actions are slow.** Moving to the next phase, and actions within a phase, take too long
   to process. Measure first: in an online game each action also fetches the other player's moves from the relay
   and then appends to it (two round trips), so check whether offline games are slow too.
   *Done (2.4.1):* measured on the owner's games (about 50 events). Every action took 25–160 ms offline except
   in the Shooting phase, where each response (the phase change and every action in it) took about 11 s: listing
   the units that can still shoot checked line of sight from every model to every enemy model, out of range
   included, against every terrain piece. Now it checks only models in range, closest first, ignores terrain that
   can't be in the way, stops at the first target and reuses sight results: 0.16–0.31 s. Online adds the two relay
   calls, about 85 ms each (0.7 s for the first of a session). `tests/test_play_speed.py` keeps it quick.
5. **Play bug: gaps in Dense terrain.** A model whose base fits through a gap between terrain pieces (or between a
   piece's walls) is treated as moving through Dense terrain. Check how a move's path is tested against terrain
   footprints: the base should be able to pass anywhere it fits.
   *Done (2.4.1):* the page sent only the drop point, and the server measured a straight line from where the unit
   started the phase, which could cut through terrain the model was steered round. Movement and charge moves now
   follow the route dragged (`route`, its bends kept as waypoints).
6. **Play: pre-set stratagems and actions (standing orders).** Let a player set reactions in advance that happen
   during the other player's turn, for example "Fire Overwatch with this unit if X enemy units come within range"
   or "use a Command Re-roll if this unit would die". Planned as **standing orders** in `play-mode-design.md`
   (Decisions during the opponent's turn) but not built. Overwatch itself isn't built yet either
   (`rules-audit-11e.md`, "Not modelled").
   *Done (2.6):* the reaction window (`turn.window`) and standing orders for Fire Overwatch (ask, never, always with a
   unit), Heroic Intervention (ask, never) and re-rolling failed saves with Command Re-roll (ask, never, only when a
   model dies); Command Re-roll of your own rolls from the battle log. Counteroffensive and faction reactive
   stratagems come with later effects batches.
7. **Play: unit profile on hover.** Hovering over a unit on the board shows its datasheet: characteristics,
   weapon profiles and abilities (and the attached leader's).
   *Done (2.5):* half a second over a model shows a card by the pointer (`Core.playUnitCard`): characteristics,
   weapons, keywords and abilities, the leaders beside the unit. Abilities come from this PC's data
   (`/api/play/abilities`), since games carry no rule text.
8. **Play: show which model is the leader.** A Character leading a unit looks like any other model in it, with no
   label. Make it clear which model is the leader. The board already gives a leader's model the `leader` class
   (`play.js`, model drawing) but no style uses it.
   *Done (2.5):* a gold ring round the leader's base and a crown on it (its name on hover), moving with it when dragged.
9. **Play: drag to move the camera.** Click and drag to pan around the board. Today the mouse wheel zooms towards
   the pointer (`zoom` in `play.js`, `Core.playZoom`) but there's no way to pan. Dragging a model already moves
   its unit, so panning needs to start on empty board (or use another button) without getting in its way.
   *Done (2.5):* left-drag on empty board pans (a click still places), middle- or right-drag anywhere (`Core.playPan`).
10. **Play: measuring tool.** A Measure button: click two points on the board and see the distance between them in
    inches. Today distances only show while dragging a unit (the readout beside the pointer).
    *Done (2.5):* the ruler button; a click on a model measures from its base edge (`game.measure`, `/api/play/measure`).
11. **Play: undo needs the other player's OK.** In a game against someone else, pressing Undo asks the other
    player to accept it before it happens. Today undo takes back your own last action at once, as long as no dice
    were rolled since (`play-mode-design.md`: "Movement and setup can be undone freely until a roll is made"; online,
    the relay's `retract`). Decide what happens while they haven't answered, and whether a request can also cover
    actions with dice.
    *Done (2.5):* online, Undo asks the other player (a Correction with `undo`); nothing else happens until they
    answer; dice actions included. Accepting replays the game to before that action and logs the difference, so the
    log keeps the action. Hot-seat Undo is unchanged.
12. **Plan: a hosted version (Docker / web), later phones.** Scope whether players could use the app in a browser,
    and eventually on a phone, with nothing to install. Needs much more planning before any building. Questions to
    answer:
    - **Data licences:** each user downloads BSData and Wahapedia themselves and the app never redistributes them
      (`CLAUDE.md`). A shared server holding them for everyone may count as redistributing.
    - **Accounts and storage:** the app is built for one user per install (SQLite, `settings.json`, the app data
      JSON, armies and games on the PC). Hosting needs accounts and per-user data.
    - **Hosting:** where it runs, what it costs, who looks after it, and security once it's on the internet.
    - **Phones:** the Play board and the other pages are built for a desktop screen and mouse.
    - **Docker for self-hosting** (a group runs its own copy) as a possible smaller first step.
13. **Play: games with more than two players.** Team games (2v2) and free-for-all (1v1v1v1), on larger boards.
    Play is built for exactly two players throughout: two seats and two armies per game, the relay's two seats,
    turn order, deployment maps and missions. Needs planning: which rules to follow (whether Games Workshop
    publishes a multiplayer format to base it on), how turns and the opponent's decisions work with several
    players, and board sizes.
14. **Play: computer-controlled armies and units.** Two uses: play a whole game against the computer controlling
    an army, and have **neutral units** in a game (for example wandering monsters) that the computer controls and
    that neither player owns. Needs a computer player that makes every decision a player does (deploying, moving,
    shooting and charging targets, fights, stratagems, reactions in the other player's turn), at a sensible level
    of skill. Neutral units also need a third side in the game (see item 13, more than two players).
15. **Play: fun, flavourful touches.** For example, notices that tell a player how lucky or unlucky their rolls
    were, statistically: "That was a 1-in-50 result" after an attack, or a luck summary at the end of a turn or
    game. The app's analytical engine already works out expected results, so an attack's actual outcome can be
    compared with them. Collect more ideas along these lines.
    *Done (2.5), first notice:* each attack's rarest roll with a target number (three dice or more, none re-rolled),
    if 1 in 20 or rarer, gets a note in the battle log ("Hot dice: 10 of 10 hit rolls on 4+, a 1-in-1,024 result";
    `Core.playLuck`, worked out from the rolls, not the Simulator's engines). Still to collect: a luck summary per
    turn or game, and other touches.
