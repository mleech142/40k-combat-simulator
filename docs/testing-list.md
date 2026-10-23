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
3. **Play: work out effects automatically.** Apply abilities', stratagems' and rules' effects to units from their
   text, so the player doesn't have to set them. Today an effect the app can't read becomes a manual effect the
   player picks from a fixed list (`game.MANUAL_EFFECTS`: ±1 to hit, re-rolls, cover and so on), or a stratagem's
   text is logged to apply by hand. Means reading much more rule text (`rules_text.py`) into effects the game
   applies, including when and to whom they apply.
4. **Play: phase changes and actions are slow.** Moving to the next phase, and actions within a phase, take too long
   to process. Measure first: in an online game each action also fetches the other player's moves from the relay
   and then appends to it (two round trips), so check whether offline games are slow too.
5. **Play bug: gaps in Dense terrain.** A model whose base fits through a gap between terrain pieces (or between a
   piece's walls) is treated as moving through Dense terrain. Check how a move's path is tested against terrain
   footprints: the base should be able to pass anywhere it fits.
6. **Play: pre-set stratagems and actions (standing orders).** Let a player set reactions in advance that happen
   during the other player's turn, for example "Fire Overwatch with this unit if X enemy units come within range"
   or "use a Command Re-roll if this unit would die". Planned as **standing orders** in `play-mode-design.md`
   (Decisions during the opponent's turn) but not built. Overwatch itself isn't built yet either
   (`rules-audit-11e.md`, "Not modelled").
7. **Play: unit profile on hover.** Hovering over a unit on the board shows its datasheet: characteristics,
   weapon profiles and abilities (and the attached leader's).
8. **Play: show which model is the leader.** A Character leading a unit looks like any other model in it, with no
   label. Make it clear which model is the leader. The board already gives a leader's model the `leader` class
   (`play.js`, model drawing) but no style uses it.
9. **Play: drag to move the camera.** Click and drag to pan around the board. Today the mouse wheel zooms towards
   the pointer (`zoom` in `play.js`, `Core.playZoom`) but there's no way to pan. Dragging a model already moves
   its unit, so panning needs to start on empty board (or use another button) without getting in its way.
10. **Play: measuring tool.** A Measure button: click two points on the board and see the distance between them in
    inches. Today distances only show while dragging a unit (the readout beside the pointer).
11. **Play: undo needs the other player's OK.** In a game against someone else, pressing Undo asks the other
    player to accept it before it happens. Today undo takes back your own last action at once, as long as no dice
    were rolled since (`play-mode-design.md`: "Movement and setup can be undone freely until a roll is made"; online,
    the relay's `retract`). Decide what happens while they haven't answered, and whether a request can also cover
    actions with dice.
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
