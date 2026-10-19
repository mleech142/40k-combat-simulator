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
  target:  its keywords (for Anti), Feel No Pain X+, Invulnerable Save X+, Stealth,
           Toughness/Save/Damage/Hit/Wound/AP Modifier ±X
X can be a number or dice (D3, D6+1); dice values are averaged in the analytical engine.

Attached units (a leader with its bodyguard unit) as a target: every attack goes to the
bodyguard models until they are all destroyed, then to the leader, each stage with its own
profile (simulate_attached_analytical / simulate_monte_carlo_attached). Precision isn't modelled.
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
        # So does an invulnerable save given by a modifier (e.g. a leader's), best value wins
        invuln = keyword_min(self.keywords, "Invulnerable Save")
        if invuln:
            invuln = int(invuln)
            self.invulnerable_save = min(self.invulnerable_save, invuln) if self.invulnerable_save else invuln


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

    # Attached units only: the bodyguard models and the leader, separately (models_killed is both)
    bodyguards_killed: Optional[float] = None
    leader_killed: Optional[float] = None  # Expected leader models killed (analytical) or average (Monte Carlo)
    leader_kill_probability: Optional[float] = None  # Monte Carlo: % of trials the leader died

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
    c = _analytical_core(attacker, defender, conditions)
    return SimulationResult(
        average_damage=round(c['damage'], 2),
        models_killed=round(min(defender.model_count, c['kills']), 2),
        overkill=round(c['overkill'], 2),
        attacks_made=round(c['attacks'], 2),
        hits=round(c['hits'], 2),
        wounds=round(c['wounds'], 2),
        failed_saves=round(c['damaging'], 2),
        damage_dealt=round(c['damage'], 2),
        method="analytical",
        auto_wounds=round(c['lethal'], 2),
        mortal_wounds=round(c['mortal'], 2),
        unsaved=round(c['unsaved'], 2),
        damage_per_attack=round(c['damage_per'], 2),
    )


# Quantities of the analytical engine that scale with the number of attacks
SCALED = ('attacks', 'hits', 'wounds', 'lethal', 'mortal', 'unsaved', 'damaging', 'damage', 'kills', 'overkill')


def _analytical_core(attacker: AttackerProfile, defender: DefenderProfile, conditions: BattleConditions) -> dict:
    """The analytical engine's expected values for one weapon against one profile (kills uncapped)."""
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
        models_killed = damaging_attacks / attacks_per_kill
    else:
        models_killed = 0.0
    overkill = damaging_attacks * (damage_per - effective_per) * fnp_factor  # Damage lost to the cap

    return {
        'attacks': attacks, 'hits': hits, 'wounds': wounds, 'lethal': lethal_wounds, 'mortal': mortal_wounds,
        'unsaved': unsaved, 'damaging': damaging_attacks, 'damage': total_damage, 'kills': models_killed,
        'overkill': overkill, 'damage_per': damage_per,
    }


def simulate_attached_analytical(
    attackers: list[AttackerProfile],
    bodyguard: DefenderProfile,
    leader: DefenderProfile,
    conditions: BattleConditions,
    bodyguard_targets: Optional[list[DefenderProfile]] = None,
    leader_targets: Optional[list[DefenderProfile]] = None,
) -> tuple[SimulationResult, list[SimulationResult]]:
    """
    Weapons attacking a leader with its bodyguard unit, in order: each weapon's expected kills go
    to the bodyguard models still standing; once a weapon would destroy the last of them, the
    rest of its attacks (and every later weapon's) are resolved against the leader's profile.
    `bodyguard_targets` / `leader_targets` optionally give each stage as each weapon sees it
    (phase-specific target rules). Returns the combined result and one result per weapon, each
    with bodyguards_killed and leader_killed (models_killed is both).
    """
    bodyguard_targets = bodyguard_targets or [bodyguard] * len(attackers)
    leader_targets = leader_targets or [leader] * len(attackers)
    bodyguards_left = float(bodyguard.model_count)
    leaders_left = float(leader.model_count)
    per_weapon = []
    for attacker, on_bodyguard, on_leader in zip(attackers, bodyguard_targets, leader_targets):
        b = _analytical_core(attacker, on_bodyguard, conditions)
        # Share of this weapon's attacks spent on bodyguards (the rest overflow to the leader)
        if bodyguards_left <= 0:
            share = 0.0
        elif b['kills'] > bodyguards_left:
            share = bodyguards_left / b['kills']
        else:
            share = 1.0
        bodyguard_kills = share * b['kills']
        bodyguards_left = max(0.0, bodyguards_left - bodyguard_kills)
        part = {key: share * b[key] for key in SCALED}
        part['kills'] = bodyguard_kills

        leader_kills = 0.0
        if share < 1.0 and b['attacks'] > 0:
            l = _analytical_core(attacker, on_leader, conditions)
            # The overflowing attacks, with the leader's odds per attack
            scale = (1 - share) * b['attacks'] / l['attacks'] if l['attacks'] else 0.0
            for key in SCALED:
                part[key] += scale * l[key]
            leader_kills = min(leaders_left, scale * l['kills'])
            leaders_left -= leader_kills
            part['kills'] = bodyguard_kills + leader_kills
        per_weapon.append(_attached_result(part, b['damage_per'], bodyguard_kills, leader_kills))

    total = {key: sum(getattr(w, FIELD_OF[key]) for w in per_weapon) for key in FIELD_OF}
    combined = SimulationResult(
        average_damage=round(total['damage'], 2),
        models_killed=round(total['kills'], 2),
        overkill=round(total['overkill'], 2),
        attacks_made=round(total['attacks'], 2),
        hits=round(total['hits'], 2),
        wounds=round(total['wounds'], 2),
        failed_saves=round(total['damaging'], 2),
        damage_dealt=round(total['damage'], 2),
        method="analytical",
        bodyguards_killed=round(bodyguard.model_count - bodyguards_left, 2),
        leader_killed=round(leader.model_count - leaders_left, 2),
    )
    return combined, per_weapon


# Result field for each analytical quantity summed over weapons
FIELD_OF = {'damage': 'average_damage', 'kills': 'models_killed', 'overkill': 'overkill', 'attacks': 'attacks_made',
            'hits': 'hits', 'wounds': 'wounds', 'damaging': 'failed_saves'}


def _attached_result(part, damage_per, bodyguard_kills, leader_kills):
    return SimulationResult(
        average_damage=round(part['damage'], 2),
        models_killed=round(bodyguard_kills + leader_kills, 2),
        overkill=round(part['overkill'], 2),
        attacks_made=round(part['attacks'], 2),
        hits=round(part['hits'], 2),
        wounds=round(part['wounds'], 2),
        failed_saves=round(part['damaging'], 2),
        damage_dealt=round(part['damage'], 2),
        method="analytical",
        auto_wounds=round(part['lethal'], 2),
        mortal_wounds=round(part['mortal'], 2),
        unsaved=round(part['unsaved'], 2),
        damage_per_attack=round(damage_per, 2),
        bodyguards_killed=round(bodyguard_kills, 2),
        leader_killed=round(leader_kills, 2),
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


def _roll_attack_count(attacker: AttackerProfile, defender: DefenderProfile, r: ResolvedAttack) -> int:
    """Attacks for every model, with per-model Rapid Fire / Blast / Cleave bonuses."""
    num_attacks = 0
    for _ in range(attacker.models):
        num_attacks += max(1, roll_dice(attacker.attacks) + round(r.attacks_bonus_flat))
        for key in r.extra_attacks:
            value = keyword_roll(attacker.abilities, key)
            num_attacks += value * (defender.model_count // 5) if key in ("Blast", "Cleave") else value
    return num_attacks


def _roll_attack(attacker: AttackerProfile, defender: DefenderProfile, conditions: BattleConditions,
                 r: ResolvedAttack, counts: dict) -> list[int]:
    """
    Roll one attack through hit, wound, save and damage (Sustained Hits may add hits). Adds to
    `counts` and returns the damage of each unsaved wound or mortal wound after Feel No Pain.
    """
    ab = attacker.abilities
    hits, lethal = 0, 0
    if r.auto_hit:
        hits = 1
    else:
        ok, crit = _roll_d6(r.hit_need, r.crit_hit_on, r.hit_reroll)
        if ok:
            if crit and r.lethal:
                lethal = 1  # Lethal hits auto-wound (and aren't Critical Wounds)
            else:
                hits = 1
            if crit and r.sustained:
                hits += keyword_roll(ab, "Sustained Hits")

    wounds, mortal = lethal, 0
    for _ in range(hits):
        ok, crit = _roll_d6(r.wound_need, r.crit_wound_on, r.wound_reroll)
        if not ok:
            continue
        if crit and r.devastating:
            mortal += 1
        else:
            wounds += 1
    failed = sum(1 for _ in range(wounds) if random.randint(1, 6) < r.save_need)

    # Damage per unsaved wound / mortal wound, then Feel No Pain per point of damage
    melta_active = not attacker.is_melee and conditions.half_range
    flat_bonus = r.damage_bonus - (keyword_avg(ab, "Melta") if melta_active else 0)
    damage = []
    for _ in range(failed + mortal):
        dmg = roll_dice(attacker.damage) + flat_bonus
        if melta_active:
            dmg += keyword_roll(ab, "Melta")
        dmg = min(max(1, int(round(dmg))), defender.wounds)  # Damage beyond a model's wounds is lost
        if defender.feel_no_pain:
            dmg = sum(1 for _ in range(dmg) if random.randint(1, 6) < defender.feel_no_pain)
        if dmg > 0:
            damage.append(dmg)

    counts['hits'] += hits + lethal
    counts['wounds'] += wounds + mortal
    counts['failed_saves'] += failed + mortal
    return damage


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
    counts = {'attacks': _roll_attack_count(attacker, defender, r), 'hits': 0, 'wounds': 0, 'failed_saves': 0}
    damage_instances = []
    for _ in range(counts['attacks']):
        damage_instances.extend(_roll_attack(attacker, defender, conditions, r, counts))
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


def simulate_monte_carlo_attached(
    attackers: list[AttackerProfile],
    bodyguard: DefenderProfile,
    leader: DefenderProfile,
    conditions: BattleConditions,
    iterations: int = 1000,
    bodyguard_targets: Optional[list[DefenderProfile]] = None,
    leader_targets: Optional[list[DefenderProfile]] = None,
) -> SimulationResult:
    """
    Several weapons attacking a leader with its bodyguard unit, rolled attack by attack: each
    attack is resolved against a bodyguard model while any is left, then against the leader,
    and its damage is applied at once (so the profile changes the moment the last bodyguard
    falls). models_killed counts both; bodyguards_killed, leader_killed and
    leader_kill_probability give them separately.
    """
    bodyguard_targets = bodyguard_targets or [bodyguard] * len(attackers)
    leader_targets = leader_targets or [leader] * len(attackers)
    resolved = [(resolve(a, b, conditions), resolve(a, l, conditions))
                for a, b, l in zip(attackers, bodyguard_targets, leader_targets)]
    totals = {'attacks': 0, 'hits': 0, 'wounds': 0, 'failed_saves': 0}
    damage_results, bodyguard_results, leader_results = [], [], []

    for _ in range(iterations):
        alive = {'bodyguard': bodyguard.model_count, 'leader': leader.model_count}
        remaining = {'bodyguard': bodyguard.wounds, 'leader': leader.wounds}
        dealt = 0
        for attacker, on_bodyguard, on_leader, (r_bodyguard, r_leader) in zip(
                attackers, bodyguard_targets, leader_targets, resolved):
            totals['attacks'] += (num_attacks := _roll_attack_count(attacker, on_bodyguard, r_bodyguard))
            for _ in range(num_attacks):
                stage = 'bodyguard' if alive['bodyguard'] > 0 else 'leader'
                target, r = (on_bodyguard, r_bodyguard) if stage == 'bodyguard' else (on_leader, r_leader)
                for dmg in _roll_attack(attacker, target, conditions, r, totals):
                    dealt += dmg
                    if alive[stage] <= 0:
                        continue  # Everything is dead: damage is still counted, nothing more dies
                    remaining[stage] -= dmg
                    if remaining[stage] <= 0:
                        alive[stage] -= 1
                        remaining[stage] = target.wounds
                        if stage == 'bodyguard' and alive['bodyguard'] == 0:
                            stage, target, r = 'leader', on_leader, r_leader  # Later wounds of this attack too
        damage_results.append(dealt)
        bodyguard_results.append(bodyguard.model_count - alive['bodyguard'])
        leader_results.append(leader.model_count - alive['leader'])

    damage_array = np.array(damage_results)
    bodyguard_array, leader_array = np.array(bodyguard_results), np.array(leader_results)
    kills_array = bodyguard_array + leader_array
    avg_damage = float(np.mean(damage_array))
    max_damage = int(np.max(damage_array)) + 1
    bins = list(range(min(max_damage + 1, 20)))
    hist, _ = np.histogram(damage_array, bins=bins + [bins[-1] + 100000])
    total_models = bodyguard.model_count + leader.model_count
    total_wounds = bodyguard.model_count * bodyguard.wounds + leader.model_count * leader.wounds
    return SimulationResult(
        average_damage=round(avg_damage, 2),
        models_killed=round(float(np.mean(kills_array)), 2),
        overkill=round(max(0, avg_damage - total_wounds), 2),
        attacks_made=round(totals['attacks'] / iterations, 2),
        hits=round(totals['hits'] / iterations, 2),
        wounds=round(totals['wounds'] / iterations, 2),
        failed_saves=round(totals['failed_saves'] / iterations, 2),
        damage_dealt=round(avg_damage, 2),
        damage_distribution={"labels": [str(b) for b in bins[:-1]] + [f"{bins[-1]}+"],
                             "probabilities": (hist / iterations * 100).tolist()},
        method="monte_carlo",
        iterations=iterations,
        kill_probability=round(float(np.mean(kills_array >= 1)) * 100, 1),
        wipe_probability=round(float(np.mean(kills_array >= total_models)) * 100, 1),
        bodyguards_killed=round(float(np.mean(bodyguard_array)), 2),
        leader_killed=round(float(np.mean(leader_array)), 2),
        leader_kill_probability=round(float(np.mean(leader_array >= leader.model_count)) * 100, 1),
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
