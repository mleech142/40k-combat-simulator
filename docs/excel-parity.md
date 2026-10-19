# Excel v2 parity review

The goal is for the Python app to do everything `40KCombatSimulator_v2.xlsx` does ("the v2 sheet, pythonised"). This document compares each v2 feature with the app as of the JSON data pipeline change (October 2026). It ends with a proposed build order for discussion.

**Key:** ✅ parity · 🟡 partial or different · ❌ missing · ➕ app-only (Excel doesn't have it)

## Summary

| Area | Status | Headline |
|---|---|---|
| Datasheet data (units, weapons, loadouts, rules, stratagems) | ✅ | Since 2.0 units, weapons, loadouts and points come from BSData (read-only, every faction); rules and stratagems are written in the app |
| Combat maths: weapon keywords | ✅ | Every keyword Excel models (stages 1–3), checked against Excel's results |
| Combat maths: stat modifiers (Hit/Wound/AP/… Modifier, Critical Hits X+, Reroll …) | ✅ | Excel's modifier language, with the ±1 cap (stage 2) |
| Battle conditions | ✅ | 11th edition cover, *Attacker Damaged*, Stealth (stage 3) |
| Faction rules and stratagems in the maths | ✅ | Tick panels on the Simulator (attacker and target) and Matchups (army-wide, both sides) (stage 4) |
| Single simulator (Sim-Shooting / Sim-Fighting) | ✅ | Several ticked weapons with Qty, per-weapon results and totals (stage 5) |
| Roster | ✅ | Unit sizes with points by size, generic target profiles with weights, army-wide rules (stages 4 and 6) |
| Results analyses | ✅ | Army Analysis panel on Matchups: summary, gaps, role scores with spider chart, T/Sv coverage, reverse durability (stage 7) |
| Maths approach | ✅ | Excel's capped damage and attacks-per-kill method (stage 8) |

## 1. Datasheet data

| v2 feature | App | Notes |
|---|---|---|
| Unit stats T, W, Sv, InSv, FNP | ✅ | |
| M, OC, Ld, Points, Models, Keywords, Leader (can lead), Abilities | ✅ | Stored and shown. Since 2.0 *can lead* and the Leader text are used: a leader can be attached in the Simulator and Matchups, adding its weapons, points and buffs (Excel had no leaders) |
| Unit Modifiers (column H, e.g. `Feel No Pain 5+`, keywords for Anti) | ✅ | Used as keywords for Anti; `Feel No Pain X+` here counts (best of this and the FNP column) |
| Loadout with Qty (models carrying each weapon) | ✅ | Pre-fills Simulator and Matchups quantities |
| Weapon profiles per faction | ✅ | |
| Weapon range | ➕ | App-only, for reference; Excel has no range column |
| Faction rules (Applies To, Phase, Modifier) | ✅ | Stored and editable on the Factions tab |
| Faction stratagems (CP, Applies To, Phase, Modifier, Notes) | ✅ | |
| Core stratagems (Keywords tab) | ✅ | |
| Keywords reference table (51 keywords, format, rule summary, "modelled?") | ✅ | Keywords tab on the Data page (searchable cards); forms warn about unrecognised keywords as you type |
| Adding a faction | ✅ | Easier in the app (one form, versus copying a tab and editing the Factions table) |

## 2. Combat maths: weapon and unit keywords

Excel marks each keyword as modelled or not on its Keywords tab. The table below lists every keyword Excel models.

| Keyword | Excel | App | Gap |
|---|---|---|---|
| Anti-X Y+ | Critical wound on Y+ vs keyword X | ✅ | Fixed in stage 1 (was parsed but never applied) |
| Blast / Blast X | +1 (or +X) attacks per 5 target models | ✅ | Fixed in stage 1: per firing model, from 5 target models, `Blast X` supported |
| Cleave X | +X attacks per 5 target models (melee) | ✅ | |
| Devastating Wounds | Critical wounds become mortal wounds equal to D | ✅ | Stage 1: critical-wound chance now includes re-rolls and Anti, as in Excel |
| Heavy | +1 to hit if stationary | ✅ | |
| Ignores Cover | Cover doesn't worsen BS | ✅ | |
| Lance | +1 to wound on the charge | ✅ | Melee only, inside the ±1 wound-modifier cap, as in Excel |
| Lethal Hits | Critical hits auto-wound | ✅ | Capitals now ignored, as in Excel |
| Melta X | +X damage at half range | ✅ | |
| Psychic | Ignores BS/WS modifiers, cover and negative hit modifiers | ✅ | |
| Rapid Fire X | +X attacks at half range | ✅ | Dice values (`Rapid Fire D3`) supported since stage 1 |
| Sustained Hits X | Critical hits score X extra hits | ✅ | Dice values supported since stage 1; critical-hit chance includes re-rolls, as in Excel |
| Torrent | Auto-hit | ✅ | Fixed in the last change: no longer counts as critical hits |
| Twin-Linked | Re-roll wound rolls | ✅ | |
| Damaged X | -1 to hit while at X or fewer wounds | ✅ | From the *Attacker Damaged* switch, as in Excel |
| Feel No Pain X+ | Ignore lost wounds on X+ | ✅ | FNP column, unit Modifiers and ticked target rules |
| Stealth | Ranged attacks against the unit count as in cover | ✅ | From the target's keywords or modifiers |

## 3. Combat maths: stat modifiers (Excel's modifier language)

Excel lets weapons, units, faction rules and stratagems carry these in their Modifier text. Since stage 2 the app reads them from weapon abilities and from the target's keywords/modifiers (rules and stratagems follow in stage 4).

**One deliberate difference:** Excel's Sim-Shooting sheet leaves its *Target AP Mod* cell empty, so a target's `AP Modifier` only works in melee there. The app applies it to shooting too.

| Modifier | Excel behaviour | App |
|---|---|---|
| Hit Modifier ±X, Wound Modifier ±X | Total hit/wound modifier capped at ±1 | ✅ |
| AP Modifier, Damage Modifier (min 1), Attacks Modifier (min 1), Strength Modifier (min 1) | Change the weapon's stat | ✅ |
| BS Modifier / WS Modifier | Change BS/WS (kept 2+–6+), not limited by the ±1 cap | ✅ |
| Toughness Modifier, Save Modifier | On a target rule: change the target's T/Sv | ✅ |
| Reroll Hits / Reroll Hits 1, Reroll Wounds / Reroll Wounds 1 | Re-rolls from a rule or weapon | ✅ Combined with the global dropdowns (better one applies) |
| Snap Shooting | Hit only on 6 (4+ if stationary) | ✅ |
| Critical Hits X+, Critical Wounds X+ | Widen the critical range; combines with Anti (lowest wins) | ✅ |

## 4. Battle conditions

| Condition | Excel | App |
|---|---|---|
| Within half range | ✅ Melta, Rapid Fire | ✅ |
| Attacker stationary | ✅ Heavy | ✅ |
| Target in cover | Worsens the attacker's **BS by 1** (11th edition), unless Ignores Cover/Psychic | ✅ Same rule (stage 3) |
| Charged this turn | ✅ Lance | ✅ |
| Attacker damaged | ✅ -1 to hit | ✅ Switch on the Simulator and Matchups |
| Target models | Typed in (Blast, Cleave, kills cap) | ✅ Defender model count, now defaulting to the datasheet |
| Re-rolls | Via `Reroll …` modifiers on ticked rules | ✅ Modifiers plus the global dropdowns |
| Phase (Shooting / Fighting / Both) on the Roster | ✅ | ✅ Matchups Shooting/Both/Melee filter |

## 5. Faction rules and stratagems in the maths

| v2 feature | App |
|---|---|
| Sim sheets: tick attacker rules/stratagems (faction + core) | ✅ Panel under the Attacker card |
| Sim sheets: tick target rules/stratagems (e.g. Stealth, Save Modifier) | ✅ Panel under the Defender card |
| Applies To (Attacker / Target) and Phase decide where a rule can be ticked | ✅ Lists follow the Shooting/Melee choice; the server also checks side, faction and phase per weapon |
| Roster: army-wide rules and stratagems ticked once, applied to every unit | ✅ Matchups "Rules & Stratagems" card; each faction's entries apply only to that faction's units, core stratagems to all |
| Opponent rules on the Roster | ➕ Excel's Roster has none; Matchups adds a Defenders column |
| Attacker/Target Effects summary | ✅ "Active: …" line on each panel |
| Entries with no modelled effect (Command Re-roll, Explosives…) | ✅ Listed greyed out, marked "No effect on the calculations" |
| Up to 15 rules/stratagems per side | ➕ No limit |

Ticks are remembered when switching tabs and saved with Scenarios and Comparisons.

## 6. Single simulator (Sim-Shooting / Sim-Fighting vs the Simulator page)

| v2 feature | App |
|---|---|
| Pick attacker faction and unit; weapons fill in | ✅ |
| Pick target unit from any faction ("Unit (Faction)") | ✅ (search by name; faction shown) |
| Separate Shooting and Fighting sheets | ✅ Shooting/Melee toggle |
| **Several weapons at once** (Use tick + Qty per weapon), totals across them | ✅ Use tick + Qty per weapon. Default: every weapon ticked except later profiles of a `Weapon - Profile` family (Excel ticks all, double-counting alternate profiles) |
| Per-weapon results: Attacks, Hits, Auto Wounds, Wounds, Mortal Wounds, Unsaved, Dmg per Attack, Total Damage, Models Killed | ✅ Per-weapon table with a total row |
| Totals: Total Damage, Total Models Killed, Weapons in Use, Check warnings | ✅ Totals in the summary cards; Monte Carlo rolls all ticked weapons together |
| Attacker Effects / Target Effects summaries | ✅ |
| Monte Carlo and damage distribution | ➕ |
| Saved scenarios | ➕ |

## 7. Roster vs Matchups

| v2 feature | App |
|---|---|
| My army: tick units, optional unit size (blank = datasheet) | ✅ Attacker table with a Size box per unit (scales weapon quantities from the loadout, as Excel's model scale); repeated units as "#2" |
| **Points scale with unit size** (datasheet points × models ÷ datasheet models) | ✅ Better than Excel: units list points per legal size (e.g. 10 = 65, 20 = 120); other sizes use the nearest listed size below plus its per-model rate |
| Opponent mode: **Generic profiles** (MEQ, TEQ, Heavy Infantry, Character, Light Vehicle, Monster, Battle Tank, Heavy Tank, Knight/Titanic, plus a horde profile), each with a **weight** | ✅ Real units / Generic profiles switch; profiles are read-only data (Data → Target Profiles), included by tick, weights shown in the matrix headers |
| Opponent mode: **Real roster** (opponent faction, ticked units, sizes) | ✅ Defender table, with points for the chosen size |
| Real roster: per-unit **Extra Mods** column (e.g. Stealth) | ✅ **Extra modifiers** box on each real defender row in Matchups (and on the Simulator's Defender card); applies to that defender only, never saved to the datasheet, not used when that unit attacks in reverse durability |
| Shared conditions and army-wide rules | ✅ |

## 8. Results analyses

| v2 Results section | App |
|---|---|
| Army summary: units, points, enemy units wiped, avg % destroyed, avg % per 100 pts, best unit | ✅ Army Analysis → Summary (plus the opponent's side in Real mode) |
| 1. Kill grid: models killed per unit × target, army total, % destroyed, wiped in one turn?, best unit | ✅ Matrix with "All attackers" row, % Destroyed metric and a "✓ wiped" marker |
| 2. % of target destroyed heat map and points efficiency (% per 100 pts) | ✅ % Destroyed and % per 100 pts in the Display menu |
| 3. Gaps: targets the whole army can't wipe in one turn | ✅ Army Analysis → Gaps, most survivors first |
| 4. Role scores: % of benchmark units destroyed (Anti-Horde … Anti-Tank, editable profiles) + spider chart | ✅ Table + spider chart; benchmarks are read-only data (Data → Target Profiles) |
| 5. Toughness/Save coverage: expected army damage over T 3–12 × Sv 2+–6+ | ✅ Heat grid |
| 6. Reverse durability: % of each of my units the opponent destroys (real roster mode) | ✅ Real units mode; opponent's default loadouts scaled to their size, no opponent rules |
| Generic-target weights in the averages | ✅ |
| Per-cell Monte Carlo detail with wipe % | ➕ |
| Per-weapon breakdown rows | ➕ |
| Saved comparisons | ➕ |

## 9. Maths approach

| Topic | Excel | App |
|---|---|---|
| Random values | Averages (D3 = 2, D6 = 3.5) | Analytical uses averages ✅; Monte Carlo rolls dice ➕ |
| Damage per attack vs target wounds | Capped at the target's W (no overkill per attack) | ✅ Both engines cap each attack's damage at W; "Overkill" now means damage lost to the cap |
| Models killed | Attacks needed per kill, then divided; capped at target models | ✅ Same method; a unit's weapons' kills add up (capped per unit), army totals add units' kills. Monte Carlo allocates model by model ➕ |
| Hit/Wound modifier cap ±1 | ✅ | n/a (no modifiers yet) |
| Mixed units (leader + bodyguard) | Not modelled (one target profile) | Attacking: modelled (leader's weapons and buffs). As a target: the leader's buffs apply, but the target is still one profile |

## 10. App-only features (keep)

Monte Carlo simulation and damage distributions; per-cell wipe %; saved scenarios and comparisons; editable data with a web UI; per-weapon matrix breakdown; repeated units with independent loadouts; tab state that survives switching pages.

## 11. Proposed build order (for discussion)

1. ~~**Engine fixes**: Anti-X, Blast at 5 models and `Blast X`, dice values in keyword numbers, FNP from unit Modifiers.~~ Done; checked against Excel's own results in `tests/test_simulation.py`.
2. ~~**Modifier language**~~ and 3. ~~**Conditions**~~: done together. Every keyword and modifier Excel models, the ±1 caps, 11th edition cover, Attacker Damaged, and the Keywords tab.
4. ~~**Rules and stratagems in use**~~: done. Tick panels on the Simulator, army-wide ticks for both sides on Matchups.
5. ~~**Simulator**~~: done. Several weapons at once, per-weapon results, Monte Carlo rolling them together; Matchups uses the same default ticks.
6. ~~**Roster**~~: done. Points by unit size, unit Size box on Matchups, generic profiles mode. (Per-defender Extra Mods not built.)
7. ~~**Results**~~: done. Army Analysis panel on Matchups.
8. ~~**Analytical maths**~~: done (built before stage 7).

**Remaining known gaps:** none. Every v2 feature is matched or exceeded.
