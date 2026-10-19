# BSData import: open findings

What the importer's report (`combat_sim.bsdata.import_folder(...).report`) flags on the real data, to investigate later. None of it blocks the migration: the affected units still import.

Run: BSData/wh40k-11e at commit `b8667d4` (2026-10-19), 46 files → 37 factions, 3,250 units, 6,532 ranged and 3,222 melee weapons.

| Kind | Count | What it means | Likely fix |
| --- | --- | --- | --- |
| `leader` | 1,419 | A Leader ability names a unit that isn't in the faction (counted once per leader per faction, so Space Marines names repeat across the 11 chapter factions). | Check whether these are renamed or removed datasheets, or units only in Legends/other catalogues. Could match on BSData's `Leading` associations instead of the ability text. |
| `unknown_keyword` | 231 | A weapon keyword the simulator doesn't know. | See below: mostly conditional keywords the engine could support. |
| `mixed_profile` | 81 | Models in a unit have different stat lines; the most numerous model's is used. | Model-level profiles (backlog item from the grilling session). |
| `weapon_stats` | 37 | Weapon with strength `*`; skipped. All are Vortex Grenade (one per faction). | Leave skipped, or model it with an overlay entry. |
| `points` | 3 | Cost modifiers the importer can't evaluate; base costs used. Beasts of Nurgle, Ironstrider Ballistarii, Lokhust Heavy Destroyers. | Look at their modifiers individually. |
| `missing_link` | 1 | Library - Astartes Heresy Legends: profile link "Terror of Khorne" points at nothing. | BSData bug; report upstream. |
| `skipped_unit` | 1 | Unaligned Forces / Searchlight has no Unit profile. | Expected (it's a wargear token). |

## Detachments

- 12 detachments have no rule text in BSData (their rule is probably written elsewhere, e.g. as a modifier); they are still listed.
- Titanicus Traitoris, Adeptus Titanicus and Unaligned Forces have no detachments.
- Detachments are taken from the faction's own catalogue and those it takes units from; Agents of the Imperium detachments no longer appear in every Imperium army, and Chaos Daemons' only in Chaos Daemons. Factions can still list detachments BSData hides with conditions (e.g. Aeldari and Drukhari both list all 24 shared ones).

## Unknown weapon keywords (top)

| Count | Keyword |
| --- | --- |
| 48 | Conversion |
| 37 + 25 + 24 + 24 + 12 + … | Conditional keywords: "Devastating Wounds: non-Monster/Vehicle", "Lethal Hits: non-Monster/Vehicle", "Sustained Hits 2: non-Monster/Vehicles", "Devastating Wounds: INFANTRY", "Devastating Wounds: PSYKER", in varying capitals |
| 24 | Defensive Array |
| 4 | Twin Linked (no hyphen) |
| 2 each | Reverberating Summons, Impaled |

The conditional form "Keyword: target keywords" is the big one. Supporting it in `resolve()` (the keyword applies only if the target has, or lacks, the listed keywords) would clear most of the list; "Twin Linked" just needs normalising.

## Leader targets most often unmatched

BIKE SQUAD, COMPANY VETERANS ON BIKES, FORTIS KILL TEAM, DECIMUS KILL TEAM, VANGUARD VETERAN SQUAD, DEATHWATCH VETERANS, INNER CIRCLE COMPANIONS, CRUSADER SQUAD, SWORD BRETHREN SQUAD, DEATHWATCH TERMINATOR SQUAD, TACTICAL SQUAD, DEATHWING KNIGHTS, DEATHWING TERMINATOR SQUAD, SPECTRUS KILL TEAM, RELIC TERMINATOR SQUAD.

Most are units of another chapter's catalogue (Deathwatch, Dark Angels, Black Templars) named in the shared Space Marines leaders' text, so they are expected misses in the other chapters' factions.
