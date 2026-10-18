"""
Core simulation engine for 40K Combat Simulator.

Supports both analytical (expected value) and Monte Carlo simulation methods.
"""

import random
import math
from dataclasses import dataclass
from typing import Optional
import numpy as np


@dataclass
class AttackerProfile:
    """Configuration for the attacking unit."""
    attacks: str  # "3", "D6", "2D6", "D3+1", etc.
    skill: int  # BS or WS (3+ = 3)
    strength: int
    ap: int  # Positive number (AP-2 = 2)
    damage: str  # "1", "D6", "D3+3", etc.
    abilities: list[str]  # ["Lethal Hits", "Sustained Hits 1", etc.]


@dataclass
class DefenderProfile:
    """Configuration for the defending unit."""
    toughness: int
    save: int  # 3+ = 3
    wounds: int
    invulnerable_save: Optional[int] = None
    feel_no_pain: Optional[int] = None
    keywords: list[str] = None
    model_count: int = 1

    def __post_init__(self):
        if self.keywords is None:
            self.keywords = []


@dataclass
class BattleConditions:
    """Modifiers active during the combat."""
    half_range: bool = False
    charged: bool = False
    stationary: bool = False
    cover: bool = False
    reroll_hits: str = "none"  # "none", "ones", "all"
    reroll_wounds: str = "none"  # "none", "ones", "all"


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


def parse_dice_expression(expr: str) -> tuple[int, int, int]:
    """
    Parse a dice expression like "D6", "2D3", "D6+2", "3".

    Returns (num_dice, die_size, modifier).
    "D6" -> (1, 6, 0)
    "2D3" -> (2, 3, 0)
    "D6+2" -> (1, 6, 2)
    "3" -> (0, 0, 3)  # flat value
    """
    expr = expr.upper().strip()

    if 'D' not in expr:
        return (0, 0, int(expr))

    modifier = 0
    if '+' in expr:
        expr, mod_str = expr.split('+')
        modifier = int(mod_str)
    elif '-' in expr:
        parts = expr.split('-')
        expr = parts[0]
        modifier = -int(parts[1])

    if expr.startswith('D'):
        num_dice = 1
        die_size = int(expr[1:])
    else:
        num_dice = int(expr[0])
        die_size = int(expr[2:])

    return (num_dice, die_size, modifier)


def average_dice(expr: str) -> float:
    """Calculate the average value of a dice expression."""
    num_dice, die_size, modifier = parse_dice_expression(expr)
    if num_dice == 0:
        return float(modifier)
    return num_dice * (die_size + 1) / 2 + modifier


def roll_dice(expr: str) -> int:
    """Roll dice according to expression, return result."""
    num_dice, die_size, modifier = parse_dice_expression(expr)
    if num_dice == 0:
        return modifier
    total = sum(random.randint(1, die_size) for _ in range(num_dice))
    return max(1, total + modifier)  # Minimum 1


def get_wound_roll_needed(strength: int, toughness: int) -> int:
    """
    Calculate the roll needed to wound based on S vs T.

    Returns the minimum roll needed (2-6), or 7 if impossible.
    """
    if strength >= toughness * 2:
        return 2
    elif strength > toughness:
        return 3
    elif strength == toughness:
        return 4
    elif strength * 2 <= toughness:
        return 6
    else:  # strength < toughness
        return 5


def probability_to_succeed(target: int, reroll: str = "none") -> float:
    """
    Calculate probability of succeeding on a D6 roll of target+.

    Accounts for rerolls.
    """
    if target > 6:
        return 0.0
    if target <= 1:
        return 1.0

    base_prob = (7 - target) / 6

    if reroll == "none":
        return base_prob
    elif reroll == "ones":
        # Reroll 1s: P(success) + P(roll 1) * P(success on reroll)
        return base_prob + (1/6) * base_prob
    elif reroll == "all":
        # Reroll all failures: P(success) + P(fail) * P(success on reroll)
        return base_prob + (1 - base_prob) * base_prob
    return base_prob


def simulate_analytical(
    attacker: AttackerProfile,
    defender: DefenderProfile,
    conditions: BattleConditions
) -> SimulationResult:
    """
    Calculate expected damage using analytical probability math.

    This is fast and exact for simple cases.
    """
    # Calculate number of attacks
    attacks = average_dice(attacker.attacks)

    # Apply Blast if present and target has multiple models
    if "Blast" in attacker.abilities and defender.model_count >= 6:
        attacks += defender.model_count // 5  # +1 per 5 models (simplified)

    # Apply Rapid Fire at half range
    for ability in attacker.abilities:
        if ability.startswith("Rapid Fire") and conditions.half_range:
            bonus = int(ability.split()[-1]) if len(ability.split()) > 2 else 1
            attacks += bonus

    # Calculate hit probability
    hit_target = attacker.skill
    if "Heavy" in attacker.abilities and conditions.stationary:
        hit_target -= 1
    hit_target = max(2, min(6, hit_target))  # Clamp to 2-6

    # Torrent auto-hits
    if "Torrent" in attacker.abilities:
        hit_prob = 1.0
    else:
        hit_prob = probability_to_succeed(hit_target, conditions.reroll_hits)

    hits = attacks * hit_prob

    # Add Sustained Hits
    for ability in attacker.abilities:
        if ability.startswith("Sustained Hits"):
            sustained = int(ability.split()[-1]) if len(ability.split()) > 2 else 1
            crit_prob = 1/6  # Natural 6s
            hits += attacks * crit_prob * sustained

    # Calculate wound probability
    wound_target = get_wound_roll_needed(attacker.strength, defender.toughness)

    # Apply Lance on charge
    if "Lance" in attacker.abilities and conditions.charged:
        wound_target -= 1
    wound_target = max(2, min(6, wound_target))

    # Check for Anti-X abilities
    anti_crit_wound = None
    for ability in attacker.abilities:
        if ability.startswith("Anti-"):
            # Parse "Anti-Infantry 4+" or "Anti-Vehicle 3+"
            parts = ability.replace("+", "").split()
            if len(parts) >= 2:
                keyword = parts[0].replace("Anti-", "")
                target_roll = int(parts[-1])
                if keyword.upper() in [k.upper() for k in defender.keywords]:
                    anti_crit_wound = target_roll

    wound_prob = probability_to_succeed(wound_target, conditions.reroll_wounds)

    # Lethal Hits: 6s to hit auto-wound
    lethal_wounds = 0
    if "Lethal Hits" in attacker.abilities:
        crit_prob = 1/6
        lethal_wounds = attacks * hit_prob * crit_prob  # 6s to hit become auto-wounds
        hits -= lethal_wounds  # Remove from normal wound pool

    wounds = hits * wound_prob + lethal_wounds

    # Devastating Wounds: 6s to wound become mortal wounds (skip saves)
    devastating_mortals = 0
    if "Devastating Wounds" in attacker.abilities:
        crit_prob = 1/6
        devastating_mortals = wounds * crit_prob
        wounds -= devastating_mortals  # Remove from save pool

    # Calculate save probability
    save_target = defender.save + attacker.ap
    if conditions.cover and attacker.ap == 0:
        save_target -= 1  # Cover improves save by 1 against AP0

    # Use invulnerable save if better
    if defender.invulnerable_save:
        save_target = min(save_target, defender.invulnerable_save)

    save_target = max(2, save_target)  # Can't save on 1

    if save_target > 6:
        save_prob = 0.0
    else:
        save_prob = (7 - save_target) / 6

    failed_saves = wounds * (1 - save_prob)

    # Calculate damage
    avg_damage_per_failed = average_dice(attacker.damage)

    # Melta bonus at half range
    for ability in attacker.abilities:
        if ability.startswith("Melta") and conditions.half_range:
            melta_bonus = int(ability.split()[-1]) if len(ability.split()) > 1 else 2
            avg_damage_per_failed += melta_bonus

    total_damage = failed_saves * avg_damage_per_failed + devastating_mortals * avg_damage_per_failed

    # Apply Feel No Pain
    if defender.feel_no_pain:
        fnp_prob = (7 - defender.feel_no_pain) / 6
        total_damage *= (1 - fnp_prob)

    # Calculate models killed (with overkill consideration)
    models_killed = min(total_damage / defender.wounds, defender.model_count)
    overkill = max(0, total_damage - (defender.model_count * defender.wounds))

    return SimulationResult(
        average_damage=round(total_damage, 2),
        models_killed=round(models_killed, 2),
        overkill=round(overkill, 2),
        attacks_made=round(attacks, 2),
        hits=round(hits + lethal_wounds, 2),
        wounds=round(wounds + devastating_mortals, 2),
        failed_saves=round(failed_saves, 2),
        damage_dealt=round(total_damage, 2),
        method="analytical"
    )


def simulate_monte_carlo(
    attacker: AttackerProfile,
    defender: DefenderProfile,
    conditions: BattleConditions,
    iterations: int = 1000
) -> SimulationResult:
    """
    Simulate combat using Monte Carlo method (rolling virtual dice).

    More accurate for complex interactions, shows probability distribution.
    """
    damage_results = []

    for _ in range(iterations):
        # Roll attacks
        num_attacks = roll_dice(attacker.attacks)

        # Apply Blast
        if "Blast" in attacker.abilities and defender.model_count >= 6:
            num_attacks += defender.model_count // 5

        # Apply Rapid Fire
        for ability in attacker.abilities:
            if ability.startswith("Rapid Fire") and conditions.half_range:
                bonus = int(ability.split()[-1]) if len(ability.split()) > 2 else 1
                num_attacks += bonus

        # Roll hits
        hit_target = attacker.skill
        if "Heavy" in attacker.abilities and conditions.stationary:
            hit_target -= 1
        hit_target = max(2, min(6, hit_target))

        hits = 0
        lethal_wounds = 0
        sustained_bonus = 0

        for _ in range(num_attacks):
            if "Torrent" in attacker.abilities:
                roll = 6  # Auto-hit
            else:
                roll = random.randint(1, 6)
                # Reroll logic
                if roll < hit_target:
                    if conditions.reroll_hits == "all" or (conditions.reroll_hits == "ones" and roll == 1):
                        roll = random.randint(1, 6)

            if roll >= hit_target:
                if roll == 6:  # Critical hit
                    if "Lethal Hits" in attacker.abilities:
                        lethal_wounds += 1
                    else:
                        hits += 1
                    # Sustained hits on crits
                    for ability in attacker.abilities:
                        if ability.startswith("Sustained Hits"):
                            sustained = int(ability.split()[-1]) if len(ability.split()) > 2 else 1
                            sustained_bonus += sustained
                else:
                    hits += 1

        hits += sustained_bonus

        # Roll wounds
        wound_target = get_wound_roll_needed(attacker.strength, defender.toughness)
        if "Lance" in attacker.abilities and conditions.charged:
            wound_target -= 1
        wound_target = max(2, min(6, wound_target))

        wounds = lethal_wounds  # Lethal hits auto-wound
        devastating_mortals = 0

        for _ in range(hits):
            roll = random.randint(1, 6)
            if roll < wound_target:
                if conditions.reroll_wounds == "all" or (conditions.reroll_wounds == "ones" and roll == 1):
                    roll = random.randint(1, 6)

            if roll >= wound_target:
                if roll == 6 and "Devastating Wounds" in attacker.abilities:
                    devastating_mortals += 1
                else:
                    wounds += 1

        # Roll saves
        save_target = defender.save + attacker.ap
        if conditions.cover and attacker.ap == 0:
            save_target -= 1
        if defender.invulnerable_save:
            save_target = min(save_target, defender.invulnerable_save)
        save_target = max(2, save_target)

        failed_saves = 0
        for _ in range(wounds):
            roll = random.randint(1, 6)
            if save_target > 6 or roll < save_target:
                failed_saves += 1

        # Calculate damage
        total_damage = 0
        damage_per_wound = attacker.damage

        # Melta bonus
        melta_bonus = 0
        for ability in attacker.abilities:
            if ability.startswith("Melta") and conditions.half_range:
                melta_bonus = int(ability.split()[-1]) if len(ability.split()) > 1 else 2

        for _ in range(failed_saves + devastating_mortals):
            dmg = roll_dice(damage_per_wound) + melta_bonus
            total_damage += dmg

        # Apply Feel No Pain
        if defender.feel_no_pain:
            fnp_target = defender.feel_no_pain
            final_damage = 0
            for _ in range(int(total_damage)):
                roll = random.randint(1, 6)
                if roll < fnp_target:
                    final_damage += 1
            total_damage = final_damage

        damage_results.append(total_damage)

    # Analyze results
    damage_array = np.array(damage_results)
    avg_damage = float(np.mean(damage_array))
    models_killed = min(avg_damage / defender.wounds, defender.model_count)

    # Build distribution
    max_damage = int(np.max(damage_array)) + 1
    bins = list(range(min(max_damage + 1, 20)))
    hist, _ = np.histogram(damage_array, bins=bins + [bins[-1] + 100])

    distribution = {
        "labels": [str(b) for b in bins[:-1]] + [f"{bins[-1]}+"],
        "probabilities": (hist / iterations * 100).tolist()
    }

    return SimulationResult(
        average_damage=round(avg_damage, 2),
        models_killed=round(models_killed, 2),
        overkill=round(max(0, avg_damage - defender.model_count * defender.wounds), 2),
        attacks_made=round(average_dice(attacker.attacks), 2),
        hits=0,  # Not tracked per-iteration
        wounds=0,
        failed_saves=0,
        damage_dealt=round(avg_damage, 2),
        damage_distribution=distribution,
        method="monte_carlo",
        iterations=iterations
    )


def simulate(
    attacker: AttackerProfile,
    defender: DefenderProfile,
    conditions: BattleConditions,
    method: str = "analytical",
    iterations: int = 1000
) -> SimulationResult:
    """
    Run a combat simulation.

    Args:
        attacker: The attacking unit/weapon profile
        defender: The defending unit profile
        conditions: Battle modifiers in effect
        method: "analytical" or "monte_carlo"
        iterations: Number of iterations for Monte Carlo

    Returns:
        SimulationResult with damage breakdown and distribution
    """
    if method == "monte_carlo":
        return simulate_monte_carlo(attacker, defender, conditions, iterations)
    else:
        return simulate_analytical(attacker, defender, conditions)
