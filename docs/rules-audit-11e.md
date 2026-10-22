# Play mode vs the 11th edition core rules

An audit of the Play engine (`src/combat_sim/play/`) against the 11th edition core rules, October 2026.
Main source: [Wahapedia's core rules](https://wahapedia.ru/wh40k11ed/the-rules/core-rules/) (section numbers below),
cross-checked with [Cadetcraft](https://cadetcraft.com/guides/rules/fight-phase),
[Wargamer](https://www.wargamer.com/warhammer-40k/11th-edition),
[Loaded Dice](https://www.loadeddice.uk/blogs/blog/the-basic-rules-of-warhammer-40-000) and
[Spikey Bits](https://spikeybits.com/warhammer-40k-11th-edition-rules-faq/).

Tick each item when the engine matches (with a test).

## Main mismatches

- [x] **One stratagem per unit per phase** (15.01): a player can't target the same unit with more than one
  stratagem in a phase. The engine only stops each stratagem being used twice in a phase.
- [x] **Ordered Retreat** (09.07): a unit that Falls Back while not battle-shocked takes a battle-shock roll
  afterwards. The engine rolls it as the Movement phase ends (moves can be redone until then).
- [x] **Fights First from datasheets** (24.13): a unit whose models all have Fights First fights in the Fights
  First step, as charging units do. Read from the datasheet when the army is frozen (games started before
  this have no record of it).
- [x] **Fight eligibility and overrun fights** (12.04, 12.06): units that charged this turn, or were engaged when
  the Fight step began, can fight; an unengaged eligible unit makes an overrun fight (an extra pile-in, then
  fights). The engine's `overrun` action is that pile-in (a drag in the Fight step); a unit with no enemy in
  reach passes. Not modelled: an Engaging consolidation's unfought enemies then fighting.
- [x] **Consolidation modes** (12.08), in order: Ongoing (engaged: stay engaged with them), Engaging (enemies
  within 3": end engaged with the ones chosen), Objective (an objective within 3": end within its range). The
  engine has no Objective consolidation.
- [x] **Monsters and Vehicles move through models on every move** (17.01 and its FAQ), except other Monsters and
  Vehicles. The engine allows it only for Normal and Advance moves.

## Smaller mismatches

- [ ] Blast against engaged Monsters and Vehicles: 17.03 exempts Blast from their -1 to hit, which suggests it can
  target them; the engine refuses (medium confidence).
- [ ] Damaged X (24.39): -1 to hit while a model has X or fewer wounds left; not applied in Play.
- [x] Precision (24.28): the attacker can make a visible Character's group the current allocation group; Play's
  allocation ignores it.
- [ ] Pile in and Consolidate: the active player moves all their units first, then the opponent, once each; the
  engine lets either player go in any order and redo moves.
- [ ] Charge moves (11.04): models that can end within 1" of, or engaged with, a target must; not enforced.
- [ ] End-of-turn coherency (03.03): units out of coherency lose models until they're back in it; not done.
- [x] Wound allocation (05.03): the defender orders the allocation groups; the engine uses standing orders
  (Characters last and wounded models first, as the rules say). Now: groups by W, Sv and invulnerable save, a
  wounded leader waits for its bodyguard, and the defender's group order is a standing order (the unit panel).
- [ ] Starting an action: the engine also refuses a unit that has shot; 16.01 doesn't list that (unverified
  against the mission cards).

## Sources disagree (the engine follows the core rules text)

- Objective markers: Wargamer says the 3" markers are gone in favour of terrain objectives; 14.02 keeps markers
  with a 3" range. Check against the Event Companion.
- Hidden: Cadetcraft says the area needs a Dense feature; 13.09 says Light or Dense.

## Not modelled

Deep Strike, Strategic Reserves, ingress and Rapid Ingress; Transports, Firing Deck, Deadly Demise; Scouts and
Infiltrators; Fire Overwatch (snap shooting), Heroic Intervention, Counteroffensive (used by hand); Indirect Fire;
Lone Operative; Towering and Plunging Fire; Hover; Aircraft; One Shot; Super-Heavy Walker; surge moves; Epic
Challenge.

## Checked and matching

Engagement range 2"; coherency 2" and 9"; 1CP to both players each Command phase; battle-shock (2D6 against the
best Leadership, at or below half strength or already shocked; OC 0, no stratagems); objective range 3" and
terrain objectives, control at the end of each phase; Advance (D6, Assault weapons only); Fall Back (no shooting or
charging); Desperate Escape (a hazard roll per model); charges (12", 2D6, engaged with every target and no other);
fight order (Fights First alternating from the active player, then the rest from whoever would pick next); pile-in
and consolidate 3"; close-quarters shooting and -1 to hit for Monsters and Vehicles; only engaged Monsters and
Vehicles can be shot; cover worsens BS by 1; Hidden 15" (12" gone to ground); Heavy; hazard rolls; mortal wound
order; climbing Dense terrain and taking to the skies; action restrictions; Tactical discards (1CP).
