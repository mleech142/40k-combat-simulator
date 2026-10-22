"""
Missions: the 11th edition Chapter Approved (2026-27) force dispositions and primary missions, as data
and scoring logic in the app's own words (names, timings, conditions and VP; no card text).
Kept in this one file so it can be updated each season or removed.

A player's primary mission comes from their force disposition against their opponent's (MATRIX).
Each primary is a list of lines; a line scores VP at its timing:
- 'turn': the end of your turn;
- 'any_turn': the end of every turn (yours and your opponent's);
- 'command': the end of your Command phase, or, if `round5_end`, the end of your turn in round 5;
- 'battle': once, at the end of the battle.
`rounds` limits a line to those battle rounds. Lines sharing an `either` group are alternatives: only
the best of them scores. A line with `needs` 'actions' waits for actions and operation markers (Play
mode's later step); it never scores yet.

Scoring reads a Context (built by game.py from the game state): what each player controls now and
did at the start of the turn, and the units destroyed this turn and the turn before. VP are capped
(game.py): 15 per battle round and 45 per game for primaries; end-of-battle VP don't count towards the
15 but do towards the 45.
"""

from dataclasses import dataclass, field

DISPOSITIONS = {'take-and-hold': 'Take and Hold', 'purge-the-foe': 'Purge the Foe', 'disruption': 'Disruption',
                'reconnaissance': 'Reconnaissance', 'priority-assets': 'Priority Assets'}
BY_NAME = {name: key for key, name in DISPOSITIONS.items()}

PRIMARY_ROUND_CAP = 15
PRIMARY_GAME_CAP = 45

# Your disposition -> your opponent's -> your primary mission
MATRIX = {
    'take-and-hold': {'take-and-hold': 'battlefield-dominance', 'purge-the-foe': 'immovable-object',
                      'disruption': 'determined-acquisition', 'reconnaissance': 'purge-and-secure',
                      'priority-assets': 'inescapable-dominion'},
    'purge-the-foe': {'take-and-hold': 'unstoppable-force', 'purge-the-foe': 'meatgrinder',
                      'disruption': 'punishment', 'reconnaissance': 'consecrate', 'priority-assets': 'destroyers-wrath'},
    'disruption': {'take-and-hold': 'death-trap', 'purge-the-foe': 'delaying-action', 'disruption': 'outmanoeuvre',
                   'reconnaissance': 'smoke-and-mirrors', 'priority-assets': 'locate-and-deny'},
    'reconnaissance': {'take-and-hold': 'reconnaissance-sweep', 'purge-the-foe': 'triangulation',
                       'disruption': 'surveil-the-foe', 'reconnaissance': 'gather-intel',
                       'priority-assets': 'search-and-scour'},
    'priority-assets': {'take-and-hold': 'secure-asset', 'purge-the-foe': 'vital-link', 'disruption': 'extract-relic',
                        'reconnaissance': 'vanguard-operation', 'priority-assets': 'sabotage'},
}


def primary_for(mine, theirs):
    """Your primary mission's key from the two force dispositions (keys or BSData names), or None."""
    mine, theirs = BY_NAME.get(mine, mine), BY_NAME.get(theirs, theirs)
    return MATRIX.get(mine, {}).get(theirs)


# --- What scoring reads ---------------------------------------------------------------------------------------

@dataclass
class Context:
    """
    The facts a primary's lines are scored on, for `player`. `held`: the objectives (dicts with 'id', 'kind',
    'owner') each player controls now; `held_at_start`: each player's at the start of this turn;
    `in_their_territory`: ids of objectives within `player`'s opponent's territory; `destroyed` /
    `destroyed_before`: units destroyed this turn / the previous turn, each {'player' (whose unit), 'by'
    (the destroying unit's id or None), 'by_player', 'by_in_range' (that unit within range of an
    objective), 'in_range', 'central', 'terrain' (where it stood at the start of the turn)};
    `quarters`: table quarters holding a unit of `player`'s wholly within them and not within 6" of the
    centre; `enemy_wholly_in_my_territory`: an enemy unit wholly within `player`'s territory;
    `condemned`: enemy units `player` condemned (Punishment).
    """
    player: int
    held: list
    held_at_start: list
    in_their_territory: set = field(default_factory=set)
    destroyed: list = field(default_factory=list)
    destroyed_before: list = field(default_factory=list)
    quarters: int = 0
    enemy_wholly_in_my_territory: bool = False
    condemned: list = field(default_factory=list)

    @property
    def enemy(self):
        return 1 - self.player

    def mine(self, exclude_home=False, kind=None):
        """The objectives `player` controls: without their home objective (`exclude_home` True), or without
        either home objective ('both'), or only of one kind."""
        return [o for o in self.held[self.player]
                if not (o['kind'] == 'home' and (exclude_home == 'both' or (exclude_home and o.get('owner') == self.player)))
                and (kind is None or o['kind'] == kind)]

    def kills(self):
        """Enemy units destroyed this turn."""
        return [d for d in self.destroyed if d['player'] == self.enemy]


def _gained(ctx, exclude_home=True):
    before = {o['id'] for o in ctx.held_at_start[ctx.player]}
    return [o for o in ctx.mine(exclude_home) if o['id'] not in before]


def _home_held(ctx):
    return any(o['kind'] == 'home' and o.get('owner') == ctx.player for o in ctx.held[ctx.player])


# Each scorer: (ctx, **params) -> (VP, how it was worked out)
SCORERS = {
    'more_objectives': lambda ctx, vp: (vp, 'more objectives than the opponent') if len(ctx.held[ctx.player]) > len(ctx.held[ctx.enemy]) else (0, ''),
    'per_objective': lambda ctx, vp, exclude_home=False: _each(len(ctx.mine(exclude_home)), vp, 'objective'),
    'per_objective_with_home': lambda ctx, vp: _each(len(ctx.mine(True)), vp, 'other objective') if _home_held(ctx) else (0, ''),
    'per_objective_their_territory': lambda ctx, vp: _each(sum(o['id'] in ctx.in_their_territory for o in ctx.mine()), vp,
                                                           'objective in their territory'),
    'per_gained': lambda ctx, vp: _each(len(_gained(ctx)), vp, 'objective taken this turn'),
    'gained_any': lambda ctx, vp: (vp, 'took an objective this turn') if _gained(ctx) else (0, ''),
    'hold_any': lambda ctx, vp, exclude_home=True: (vp, 'holds an objective') if ctx.mine(exclude_home) else (0, ''),
    'hold_kind': lambda ctx, vp, kind: (vp, f'holds a {kind} objective') if ctx.mine(kind=kind) else (0, ''),
    'hold_at_least': lambda ctx, vp, count: (vp, f'holds {count}+ objectives') if len(ctx.held[ctx.player]) >= count else (0, ''),
    'hold_their_home': lambda ctx, vp: (vp, "holds the opponent's home objective")
    if any(o['kind'] == 'home' and o.get('owner') == ctx.enemy for o in ctx.held[ctx.player]) else (0, ''),
    'hold_central_and_expansion': lambda ctx, vp: (vp, 'holds central and expansion objectives')
    if ctx.mine(kind='central') and ctx.mine(kind='expansion') else (0, ''),
    'killed_any': lambda ctx, vp: (vp, 'destroyed an enemy unit') if ctx.kills() else (0, ''),
    'per_kill': lambda ctx, vp: _each(len(ctx.kills()), vp, 'enemy unit destroyed'),
    'killed_by_unit_on_objective': lambda ctx, vp: (vp, 'a unit on an objective destroyed an enemy unit')
    if any(d['by_in_range'] and d['by_player'] == ctx.player for d in ctx.kills()) else (0, ''),
    'killed_started': lambda ctx, vp, where: (vp, f'destroyed an enemy unit that started {WHERE[where]}')
    if any(d[where] for d in ctx.kills()) else (0, ''),
    'kills_beat_losses': lambda ctx, vp: (vp, 'more kills this turn than losses last turn')
    if len(ctx.kills()) > sum(d['player'] == ctx.player for d in ctx.destroyed_before) else (0, ''),
    'quarters': lambda ctx, vp, count: (vp, f'units in {count} table quarters') if ctx.quarters >= count else (0, ''),
    'territory_clear': lambda ctx, vp: (vp, 'no enemy unit wholly in your territory') if not ctx.enemy_wholly_in_my_territory else (0, ''),
    'condemned_left': lambda ctx, vp: (vp, 'a condemned unit was destroyed')
    if any(d['unit'] in ctx.condemned for d in ctx.destroyed) else (0, ''),
}
WHERE = {'in_range': 'within range of an objective', 'central': 'within range of a central objective',
         'terrain': 'within a terrain area'}


def _each(count, vp, what):
    return count * vp, f'{count} {what}{"s" if count != 1 else ""} × {vp}VP' if count else ''


def line(text, when, score=None, vp=0, rounds=(1, 5), round5_end=False, either=None, needs=None, **params):
    return {'text': text, 'when': when, 'score': score, 'vp': vp, 'rounds': rounds, 'round5_end': round5_end,
            'either': either, 'needs': needs, 'params': params}


def _command(text, score, vp, **params):
    """A line scored at the end of your Command phase from round 2 (the end of your turn in round 5)."""
    return line(text, 'command', score, vp, rounds=(2, 5), round5_end=True, **params)


ACTIONS = 'actions'
HOLD_ANY_4 = _command('Hold an objective other than your home objective', 'hold_any', 4)

PRIMARIES = {
    # Take and Hold
    'battlefield-dominance': ('Battlefield Dominance', 'take-and-hold', [
        line('Rounds 1-2: hold more objectives than your opponent', 'turn', 'more_objectives', 2, rounds=(1, 2)),
        _command('Each objective you hold', 'per_objective', 3),
        _command('Each other objective you hold, while you hold your home objective', 'per_objective_with_home', 2),
    ]),
    'immovable-object': ('Immovable Object', 'take-and-hold', [
        line('Hold a central objective', 'turn', 'hold_kind', 3, kind='central'),
        line('Rounds 2-4: each objective you hold, other than your home objective', 'command', 'per_objective', 5,
             rounds=(2, 4), exclude_home=True),
        line('Round 5: each objective you hold, other than your home objective', 'turn', 'per_objective', 5,
             rounds=(5, 5), exclude_home=True),
    ]),
    'determined-acquisition': ('Determined Acquisition', 'take-and-hold', [
        line('Each objective you took this turn (not your home objective)', 'turn', 'per_gained', 2),
        _command('Each objective you hold', 'per_objective', 3),
        _command('Each of those in your opponent\'s territory', 'per_objective_their_territory', 3),
    ]),
    'purge-and-secure': ('Purge and Secure', 'take-and-hold', [
        line('A unit within range of an objective destroyed an enemy unit', 'turn', 'killed_by_unit_on_objective', 3,
             either='kill'),
        line('You destroyed an enemy unit that started the turn within range of an objective', 'turn', 'killed_started', 3,
             either='kill', where='in_range'),
        _command('Each objective you hold, other than your home objective', 'per_objective', 4, exclude_home=True),
        line('You took an objective this turn (not your home objective)', 'turn', 'gained_any', 3, rounds=(2, 5)),
    ]),
    'inescapable-dominion': ('Inescapable Dominion', 'take-and-hold', [
        line('Hold three or more objectives', 'turn', 'hold_at_least', 4, count=3),
        _command('Hold two or more objectives', 'hold_at_least', 5, count=2),
        _command('Hold more objectives than your opponent', 'more_objectives', 4),
        line("End of the battle: hold your opponent's home objective", 'battle', 'hold_their_home', 5),
    ]),
    # Purge the Foe
    'unstoppable-force': ('Unstoppable Force', 'purge-the-foe', [
        line('Destroy an enemy unit this turn', 'turn', 'killed_any', 3),
        _command('Each objective you hold, other than your home objective', 'per_objective', 4, exclude_home=True),
        line('You took an objective this turn (not your home objective)', 'turn', 'gained_any', 3, rounds=(2, 5)),
        line('End of the battle: hold a central objective', 'battle', 'hold_kind', 5, kind='central'),
    ]),
    'meatgrinder': ('Meatgrinder', 'purge-the-foe', [
        line('Destroy an enemy unit this turn', 'turn', 'killed_any', 3),
        HOLD_ANY_4,
        line('More enemy units destroyed this turn than of yours the turn before', 'turn', 'kills_beat_losses', 5,
             rounds=(2, 5)),
        line("End of the battle: hold your opponent's home objective", 'battle', 'hold_their_home', 5),
    ]),
    'punishment': ('Punishment', 'purge-the-foe', [
        line('A condemned enemy unit was destroyed this turn (either player\'s turn)', 'any_turn', 'condemned_left', 5),
        HOLD_ANY_4,
        _command('Hold more objectives than your opponent', 'more_objectives', 5),
        line("End of the battle: hold your opponent's home objective", 'battle', 'hold_their_home', 8),
    ]),
    'consecrate': ('Consecrate', 'purge-the-foe', [
        line('One or two objectives consecrated', 'turn', vp=3, either='consecrated', needs=ACTIONS),
        line('Three or more objectives consecrated', 'turn', vp=6, either='consecrated', needs=ACTIONS),
        HOLD_ANY_4,
        _command('Hold more objectives than your opponent', 'more_objectives', 4),
        line("End of the battle: your opponent's home objective is consecrated", 'battle', vp=5, needs=ACTIONS),
    ]),
    'destroyers-wrath': ("Destroyer's Wrath", 'purge-the-foe', [
        line('Destroy an enemy unit this turn', 'turn', 'killed_any', 3),
        HOLD_ANY_4,
        _command('Hold more objectives than your opponent', 'more_objectives', 6),
        line('More enemy units destroyed this turn than of yours the turn before', 'turn', 'kills_beat_losses', 4,
             rounds=(2, 5)),
    ]),
    # Disruption
    'death-trap': ('Death Trap', 'disruption', [
        line('Each terrain area you trapped this turn (+3VP more if it is an objective)', 'turn', vp=2, needs=ACTIONS),
        line('An enemy unit that started the turn in a trapped terrain area was destroyed', 'turn', vp=3, needs=ACTIONS),
        HOLD_ANY_4,
    ]),
    'delaying-action': ('Delaying Action', 'disruption', [
        line('Each enemy unit destroyed this turn', 'turn', 'per_kill', 2),
        _command('Hold an objective other than either home objective', 'hold_any', 4, exclude_home='both'),
        line('Hold a central and an expansion objective', 'turn', 'hold_central_and_expansion', 3, rounds=(2, 5)),
    ]),
    'outmanoeuvre': ('Outmanoeuvre', 'disruption', [
        line("Hold your opponent's home objective", 'turn', 'hold_their_home', 10),
        line('Round 1: each objective you hold, other than your home objective', 'turn', 'per_objective', 4,
             rounds=(1, 1), exclude_home=True),
        line('Rounds 2-3: each objective you hold, other than your home objective', 'command', 'per_objective', 5,
             rounds=(2, 3), exclude_home=True),
        line('Rounds 4-5: each objective you hold, other than your home objective', 'turn', 'per_objective', 6,
             rounds=(4, 5), exclude_home=True),
    ]),
    'smoke-and-mirrors': ('Smoke and Mirrors', 'disruption', [
        line('Each decoyed objective (+2VP more in your opponent\'s territory)', 'turn', vp=2, needs=ACTIONS),
        HOLD_ANY_4,
        line('End of the battle: four or more objectives decoyed', 'battle', vp=10, needs=ACTIONS),
    ]),
    'locate-and-deny': ('Locate and Deny', 'disruption', [
        line('You destroyed an enemy unit that started the turn within range of an objective', 'turn', 'killed_started', 4,
             where='in_range'),
        line('Only one of your markers is left, with your unit and no enemy in its terrain area', 'turn', vp=4, needs=ACTIONS),
        HOLD_ANY_4,
        line('End of the battle: only one of your markers is left, held as above', 'battle', vp=5, needs=ACTIONS),
    ]),
    # Reconnaissance
    'reconnaissance-sweep': ('Reconnaissance Sweep', 'reconnaissance', [
        line('Units wholly within three table quarters, none within 6" of the centre', 'turn', 'quarters', 3,
             either='quarters', count=3),
        line('Units wholly within all four table quarters, none within 6" of the centre', 'turn', 'quarters', 6,
             either='quarters', count=4),
        line('Each enemy unit destroyed this turn', 'turn', 'per_kill', 1),
        _command('Hold an objective other than your home objective', 'hold_any', 3),
    ]),
    'triangulation': ('Triangulation', 'reconnaissance', [
        HOLD_ANY_4,
        line('Objectives triangulated: one 3VP, two 6VP, three or more 10VP', 'turn', vp=3, rounds=(2, 5), needs=ACTIONS),
        line('End of the battle: hold four or more objectives', 'battle', 'hold_at_least', 10, count=4),
    ]),
    'surveil-the-foe': ('Surveil the Foe', 'reconnaissance', [
        line('You surveilled an enemy unit away from marked objectives', 'turn', vp=4, needs=ACTIONS),
        HOLD_ANY_4,
        _command('Hold more objectives than your opponent', 'more_objectives', 4),
        line("None of your opponent's operation markers are on the battlefield", 'turn', vp=5, rounds=(2, 5), needs=ACTIONS),
    ]),
    'gather-intel': ('Gather Intel', 'reconnaissance', [
        line('Round 1: hold a central objective', 'turn', 'hold_kind', 6, rounds=(1, 1), kind='central'),
        HOLD_ANY_4,
        line('Each unit that extracted intelligence this turn', 'turn', vp=7, rounds=(2, 5), needs=ACTIONS),
        line('End of the battle: three or more of your operation markers on the battlefield', 'battle', vp=5, needs=ACTIONS),
        line("End of the battle: one of your markers by your opponent's home objective", 'battle', vp=5, needs=ACTIONS),
    ]),
    'search-and-scour': ('Search and Scour', 'reconnaissance', [
        line('Hold a central objective', 'turn', 'hold_kind', 3, kind='central'),
        line('You destroyed an enemy unit that started the turn within a terrain area', 'turn', 'killed_started', 2,
             where='terrain'),
        _command('Each objective you hold, other than your home objective', 'per_objective', 4, exclude_home=True),
        line('End of the battle: no enemy unit wholly within your territory', 'battle', 'territory_clear', 5),
    ]),
    # Priority Assets
    'secure-asset': ('Secure Asset', 'priority-assets', [
        line('A unit of yours secured the asset this turn', 'turn', vp=4, needs=ACTIONS),
        line('You destroyed an enemy unit that started the turn within range of a central objective', 'turn',
             'killed_started', 2, where='central'),
        HOLD_ANY_4,
        _command('Hold three or more objectives', 'hold_at_least', 4, count=3),
    ]),
    'vital-link': ('Vital Link', 'priority-assets', [
        line('Hold a central objective', 'turn', 'hold_kind', 2, kind='central'),
        line('Each of your operation markers on the central objectives you hold', 'turn', vp=1, needs=ACTIONS),
        HOLD_ANY_4,
        _command('One of those is a central objective', 'hold_kind', 4, kind='central'),
        line("End of the battle: hold your opponent's home objective", 'battle', 'hold_their_home', 10),
    ]),
    'extract-relic': ('Extract Relic', 'priority-assets', [
        line('A unit of yours made a sensor sweep this turn', 'turn', vp=4, needs=ACTIONS),
        line('You destroyed an enemy unit that started the turn within range of an objective', 'turn', 'killed_started', 3,
             where='in_range'),
        line("Only one of your opponent's markers is left, with your unit and no enemy in its area", 'turn', vp=4,
             needs=ACTIONS),
        HOLD_ANY_4,
        line('End of the battle: only one of their markers is left, held as above', 'battle', vp=5, needs=ACTIONS),
    ]),
    'vanguard-operation': ('Vanguard Operation', 'priority-assets', [
        line('A unit of yours made a vanguard operation this turn', 'turn', vp=4, needs=ACTIONS),
        line('Destroy an enemy unit this turn', 'turn', 'killed_any', 2),
        HOLD_ANY_4,
        line("End of the battle: hold your opponent's home objective", 'battle', 'hold_their_home', 10),
    ]),
    'sabotage': ('Sabotage', 'priority-assets', [
        line('Each unit that committed sabotage this turn (+2VP more in your opponent\'s territory)', 'turn', vp=3,
             needs=ACTIONS),
        HOLD_ANY_4,
    ]),
}


def primary(key):
    """{'key', 'name', 'disposition', 'lines': [line with 'id']} for a primary mission's key."""
    name, disposition, lines = PRIMARIES[key]
    return {'key': key, 'name': name, 'disposition': disposition,
            'lines': [dict(entry, id=f'{key}:{i}') for i, entry in enumerate(lines)]}


def timing(entry):
    """When a line scores, for people: 'End of your turn · rounds 2-5' and so on."""
    when = {'turn': 'End of your turn', 'any_turn': 'End of every turn', 'battle': 'End of the battle',
            'command': 'End of your Command phase'}[entry['when']]
    if entry['when'] == 'command' and entry['round5_end']:
        when += ' (round 5: end of your turn)'
    first, last = entry['rounds']
    if entry['when'] == 'battle' or (first, last) == (1, 5):
        return when
    return f'{when} · round {first}' if first == last else f'{when} · rounds {first}-{last}'


def due(entry, when, round_number):
    """Whether a line scores at this timing ('turn', 'any_turn', 'command' or 'battle') in this round."""
    first, last = entry['rounds']
    if when == 'battle':
        return entry['when'] == 'battle'
    if entry['when'] == 'battle' or not first <= round_number <= last:
        return False
    if entry['when'] == 'command':
        if entry['round5_end'] and round_number == 5:
            return when == 'turn'
        return when == 'command'
    return entry['when'] == when


def score_lines(key, when, round_number, ctx):
    """[(line, VP, how)] for the lines of a primary mission due now, each 'either' group's best only."""
    found, best = [], {}
    for entry in primary(key)['lines']:
        if entry['needs'] or not entry['score'] or not due(entry, when, round_number):
            continue
        vp, how = SCORERS[entry['score']](ctx, entry['vp'], **entry['params'])
        if not vp:
            continue
        if entry['either']:
            if entry['either'] in best and best[entry['either']][1] >= vp:
                continue
            best[entry['either']] = (entry, vp, how)
            continue
        found.append((entry, vp, how))
    return found + list(best.values())


def preview(key, ctx):
    """{line id: (VP, how)} for every line that would score right now, whatever its timing (Mission panel)."""
    out = {}
    for entry in primary(key)['lines']:
        if entry['needs'] or not entry['score']:
            continue
        out[entry['id']] = SCORERS[entry['score']](ctx, entry['vp'], **entry['params'])
    return out
