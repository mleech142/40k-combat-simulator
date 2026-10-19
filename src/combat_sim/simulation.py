"""
Core simulation engine for 40K Combat Simulator.

Follows the maths of 40KCombatSimulator_v2.xlsx (Sim-Shooting / Sim-Fighting):
the weapon's keywords and modifier text, plus the target's keywords and modifiers,
are resolved into the rolls needed (see `resolve`). The analytical engine then uses
average dice and probabilities, as the spreadsheet does; the Monte Carlo engine rolls
the same rolls with dice.

Modifier language (comma-separated, exact names from the Keywords tab, capitals ignored):
  weapon:  Lethal Hits, Sustained Hits X, Devastating Wounds, Torrent, Twin-Linked, Lance,
           Heavy, Rapid Fire X, Blast [X], Cleave X, Melta X, Ignores Cover, Psychic,
           Anti-<Keyword> X+, Snap Shooting, Critical Hits X+, Critical Wounds X+,
           Hit/Wound/AP/Damage/Attacks/Strength Modifier ±X, BS/WS Modifier ±X,
           Save Modifier -X, Reroll Hits [1], Reroll Wounds [1]
  target:  its keywords (for Anti), Feel No Pain X+, Stealth,
           Toughness/Save/Damage/Hit/Wound/AP Modifier ±X
X can be a number or dice (D3, D6+1); dice values are averaged in the analytical engine.
"""

import math
import random
import re
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

REROLL_RANK = {"none": 0, "ones": 1, "all": 2}


@dataclass
class AttackerProfile:
    """One weapon, as used by `models` models."""
    attacks: str  # "3", "D6", "2D6", "D3+1", etc.
    skill: Optional[int]  # BS or WS (3+ = 3); None = no roll ("-"), always hits
    strength: int
    ap: int  # Positive number (AP-2 = 2)
    damage: str  # "1", "D6", "D3+3", etc.
    abilities: list[str]  # Weapon keywords/modifiers, plus any attacker rule modifiers
    models: int = 1  # Number of models attacking with this weapon
    weapon_type: str = "ranged"  # "ranged" (shooting) or "melee" (fighting)

    def __post_init__(self):
        self.abilities = [a.strip() for a in (self.abilities or []) if a and a.strip()]

    @property
    def is_melee(self):
        return self.weapon_type == "melee"


@dataclass
class DefenderProfile:
    """The target unit."""
    toughness: int
    save: int  # 3+ = 3
    wounds: int
    invulnerable_save: Optional[int] = None
    feel_no_pain: Optional[int] = None
    keywords: list[str] = None  # Unit keywords and modifiers (and any target rule modifiers)
    model_count: int = 1

    def __post_init__(self):
        if self.keywords is None:
            self.keywords = []
        # Feel No Pain written as a modifier also counts (best value wins), as in Excel
        fnp = keyword_min(self.keywords, "Feel No Pain")
        if fnp:
            fnp = int(fnp)
            self.feel_no_pain = min(self.feel_no_pain, fnp) if self.feel_no_pain else fnp


@dataclass
class BattleConditions:
    """Switches active during the combat."""
    half_range: bool = False  # Rapid Fire, Melta (shooting)
    charged: bool = False  # Lance (fighting)
    stationary: bool = False  # Heavy, Snap Shooting (shooting)
    cover: bool = False  # Target in cover: attacker's BS worsened by 1 (shooting)
    damaged: bool = False  # Attacker damaged: -1 to hit
    reroll_hits: str = "none"  # "none", "ones", "all" (combined with Reroll Hits modifiers)
    reroll_wounds: str = "none"


@dataclass
class SimulationResult:
    """Results from a combat simulation."""
    # Summary
    average_damage: float
    models_killed: float
    overkill: float

    # Breakdown
    attacks_made: float
    hits: float
    wounds: float
    failed_saves: float
    damage_dealt: float

    # Distribution (for Monte Carlo)
    damage_distribution: Optional[dict] = None

    # Metadata
    method: str = "analytical"
    iterations: int = 0

    # Monte Carlo only: % chance to kill at least one model / the whole unit
    kill_probability: Optional[float] = None
    wipe_probability: Optional[float] = None

    # Analytical only: Excel's extra per-weapon columns
    auto_wounds: Optional[float] = None  # Lethal Hits
    mortal_wounds: Optional[float] = None  # Devastating Wounds
    unsaved: Optional[float] = None  # Failed saves (excluding mortal wounds)
    damage_per_attack: Optional[float] = None


# ---------------------------------------------------------------------------
# Dice
# ---------------------------------------------------------------------------

def parse_dice_expression(expr: str) -> tuple[int, int, int]:
    """
    Parse a dice expression like "D6", "2D3", "D6+2", "3", "+1".

    Returns (num_dice, die_size, modifier): "D6" -> (1, 6, 0), "2D3" -> (2, 3, 0),
    "D6+2" -> (1, 6, 2), "3" -> (0, 0, 3).
    """
    expr = str(expr).upper().replace(' ', '')

    if 'D' not in expr:
        return (0, 0, int(expr))

    dice_part, modifier = expr, 0
    for sign in ('+', '-'):
        if sign in expr[1:]:
            idx = expr.index(sign, 1)
            dice_part, modifier = expr[:idx], int(expr[idx:])
            break

    num, sides = dice_part.split('D')
    return (int(num) if num else 1, int(sides), modifier)


def average_dice(expr: str) -> float:
    """Average value of a dice expression."""
    num_dice, die_size, modifier = parse_dice_expression(expr)
    if num_dice == 0:
        return float(modifier)
    return num_dice * (die_size + 1) / 2 + modifier


def roll_dice(expr: str) -> int:
    """Roll a dice expression (minimum 1)."""
    num_dice, die_size, modifier = parse_dice_expression(expr)
    if num_dice == 0:
        return modifier
    total = sum(random.randint(1, die_size) for _ in range(num_dice))
    return max(1, total + modifier)


def get_wound_roll_needed(strength: int, toughness: int) -> int:
    """Roll needed to wound from S vs T."""
    if strength >= toughness * 2:
        return 2
    elif strength > toughness:
        return 3
    elif strength == toughness:
        return 4
    elif strength * 2 <= toughness:
        return 6
    else:
        return 5


# ---------------------------------------------------------------------------
# Keyword values (mirror the Excel KW / KWSUM / KWMIN / KWANTI helpers)
# ---------------------------------------------------------------------------

def keyword_entries(abilities: list[str], key: str) -> list[str]:
    """Values written after `key`: 'Sustained Hits D3' -> ['D3']; a bare keyword -> ['']. Capitals ignored."""
    entries = []
    for ability in abilities:
        if ability.lower().startswith(key.lower()):
            rest = ability[len(key):]
            if rest == '' or rest[0] == ' ':
                entries.append(rest.strip().rstrip('+').strip())
    return entries


def has_keyword(abilities: list[str], key: str) -> bool:
    return bool(keyword_entries(abilities, key))


def _value_avg(value: str) -> float:
    """'' (bare keyword) counts as 1, as in Excel; numbers and dice average."""
    if value == '':
        return 1.0
    try:
        return average_dice(value)
    except (ValueError, IndexError):
        return 0.0


def keyword_avg(abilities: list[str], key: str) -> float:
    """Largest average value of a keyword; 0 if absent (Excel KW)."""
    return max((_value_avg(v) for v in keyword_entries(abilities, key)), default=0.0)


def keyword_sum(abilities: list[str], key: str) -> float:
    """Sum of a keyword's values, e.g. two Hit Modifiers (Excel KWSUM)."""
    return sum(_value_avg(v) for v in keyword_entries(abilities, key) if v != '')


def keyword_min(abilities: list[str], key: str) -> Optional[float]:
    """Smallest numeric value of a keyword, or None (Excel KWMIN)."""
    values = [_value_avg(v) for v in keyword_entries(abilities, key) if v != '']
    return min(values) if values else None


def keyword_roll(abilities: list[str], key: str) -> int:
    """Rolled value of a keyword (the entry with the largest average); 0 if absent."""
    entries = keyword_entries(abilities, key)
    if not entries:
        return 0
    value = max(entries, key=_value_avg)
    return 1 if value == '' else roll_dice(value)


def keyword_name(entry: str) -> str:
    """
    The keyword an entry refers to, as written: 'Anti-Infantry 2+' -> 'Anti',
    'Sustained Hits D3' -> 'Sustained Hits', 'Hit Modifier +1' -> 'Hit Modifier'.
    """
    text = entry.strip()
    if text.lower().startswith('anti-'):
        return text[:4]
    return re.sub(r'\s+[-+]?(\d+|D\d+)([-+]\d+)?\+?"?$', '', text, flags=re.IGNORECASE).strip()


def check_keywords(text: str, known_names, mode: str = '') -> dict:
    """
    Check comma-separated keyword text against the known keyword names.

    Returns {'unknown': [entries], 'capitals': [[entry, canonical name]]}. In mode 'unit',
    plain words are fine (they are unit keywords used by Anti); only entries with values
    are checked. Capitals never change the maths; they are reported as a tidy-up hint.
    """
    names = {name.lower(): name for name in known_names}
    unknown, capitals = [], []
    for entry in (part.strip() for part in (text or '').split(',')):
        if not entry or entry.lower() == 'none':
            continue
        name = keyword_name(entry)
        canonical = names.get(name.lower())
        if canonical:
            if name.lower() != 'anti' and not entry.startswith(canonical):
                capitals.append([entry, canonical])
        elif mode != 'unit' or re.search(r'\d', entry):
            unknown.append(entry)
    return {'unknown': unknown, 'capitals': capitals}


def has_exact(abilities: list[str], key: str) -> bool:
    """The bare keyword with no value, e.g. 'Reroll Hits' (not 'Reroll Hits 1')."""
    return '' in keyword_entries(abilities, key)


def anti_threshold(abilities: list[str], target_keywords: list[str]) -> Optional[int]:
    """Lowest 'Anti-X Y+' value whose keyword X the target has, else None."""
    target = {k.strip().upper() for k in target_keywords}
    best = None
    for ability in abilities:
        if not ability.lower().startswith('anti-'):
            continue
        parts = ability[5:].rsplit(' ', 1)
        if len(parts) != 2:
            continue
        keyword, value = parts[0].strip().upper(), parts[1].strip().rstrip('+')
        if keyword in target and value.isdigit():
            best = int(value) if best is None else min(best, int(value))
    return best


def clamp(value, low, high):
    return max(low, min(high, value))


# ---------------------------------------------------------------------------
# Probabilities with re-rolls (same formulas as the Excel sheet)
# ---------------------------------------------------------------------------

def reroll_success(p: float, reroll: str) -> float:
    """Chance a roll succeeds, given its base chance p and the re-roll mode."""
    if reroll == "all":
        return p + (1 - p) * p
    if reroll == "ones":
        return p + p / 6
    return p


def reroll_critical(pc: float, p: float, reroll: str) -> float:
    """Chance a roll is a critical (base chance pc), when failures (base success chance p) are re-rolled."""
    if reroll == "all":
        return pc + (1 - p) * pc
    if reroll == "ones":
        return pc + pc / 6
    return pc


def probability_to_succeed(target: int, reroll: str = "none") -> float:
    """Probability of succeeding on a D6 roll of target+, with re-rolls."""
    if target > 6:
        return 0.0
    if target <= 1:
        return 1.0
    return reroll_success((7 - target) / 6, reroll)


def _best_reroll(*modes: str) -> str:
    return max(modes, key=lambda m: REROLL_RANK.get(m, 0))


# ---------------------------------------------------------------------------
# Resolving one weapon against one target (Excel's helper columns)
# ---------------------------------------------------------------------------

@dataclass
class ResolvedAttack:
    """Everything the engines need for one weapon vs one target."""
    attacks_per_model_avg: float  # Including Rapid Fire / Blast / Cleave bonuses
    attacks_bonus_flat: float  # Attacks Modifier (added to the rolled A, min 1)
    auto_hit: bool  # Torrent / no BS-WS: every attack hits, never critical
    hit_need: int
    crit_hit_on: int
    hit_reroll: str
    sustained: float  # Average extra hits per critical hit
    lethal: bool
    wound_need: int
    crit_wound_on: int
    wound_reroll: str
    devastating: bool
    save_need: int  # 7 = no save
    damage_bonus: float  # Melta (if active) + damage modifiers
    snap_shooting: bool
    extra_attacks: list[str] = field(default_factory=list)  # Keywords whose values are rolled per model in MC


def resolve(attacker: AttackerProfile, defender: DefenderProfile, conditions: BattleConditions) -> ResolvedAttack:
    """Rolls needed and modifiers for this weapon against this target (Excel columns AA:BD)."""
    ab = attacker.abilities
    target = defender.keywords
    shooting = not attacker.is_melee

    # Attacks per model: Rapid Fire (shooting, half range), Blast and Cleave per 5 target models
    per_model_bonus_keys = ["Blast", "Cleave"]
    if shooting and conditions.half_range:
        per_model_bonus_keys.append("Rapid Fire")
    bonus = 0.0
    for key in per_model_bonus_keys:
        value = keyword_avg(ab, key)
        bonus += value * (defender.model_count // 5) if key in ("Blast", "Cleave") else value
    attacks_modifier = keyword_sum(ab, "Attacks Modifier")
    attacks_per_model = max(1.0, average_dice(attacker.attacks) + attacks_modifier) + bonus

    # Hit roll
    psychic = has_keyword(ab, "Psychic")
    snap = shooting and has_keyword(ab, "Snap Shooting")
    auto_hit = attacker.skill is None or has_keyword(ab, "Torrent")
    if snap:
        hit_need = 4 if conditions.stationary else 6
        crit_hit_on = 6
    else:
        heavy = 1 if (shooting and has_keyword(ab, "Heavy") and conditions.stationary) else 0
        damaged = -1 if conditions.damaged else 0
        weapon_hit_modifier = keyword_sum(ab, "Hit Modifier")
        target_hit_modifier = keyword_sum(target, "Hit Modifier")
        if psychic:
            hit_modifiers = max(0, heavy) + max(0, weapon_hit_modifier) + max(0, target_hit_modifier)
        else:
            hit_modifiers = heavy + damaged + weapon_hit_modifier + target_hit_modifier

        skill_modifier = keyword_sum(ab, "WS Modifier" if attacker.is_melee else "BS Modifier")
        in_cover = shooting and (conditions.cover or has_keyword(target, "Stealth"))
        cover = 1 if (in_cover and not has_keyword(ab, "Ignores Cover") and not psychic) else 0
        skill = attacker.skill if attacker.skill is not None else 2
        skill = clamp(skill + (min(0, skill_modifier) if psychic else skill_modifier + cover), 2, 6)
        hit_need = int(clamp(skill - clamp(hit_modifiers, -1, 1), 2, 6))

        crit_hits = keyword_min(ab, "Critical Hits")
        crit_hit_on = int(clamp(crit_hits, 2, 6)) if crit_hits else 6

    # Re-rolls: the better of the battle-condition dropdown and the weapon's modifiers
    if snap:
        hit_reroll = "none"
    else:
        weapon_hit_reroll = "all" if has_exact(ab, "Reroll Hits") else ("ones" if has_keyword(ab, "Reroll Hits") else "none")
        hit_reroll = _best_reroll(conditions.reroll_hits, weapon_hit_reroll)
    weapon_wound_reroll = "all" if (has_keyword(ab, "Twin-Linked") or has_exact(ab, "Reroll Wounds")) else (
        "ones" if has_keyword(ab, "Reroll Wounds") else "none")
    wound_reroll = _best_reroll(conditions.reroll_wounds, weapon_wound_reroll)

    # Wound roll
    strength = max(1, attacker.strength + keyword_sum(ab, "Strength Modifier"))
    toughness = max(1, defender.toughness + keyword_sum(target, "Toughness Modifier"))
    lance = 1 if (attacker.is_melee and has_keyword(ab, "Lance") and conditions.charged) else 0
    wound_modifier = keyword_sum(ab, "Wound Modifier") + lance + keyword_sum(target, "Wound Modifier")
    wound_need = int(clamp(get_wound_roll_needed(strength, toughness) - clamp(wound_modifier, -1, 1), 2, 6))

    anti = anti_threshold(ab, target)
    crit_wounds = keyword_min(ab, "Critical Wounds")
    crit_wound_on = min(anti or 6, int(clamp(crit_wounds, 2, 6)) if crit_wounds else 6)

    # Save roll (AP and Save modifiers; best of armour and invulnerable)
    ap = min(0, -attacker.ap + keyword_sum(ab, "AP Modifier") + keyword_sum(target, "AP Modifier"))
    armour = defender.save - ap - keyword_sum(target, "Save Modifier") - keyword_sum(ab, "Save Modifier")
    invuln = defender.invulnerable_save or 7
    save_need = int(clamp(min(armour, invuln), 2, 7))

    # Damage
    damage_bonus = keyword_sum(ab, "Damage Modifier") + keyword_sum(target, "Damage Modifier")
    if shooting and conditions.half_range:
        damage_bonus += keyword_avg(ab, "Melta")

    return ResolvedAttack(
        attacks_per_model_avg=attacks_per_model,
        attacks_bonus_flat=attacks_modifier,
        auto_hit=auto_hit and not snap,
        hit_need=hit_need,
        crit_hit_on=crit_hit_on,
        hit_reroll=hit_reroll,
        sustained=keyword_avg(ab, "Sustained Hits"),
        lethal=has_keyword(ab, "Lethal Hits"),
        wound_need=wound_need,
        crit_wound_on=crit_wound_on,
        wound_reroll=wound_reroll,
        devastating=has_keyword(ab, "Devastating Wounds"),
        save_need=save_need,
        damage_bonus=damage_bonus,
        snap_shooting=snap,
        extra_attacks=per_model_bonus_keys,
    )


# ---------------------------------------------------------------------------
# Analytical engine (average outcome, the Excel sheet's method)
# ---------------------------------------------------------------------------

def simulate_analytical(
    attacker: AttackerProfile,
    defender: DefenderProfile,
    conditions: BattleConditions
) -> SimulationResult:
    """Expected damage from average dice and probabilities."""
    r = resolve(attacker, defender, conditions)

    attacks = r.attacks_per_model_avg * attacker.models

    # Hits (critical hits trigger Lethal Hits and Sustained Hits)
    if r.auto_hit:
        hits = attacks
        crit_hits = 0.0
    else:
        p_crit = (7 - r.crit_hit_on) / 6
        p_hit = max((7 - r.hit_need) / 6, p_crit)
        hits = attacks * reroll_success(p_hit, r.hit_reroll)
        crit_hits = attacks * reroll_critical(p_crit, p_hit, r.hit_reroll)
    hits += crit_hits * r.sustained
    lethal_wounds = crit_hits if r.lethal else 0.0

    # Wounds: success on the wound roll needed or a Critical Wound
    wound_pool = hits - lethal_wounds
    p_crit_wound = (7 - r.crit_wound_on) / 6
    p_wound = max((7 - r.wound_need) / 6, p_crit_wound)
    wounds = wound_pool * reroll_success(p_wound, r.wound_reroll) + lethal_wounds
    crit_wounds = wound_pool * reroll_critical(p_crit_wound, p_wound, r.wound_reroll)
    mortal_wounds = crit_wounds if r.devastating else 0.0

    # Saves and damage. As in Excel, each attack's damage is capped at the target's wounds
    # (damage beyond a model's wounds is lost), and Feel No Pain scales what gets through.
    unsaved = (wounds - mortal_wounds) * (r.save_need - 1) / 6
    damage_per = max(1.0, average_dice(attacker.damage) + r.damage_bonus)
    effective_per = min(damage_per, defender.wounds)
    fnp_factor = (defender.feel_no_pain - 1) / 6 if defender.feel_no_pain else 1.0
    damaging_attacks = unsaved + mortal_wounds
    total_damage = damaging_attacks * effective_per * fnp_factor

    # Models killed: attacks needed per kill, then divided (Excel's "Attacks per Kill")
    if fnp_factor > 0:
        attacks_per_kill = math.ceil(round(defender.wounds / effective_per, 9)) / fnp_factor
        models_killed = min(defender.model_count, damaging_attacks / attacks_per_kill)
    else:
        models_killed = 0.0
    overkill = damaging_attacks * (damage_per - effective_per) * fnp_factor  # Damage lost to the cap

    return SimulationResult(
        average_damage=round(total_damage, 2),
        models_killed=round(models_killed, 2),
        overkill=round(overkill, 2),
        attacks_made=round(attacks, 2),
        hits=round(hits, 2),
        wounds=round(wounds, 2),
        failed_saves=round(unsaved + mortal_wounds, 2),
        damage_dealt=round(total_damage, 2),
        method="analytical",
        auto_wounds=round(lethal_wounds, 2),
        mortal_wounds=round(mortal_wounds, 2),
        unsaved=round(unsaved, 2),
        damage_per_attack=round(damage_per, 2),
    )


# ---------------------------------------------------------------------------
# Monte Carlo engine (rolls the same rolls with dice)
# ---------------------------------------------------------------------------

def _roll_d6(need: int, crit_on: int, reroll: str) -> tuple[bool, bool]:
    """Roll one D6 against `need` (or a critical on `crit_on`+), re-rolling failures per `reroll`."""
    roll = random.randint(1, 6)
    ok = roll >= need or roll >= crit_on
    if not ok and (reroll == "all" or (reroll == "ones" and roll == 1)):
        roll = random.randint(1, 6)
        ok = roll >= need or roll >= crit_on
    return ok, ok and roll >= crit_on


def _roll_weapon(
    attacker: AttackerProfile,
    defender: DefenderProfile,
    conditions: BattleConditions
) -> tuple[dict, list[int]]:
    """
    Roll one weapon's attacks (for all its models) once.

    Returns (counts, damage_instances): counts of attacks/hits/wounds/failed saves,
    and the damage of each unsaved wound or mortal wound after Feel No Pain.
    """
    r = resolve(attacker, defender, conditions)
    ab = attacker.abilities

    # Attacks for every model, with per-model Rapid Fire / Blast / Cleave bonuses
    num_attacks = 0
    for _ in range(attacker.models):
        num_attacks += max(1, roll_dice(attacker.attacks) + round(r.attacks_bonus_flat))
        for key in r.extra_attacks:
            value = keyword_roll(ab, key)
            num_attacks += value * (defender.model_count // 5) if key in ("Blast", "Cleave") else value

    # Hits
    hits = 0
    lethal_wounds = 0
    if r.auto_hit:
        hits = num_attacks
    else:
        for _ in range(num_attacks):
            ok, crit = _roll_d6(r.hit_need, r.crit_hit_on, r.hit_reroll)
            if not ok:
                continue
            if crit:
                if r.lethal:
                    lethal_wounds += 1
                else:
                    hits += 1
                if r.sustained:
                    hits += keyword_roll(ab, "Sustained Hits")
            else:
                hits += 1

    # Wounds
    wounds = lethal_wounds  # Lethal hits auto-wound (and aren't Critical Wounds)
    mortal_wounds = 0
    for _ in range(hits):
        ok, crit = _roll_d6(r.wound_need, r.crit_wound_on, r.wound_reroll)
        if not ok:
            continue
        if crit and r.devastating:
            mortal_wounds += 1
        else:
            wounds += 1

    # Saves
    failed_saves = sum(1 for _ in range(wounds) if random.randint(1, 6) < r.save_need)

    # Damage per unsaved wound / mortal wound, then Feel No Pain per point of damage
    melta_active = not attacker.is_melee and conditions.half_range
    flat_bonus = r.damage_bonus - (keyword_avg(ab, "Melta") if melta_active else 0)
    damage_instances = []
    for _ in range(failed_saves + mortal_wounds):
        dmg = roll_dice(attacker.damage) + flat_bonus
        if melta_active:
            dmg += keyword_roll(ab, "Melta")
        dmg = min(max(1, int(round(dmg))), defender.wounds)  # Damage beyond a model's wounds is lost
        if defender.feel_no_pain:
            dmg = sum(1 for _ in range(dmg) if random.randint(1, 6) < defender.feel_no_pain)
        if dmg > 0:
            damage_instances.append(dmg)

    counts = {
        'attacks': num_attacks,
        'hits': hits + lethal_wounds,
        'wounds': wounds + mortal_wounds,
        'failed_saves': failed_saves + mortal_wounds,
    }
    return counts, damage_instances


def _allocate_damage(damage_instances: list[int], defender: DefenderProfile) -> int:
    """
    Apply damage to defender models one at a time and return models killed.

    Each damage instance hits the current model; damage beyond its remaining
    wounds is lost (no spill-over), as in the core rules.
    """
    killed = 0
    remaining = defender.wounds
    for dmg in damage_instances:
        if killed >= defender.model_count:
            break
        remaining -= dmg
        if remaining <= 0:
            killed += 1
            remaining = defender.wounds
    return killed


def simulate_monte_carlo_combined(
    attackers: list[AttackerProfile],
    defender: DefenderProfile,
    conditions: BattleConditions,
    iterations: int = 1000,
    weapon_defenders: Optional[list[DefenderProfile]] = None,
) -> SimulationResult:
    """
    Simulate several weapons attacking the same defender together.

    Every iteration rolls all the weapons, then allocates their combined damage
    to the defending models, so models killed and wipe chance are per-trial.
    `weapon_defenders` optionally gives the target as seen by each weapon (e.g. a
    shooting-only target stratagem); damage is still allocated to `defender`.
    """
    targets = weapon_defenders or [defender] * len(attackers)
    damage_results = []
    kills_results = []
    totals = {'attacks': 0, 'hits': 0, 'wounds': 0, 'failed_saves': 0}

    for _ in range(iterations):
        instances = []
        for attacker, target in zip(attackers, targets):
            counts, weapon_instances = _roll_weapon(attacker, target, conditions)
            instances.extend(weapon_instances)
            for key in totals:
                totals[key] += counts[key]

        damage_results.append(sum(instances))
        kills_results.append(_allocate_damage(instances, defender))

    damage_array = np.array(damage_results)
    kills_array = np.array(kills_results)
    avg_damage = float(np.mean(damage_array))

    # Distribution
    max_damage = int(np.max(damage_array)) + 1
    bins = list(range(min(max_damage + 1, 20)))
    hist, _ = np.histogram(damage_array, bins=bins + [bins[-1] + 100000])

    distribution = {
        "labels": [str(b) for b in bins[:-1]] + [f"{bins[-1]}+"],
        "probabilities": (hist / iterations * 100).tolist()
    }

    return SimulationResult(
        average_damage=round(avg_damage, 2),
        models_killed=round(float(np.mean(kills_array)), 2),
        overkill=round(max(0, avg_damage - defender.model_count * defender.wounds), 2),
        attacks_made=round(totals['attacks'] / iterations, 2),
        hits=round(totals['hits'] / iterations, 2),
        wounds=round(totals['wounds'] / iterations, 2),
        failed_saves=round(totals['failed_saves'] / iterations, 2),
        damage_dealt=round(avg_damage, 2),
        damage_distribution=distribution,
        method="monte_carlo",
        iterations=iterations,
        kill_probability=round(float(np.mean(kills_array >= 1)) * 100, 1),
        wipe_probability=round(float(np.mean(kills_array >= defender.model_count)) * 100, 1),
    )


def simulate_monte_carlo(
    attacker: AttackerProfile,
    defender: DefenderProfile,
    conditions: BattleConditions,
    iterations: int = 1000
) -> SimulationResult:
    """Simulate one weapon with dice rolls; gives a damage distribution."""
    return simulate_monte_carlo_combined([attacker], defender, conditions, iterations)


def simulate(
    attacker: AttackerProfile,
    defender: DefenderProfile,
    conditions: BattleConditions,
    method: str = "analytical",
    iterations: int = 1000
) -> SimulationResult:
    """Run a combat simulation ("analytical" or "monte_carlo")."""
    if method == "monte_carlo":
        return simulate_monte_carlo(attacker, defender, conditions, iterations)
    return simulate_analytical(attacker, defender, conditions)
