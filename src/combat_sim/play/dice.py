"""Game dice: every roll is kept, so the game log can show and replay it."""

import random
from dataclasses import dataclass, field

from ..simulation import parse_dice_expression


@dataclass
class Die:
    face: int
    success: bool
    critical: bool
    rerolled_from: int | None = None  # The first face, when this die was re-rolled


@dataclass
class RollGroup:
    """Dice rolled together for one purpose ('hit', 'wound', 'save', ...)."""
    purpose: str
    need: int
    crit_on: int = 6
    dice: list[Die] = field(default_factory=list)

    @property
    def successes(self):
        return sum(d.success for d in self.dice)

    @property
    def criticals(self):
        return sum(d.critical for d in self.dice)


@dataclass
class ValueRoll:
    """A rolled value such as 'D3+1' attacks or a '2D6' charge: its dice, modifier and total."""
    purpose: str
    size: int
    dice: list[Die]
    modifier: int
    total: int


class Dice:
    """Rolls dice from `rng` (a random.Random by default) and keeps each group in `log`."""

    def __init__(self, rng=None):
        self.rng = rng or random.Random()
        self.log = []

    def roll(self, count, purpose, need, crit_on=6, reroll='none'):
        """
        Roll `count` D6 needing `need`+; a critical (`crit_on`+) always succeeds. Then, as at the
        table, re-roll the failures `reroll` allows ('ones' or 'all') once each, keeping the first face.
        """
        group = RollGroup(purpose, need, crit_on)
        group.dice = [self._die(self.rng.randint(1, 6), need, crit_on) for _ in range(count)]
        for i, die in enumerate(group.dice):
            if not die.success and (reroll == 'all' or (reroll == 'ones' and die.face == 1)):
                group.dice[i] = self._die(self.rng.randint(1, 6), need, crit_on, rerolled_from=die.face)
        self.log.append(group)
        return group

    def roll_value(self, expr, purpose):
        """Roll a dice expression ('D6', '2D3', 'D3+1'; at least 1). A fixed value ('3') rolls nothing."""
        count, size, modifier = parse_dice_expression(expr)
        if count == 0:
            return modifier
        dice = [Die(self.rng.randint(1, size), True, False) for _ in range(count)]
        total = max(1, sum(d.face for d in dice) + modifier)
        self.log.append(ValueRoll(purpose, size, dice, modifier, total))
        return total

    @staticmethod
    def _die(face, need, crit_on, rerolled_from=None):
        critical = face >= crit_on
        return Die(face, critical or face >= need, critical, rerolled_from)
