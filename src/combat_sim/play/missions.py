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
the best of them scores. A line with `needs` set can't score yet (none since actions arrived).

Mission actions (ACTIONS) are started in the Shooting phase by units of a player whose primary offers
them (PRIMARY_ACTIONS); some complete at once, the rest at the end of the turn. Operation markers carry a
`tag` (what they mark: 'trapped', 'decoyed', ...); removing a marker removes its status.

Secondary missions: before the battle each player plays Fixed (two of the cards marked Fixed,
SECONDARIES, active all battle) or Tactical (the 18-card deck, TACTICAL: two drawn at the start of each of
their Command phases, kept until scored or discarded). Their lines are scored like a primary's; a line's
`turn_cap` limits its VP in one turn. Two more timings: 'opp_turn' (the end of your opponent's turn) and
'opp_turn_or_r5' (that, or the end of battle round 5, whichever comes first).

Scoring reads a Context (built by mission_state.py from the game state): what each player controls now
and did at the start of the turn, and the units and models destroyed this turn and the turn before. VP
are capped (mission_state.py): primaries 15 per battle round and 45 per game, end-of-battle VP not
counting towards the 15; secondaries likewise 15 and 45, and each Fixed card at most 20.
"""

from dataclasses import dataclass, field

DISPOSITIONS = {'take-and-hold': 'Take and Hold', 'purge-the-foe': 'Purge the Foe', 'disruption': 'Disruption',
                'reconnaissance': 'Reconnaissance', 'priority-assets': 'Priority Assets'}
BY_NAME = {name: key for key, name in DISPOSITIONS.items()}

PRIMARY_ROUND_CAP = 15
PRIMARY_GAME_CAP = 45
SECONDARY_ROUND_CAP = 15
SECONDARY_GAME_CAP = 45
FIXED_CARD_CAP = 20
FIXED_CARDS = 2  # A player playing Fixed chooses this many
BIG_UNIT = 13  # A Grievous Blow: units with a starting strength of this many models or more
BIG_MODEL_WOUNDS = 10  # Bring It Down: models with this many wounds or more
TOUGH_CHARACTER_WOUNDS = 4  # Assassination: +1VP for a Character model with this many wounds or more

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
    models_destroyed: list = field(default_factory=list)
    presence: int = 0
    markers: list = field(default_factory=list)  # Every operation marker: {'player', 'tag', 'objective', 'terrain'}
    completed: list = field(default_factory=list)  # Actions completed this turn: {'player', 'action', 'unit', ...}
    trapped_now: list = field(default_factory=list)  # Terrain areas `player` trapped this turn: {'terrain', 'objective'}
    killed_in_trapped: bool = False  # An enemy unit that started the turn in an area `player` trapped was destroyed
    lone_mine: bool = False  # Locate and Deny: one of `player`'s markers left, held by them alone in its area
    lone_theirs: bool = False  # Extract Relic: the same for one of the opponent's markers
    surveilled: bool = False  # Surveil the Foe: an enemy unit surveilled this turn away from marked objectives
    markers_on_held_central: int = 0  # Vital Link: `player`'s markers in range of central objectives they hold
    # Tactical cards (units counted leave out Aircraft and battle-shocked units)
    nml: tuple = (0, 0)  # Units wholly within No Man's Land: (`player`'s, the enemy's)
    nml_objectives: set = field(default_factory=set)  # Ids of objectives within No Man's Land
    outflank: int = 0  # 1: a unit within 6" of an edge outside your territory; 2: units by opposite edges, one outside
    beacon: int = 0  # 1: the beacon unit is outside your deployment zone; 2: outside your territory
    enemy_in_my_zone: bool = False
    centre_mine: bool = False  # One of `player`'s units within 3" of the centre of the battlefield
    enemy_from_centre: float = 99  # How close the nearest enemy unit is to the centre
    all_enemy_characters_dead: bool = False
    tempting_held: bool = False
    behind_lines: int = 0  # Units wholly within the opponent's deployment zone
    guarded: int = 0  # Objectives guarded by `player`'s army (Burden of Trust)
    held_expansions_all: bool = False  # `player` holds every expansion objective (there being some)

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

    def my_markers(self, tag=None, player=None):
        """`player`'s operation markers (or another player's), of one tag or any."""
        who = self.player if player is None else player
        return [m for m in self.markers if m['player'] == who and (tag is None or m['tag'] == tag)]

    def done(self, action):
        """Actions of `player`'s completed this turn."""
        return [c for c in self.completed if c['player'] == self.player and c['action'] == action]

    def model_kills(self):
        """Enemy models destroyed this turn: {'model', 'unit', 'player', 'character', 'wounds'}."""
        return [m for m in self.models_destroyed if m['player'] == self.enemy]


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
    # Secondary missions
    'per_big_unit_killed': lambda ctx, vp: _each(sum((d.get('strength') or 0) >= BIG_UNIT for d in ctx.kills()), vp,
                                                 f'unit of {BIG_UNIT}+ models destroyed'),
    'per_character_killed': lambda ctx, vp, tough=False: _each(
        sum(m['character'] and (not tough or (m['wounds'] or 0) >= TOUGH_CHARACTER_WOUNDS) for m in ctx.model_kills()), vp,
        f'Character model{f" with {TOUGH_CHARACTER_WOUNDS}+ wounds" if tough else ""} destroyed'),
    'per_big_model_killed': lambda ctx, vp: _each(sum((m['wounds'] or 0) >= BIG_MODEL_WOUNDS for m in ctx.model_kills()), vp,
                                                  f'model with {BIG_MODEL_WOUNDS}+ wounds destroyed'),
    'presence': lambda ctx, vp, count: (vp, f'a presence in {count} table quarters') if ctx.presence >= count else (0, ''),
    # Actions and operation markers
    'marker_count': lambda ctx, vp, tag, least, most=None: (vp, f'{len(ctx.my_markers(tag))} objectives {tag}')
    if least <= len(ctx.my_markers(tag)) <= (most or 99) else (0, ''),
    'per_marker': lambda ctx, vp, tag: _each(len(ctx.my_markers(tag)), vp, f'objective {tag}'),
    'per_marker_their_territory': lambda ctx, vp, tag: _each(
        sum(m['objective'] in ctx.in_their_territory for m in ctx.my_markers(tag)), vp, f'objective {tag} in their territory'),
    'their_home_marked': lambda ctx, vp, tag: (vp, f"your opponent's home objective is {tag}")
    if any(m.get('objective_kind') == 'home' and m.get('objective_owner') == ctx.enemy for m in ctx.my_markers(tag)) else (0, ''),
    'my_marker_on_their_home': lambda ctx, vp: (vp, "a marker by your opponent's home objective")
    if any(m.get('objective_kind') == 'home' and m.get('objective_owner') == ctx.enemy for m in ctx.my_markers()) else (0, ''),
    'markers_at_least': lambda ctx, vp, count: (vp, f'{count}+ of your markers on the battlefield')
    if len(ctx.my_markers()) >= count else (0, ''),
    'per_trapped': lambda ctx, vp: _each(len(ctx.trapped_now), vp, 'terrain area trapped this turn'),
    'per_trapped_objective': lambda ctx, vp: _each(sum(t['objective'] for t in ctx.trapped_now), vp, 'of those an objective'),
    'killed_in_trapped': lambda ctx, vp: (vp, 'destroyed a unit that started in a trapped area') if ctx.killed_in_trapped else (0, ''),
    'lone_marker': lambda ctx, vp, whose: (vp, 'one marker left, held by you alone')
    if (ctx.lone_mine if whose == 'mine' else ctx.lone_theirs) else (0, ''),
    'surveilled': lambda ctx, vp: (vp, 'surveilled an enemy unit') if ctx.surveilled else (0, ''),
    'no_their_markers': lambda ctx, vp: (vp, 'none of their markers on the battlefield')
    if not ctx.my_markers(player=ctx.enemy) else (0, ''),
    'completed_any': lambda ctx, vp, action: (vp, f'{ACTION_NAMES.get(action, action)} done this turn') if ctx.done(action) else (0, ''),
    'per_completed': lambda ctx, vp, action: _each(len(ctx.done(action)), vp, f'unit that completed {ACTION_NAMES.get(action, action)}'),
    'per_completed_their_territory': lambda ctx, vp, action: _each(sum(bool(c.get('their_territory')) for c in ctx.done(action)), vp,
                                                                   'of those in their territory'),
    'markers_on_held_central': lambda ctx, vp: _each(ctx.markers_on_held_central, vp, 'marker on a central objective you hold'),
    # Tactical secondary missions
    'per_kill_started': lambda ctx, vp, where: _each(sum(d[where] for d in ctx.kills()), vp,
                                                     f'enemy unit destroyed that started {WHERE[where]}'),
    'completed_count': lambda ctx, vp, action, least, most=None: (vp, f'{len(ctx.done(action))} {ACTION_NAMES.get(action, action)}')
    if least <= len(ctx.done(action)) <= (most or 99) else (0, ''),
    'nml_majority': lambda ctx, vp: (vp, f'{ctx.nml[0]} to {ctx.nml[1]} units in No Man\'s Land') if ctx.nml[0] > ctx.nml[1] else (0, ''),
    'outflank': lambda ctx, vp, level: (vp, 'outflanking') if ctx.outflank >= level else (0, ''),
    'beacon': lambda ctx, vp, level: (vp, 'the beacon holds') if ctx.beacon >= level else (0, ''),
    'hold_my_home': lambda ctx, vp: (vp, 'holds your home objective') if _home_held(ctx) else (0, ''),
    'home_and_zone_clear': lambda ctx, vp: (vp, 'no enemy in your deployment zone')
    if _home_held(ctx) and not ctx.enemy_in_my_zone else (0, ''),
    'nml_objectives': lambda ctx, vp, count: (vp, f'holds {count}+ objectives in No Man\'s Land')
    if sum(o['id'] in ctx.nml_objectives for o in ctx.mine(True)) >= count else (0, ''),
    'forward_position': lambda ctx, vp: (vp, 'a forward position') if (
        any(o['kind'] == 'home' and o.get('owner') == ctx.enemy for o in ctx.held[ctx.player])
        or (ctx.held_expansions_all)) else (0, ''),
    'centre_ground': lambda ctx, vp, clear: (vp, f'the centre held, no enemy within {clear}"')
    if ctx.centre_mine and ctx.enemy_from_centre > clear else (0, ''),
    'assassination_tactical': lambda ctx, vp: (vp, 'an enemy Character destroyed')
    if any(m['character'] for m in ctx.model_kills()) or ctx.all_enemy_characters_dead else (0, ''),
    'tempting': lambda ctx, vp: (vp, 'holds the tempting target') if ctx.tempting_held else (0, ''),
    'behind_lines': lambda ctx, vp: _each(ctx.behind_lines, vp, 'unit in their deployment zone'),
    'guarded': lambda ctx, vp: _each(ctx.guarded, vp, 'objective guarded'),
}
WHERE = {'in_range': 'within range of an objective', 'central': 'within range of a central objective',
         'terrain': 'within a terrain area'}


def _each(count, vp, what):
    return count * vp, f'{count} {what}{"s" if count != 1 else ""} × {vp}VP' if count else ''


def line(text, when, score=None, vp=0, rounds=(1, 5), round5_end=False, either=None, needs=None, turn_cap=None, **params):
    return {'text': text, 'when': when, 'score': score, 'vp': vp, 'rounds': rounds, 'round5_end': round5_end,
            'either': either, 'needs': needs, 'turn_cap': turn_cap, 'params': params}


def _command(text, score, vp, **params):
    """A line scored at the end of your Command phase from round 2 (the end of your turn in round 5)."""
    return line(text, 'command', score, vp, rounds=(2, 5), round5_end=True, **params)


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
        line('One or two objectives consecrated', 'turn', 'marker_count', 3, either='consecrated', tag='consecrated', least=1, most=2),
        line('Three or more objectives consecrated', 'turn', 'marker_count', 6, either='consecrated', tag='consecrated', least=3),
        HOLD_ANY_4,
        _command('Hold more objectives than your opponent', 'more_objectives', 4),
        line("End of the battle: your opponent's home objective is consecrated", 'battle', 'their_home_marked', 5, tag='consecrated'),
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
        line('Each terrain area you trapped this turn', 'turn', 'per_trapped', 2),
        line('Each of those that is an objective', 'turn', 'per_trapped_objective', 3),
        line('An enemy unit that started the turn in a trapped terrain area was destroyed', 'turn', 'killed_in_trapped', 3),
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
        line('Each decoyed objective', 'turn', 'per_marker', 2, tag='decoyed'),
        line("Each of those in your opponent's territory", 'turn', 'per_marker_their_territory', 2, tag='decoyed'),
        HOLD_ANY_4,
        line('End of the battle: four or more objectives decoyed', 'battle', 'marker_count', 10, tag='decoyed', least=4),
    ]),
    'locate-and-deny': ('Locate and Deny', 'disruption', [
        line('You destroyed an enemy unit that started the turn within range of an objective', 'turn', 'killed_started', 4,
             where='in_range'),
        line('Only one of your markers is left, with your unit and no enemy in its terrain area', 'turn', 'lone_marker', 4, whose='mine'),
        HOLD_ANY_4,
        line('End of the battle: only one of your markers is left, held as above', 'battle', 'lone_marker', 5, whose='mine'),
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
        line('One objective triangulated', 'turn', 'marker_count', 3, rounds=(2, 5), either='triangulated', tag='triangulated', least=1, most=1),
        line('Two objectives triangulated', 'turn', 'marker_count', 6, rounds=(2, 5), either='triangulated', tag='triangulated', least=2, most=2),
        line('Three or more objectives triangulated', 'turn', 'marker_count', 10, rounds=(2, 5), either='triangulated', tag='triangulated', least=3),
        line('End of the battle: hold four or more objectives', 'battle', 'hold_at_least', 10, count=4),
    ]),
    'surveil-the-foe': ('Surveil the Foe', 'reconnaissance', [
        line('You surveilled an enemy unit away from marked objectives', 'turn', 'surveilled', 4),
        HOLD_ANY_4,
        _command('Hold more objectives than your opponent', 'more_objectives', 4),
        line("None of your opponent's operation markers are on the battlefield", 'turn', 'no_their_markers', 5, rounds=(2, 5)),
    ]),
    'gather-intel': ('Gather Intel', 'reconnaissance', [
        line('Round 1: hold a central objective', 'turn', 'hold_kind', 6, rounds=(1, 1), kind='central'),
        HOLD_ANY_4,
        line('Each unit that extracted intelligence this turn', 'turn', 'per_completed', 7, rounds=(2, 5), action='extract-intelligence'),
        line('End of the battle: three or more of your operation markers on the battlefield', 'battle', 'markers_at_least', 5, count=3),
        line("End of the battle: one of your markers by your opponent's home objective", 'battle', 'my_marker_on_their_home', 5),
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
        line('A unit of yours secured the asset this turn', 'turn', 'completed_any', 4, action='secure-asset'),
        line('You destroyed an enemy unit that started the turn within range of a central objective', 'turn',
             'killed_started', 2, where='central'),
        HOLD_ANY_4,
        _command('Hold three or more objectives', 'hold_at_least', 4, count=3),
    ]),
    'vital-link': ('Vital Link', 'priority-assets', [
        line('Hold a central objective', 'turn', 'hold_kind', 2, kind='central'),
        line('Each of your operation markers on the central objectives you hold', 'turn', 'markers_on_held_central', 1),
        HOLD_ANY_4,
        _command('One of those is a central objective', 'hold_kind', 4, kind='central'),
        line("End of the battle: hold your opponent's home objective", 'battle', 'hold_their_home', 10),
    ]),
    'extract-relic': ('Extract Relic', 'priority-assets', [
        line('A unit of yours made a sensor sweep this turn', 'turn', 'completed_any', 4, action='sensor-sweep'),
        line('You destroyed an enemy unit that started the turn within range of an objective', 'turn', 'killed_started', 3,
             where='in_range'),
        line("Only one of your opponent's markers is left, with your unit and no enemy in its area", 'turn', 'lone_marker', 4,
             whose='theirs'),
        HOLD_ANY_4,
        line('End of the battle: only one of their markers is left, held as above', 'battle', 'lone_marker', 5, whose='theirs'),
    ]),
    'vanguard-operation': ('Vanguard Operation', 'priority-assets', [
        line('A unit of yours made a vanguard operation this turn', 'turn', 'completed_any', 4, action='vanguard-operation'),
        line('Destroy an enemy unit this turn', 'turn', 'killed_any', 2),
        HOLD_ANY_4,
        line("End of the battle: hold your opponent's home objective", 'battle', 'hold_their_home', 10),
    ]),
    'sabotage': ('Sabotage', 'priority-assets', [
        line('Each unit that committed sabotage this turn', 'turn', 'per_completed', 3, action='sabotage'),
        line("Each of those within range of an objective in your opponent's territory", 'turn', 'per_completed_their_territory', 2,
             action='sabotage'),
        HOLD_ANY_4,
    ]),
}


# The secondary missions playable as Fixed (their Fixed values; the Tactical deck comes later)
SECONDARIES = {
    'a-grievous-blow': ('A Grievous Blow', [
        line(f'Each enemy unit of {BIG_UNIT}+ models (starting strength) destroyed this turn, up to 5VP a turn', 'any_turn',
             'per_big_unit_killed', 4, turn_cap=5),
    ]),
    'assassination': ('Assassination', [
        line('Each enemy Character model destroyed this turn', 'any_turn', 'per_character_killed', 3),
        line(f'Each of those with {TOUGH_CHARACTER_WOUNDS}+ wounds', 'any_turn', 'per_character_killed', 1, tough=True),
    ]),
    'bring-it-down': ('Bring It Down', [
        line(f'Each enemy model with {BIG_MODEL_WOUNDS}+ wounds destroyed this turn, up to 5VP a turn', 'any_turn',
             'per_big_model_killed', 4, turn_cap=5),
    ]),
    'engage-on-all-fronts': ('Engage on All Fronts', [
        line('A presence in three table quarters (units wholly within, not within 6" of the centre, not Aircraft '
             'or battle-shocked)', 'turn', 'presence', 2, either='quarters', count=3),
        line('A presence in all four table quarters', 'turn', 'presence', 4, either='quarters', count=4),
    ]),
}


# Mission actions: (name, what it is started on, how often, which must differ between units starting it this
# phase, first round, when it completes, the marker it places). 'on' is 'objective' (within range of one,
# not your home), 'central' (within range of a central objective), 'trap' (a terrain area: see Booby Trap),
# 'their_terrain' (a terrain area in your opponent's territory), 'enemy' (within 18" and visible), or
# 'marker' (one to remove).
ACTIONS = {
    'booby-trap': {'name': 'Booby Trap', 'on': 'trap', 'limit': None, 'distinct': True, 'from_round': 1, 'completes': 'now', 'tag': 'trapped'},
    'decoy': {'name': 'Decoy', 'on': 'objective', 'limit': None, 'distinct': True, 'from_round': 1, 'completes': 'end', 'tag': 'decoyed'},
    'sensor-sweep': {'name': 'Sensor Sweep', 'on': 'central', 'limit': 1, 'distinct': False, 'from_round': 1, 'completes': 'end', 'tag': None},
    'triangulate': {'name': 'Triangulate', 'on': 'objective', 'limit': 1, 'distinct': False, 'from_round': 2, 'completes': 'end',
                    'tag': 'triangulated'},
    'surveil': {'name': 'Surveil the Foe', 'on': 'enemy', 'limit': None, 'distinct': True, 'from_round': 1, 'completes': 'now', 'tag': None},
    'extract-intelligence': {'name': 'Extract Intelligence', 'on': 'objective', 'limit': None, 'distinct': True, 'from_round': 2,
                             'completes': 'end', 'tag': 'intel'},
    'secure-asset': {'name': 'Secure Asset', 'on': 'objective', 'limit': 1, 'distinct': False, 'from_round': 1, 'completes': 'end', 'tag': None},
    'maintain-control': {'name': 'Maintain Control', 'on': 'central', 'limit': 1, 'distinct': False, 'from_round': 1, 'completes': 'end',
                         'tag': 'linked'},
    'vanguard-operation': {'name': 'Vanguard Operation', 'on': 'their_terrain', 'limit': 1, 'distinct': False, 'from_round': 1,
                           'completes': 'end', 'tag': None},
    'sabotage': {'name': 'Sabotage', 'on': 'objective', 'limit': None, 'distinct': True, 'from_round': 1, 'completes': 'end', 'tag': None},
    # Tactical cards' actions: 'outside_terrain' is a terrain area not within your territory
    'plunder': {'name': 'Plunder', 'on': 'outside_terrain', 'limit': 1, 'distinct': False, 'from_round': 1, 'completes': 'now', 'tag': None},
    'cleanse': {'name': 'Cleanse', 'on': 'objective', 'limit': None, 'distinct': True, 'from_round': 1, 'completes': 'end', 'tag': None},
}
CARD_ACTIONS = {'plunder': 'plunder', 'cleanse': 'cleanse'}  # A Tactical card active for you -> the action it offers
ACTION_NAMES = {key: a['name'] for key, a in ACTIONS.items()}
PRIMARY_ACTIONS = {'death-trap': ['booby-trap'], 'smoke-and-mirrors': ['decoy'], 'locate-and-deny': ['sensor-sweep'],
                   'extract-relic': ['sensor-sweep'], 'triangulation': ['triangulate'], 'surveil-the-foe': ['surveil'],
                   'gather-intel': ['extract-intelligence'], 'secure-asset': ['secure-asset'], 'vital-link': ['maintain-control'],
                   'vanguard-operation': ['vanguard-operation'], 'sabotage': ['sabotage']}
LOCATE_MARKERS = 5  # Locate and Deny: markers placed in terrain areas outside your deployment zone at the start


# The Tactical deck: (name, lines with their Tactical values, what happens when drawn). When drawn:
# 'redraw' says when you may draw a new card instead (shuffling this one back): 'round_1', 'with:<card>'
# (that card is active for you), 'no_big_units' / 'no_big_models' (no such enemy on the battlefield);
# 'must_redraw': 'round_1' redraws without asking; 'choose' names a choice: 'beacon' (yours), 'tempting'
# (your opponent's) or 'guards' (yours, optional).
TACTICAL = {
    'no-prisoners': ('No Prisoners', [
        line('Each enemy unit destroyed this turn, up to 5VP', 'any_turn', 'per_kill', 2, turn_cap=5)], {}),
    'overwhelming-force': ('Overwhelming Force', [
        line('Each enemy unit destroyed that started the turn within range of an objective, up to 5VP', 'any_turn',
             'per_kill_started', 3, turn_cap=5, where='in_range')], {}),
    'plunder': ('Plunder', [
        line('A terrain area was plundered this turn (the Plunder action)', 'turn', 'completed_any', 5, action='plunder')],
        {'redraw': 'with:cleanse'}),
    'display-of-might': ('Display of Might', [
        line("More of your units than the enemy's wholly within No Man's Land", 'turn', 'nml_majority', 2),
        line('The same, at the end of your opponent\'s turn', 'opp_turn', 'nml_majority', 5)], {}),
    'outflank': ('Outflank', [
        line('A unit within 6" of a battlefield edge, not within your territory', 'turn', 'outflank', 3, either='outflank', level=1),
        line('Units within 6" of opposite edges, one of them not within your territory', 'turn', 'outflank', 5,
             either='outflank', level=2)], {}),
    'beacon': ('Beacon', [
        line('Your beacon unit is on the battlefield, not within your deployment zone', 'opp_turn_or_r5', 'beacon', 3,
             either='beacon', level=1),
        line('Your beacon unit is on the battlefield, not within your territory', 'opp_turn_or_r5', 'beacon', 5,
             either='beacon', level=2)], {'choose': 'beacon'}),
    'cleanse': ('Cleanse', [
        line('One objective cleansed by your army this turn (the Cleanse action)', 'turn', 'completed_count', 2,
             either='cleanse', action='cleanse', least=1, most=1),
        line('Two or more objectives cleansed by your army this turn', 'turn', 'completed_count', 5, either='cleanse',
             action='cleanse', least=2)], {'redraw': 'with:plunder'}),
    'a-grievous-blow': ('A Grievous Blow', [
        line(f'Each enemy unit of {BIG_UNIT}+ models (starting strength) destroyed this turn, up to 5VP', 'any_turn',
             'per_big_unit_killed', 5, turn_cap=5)], {'redraw': 'no_big_units'}),
    'defend-stronghold': ('Defend Stronghold', [
        line('Hold your home objective', 'opp_turn_or_r5', 'hold_my_home', 3, rounds=(2, 5)),
        line('And no enemy units within your deployment zone', 'opp_turn_or_r5', 'home_and_zone_clear', 2, rounds=(2, 5))],
        {'must_redraw': 'round_1'}),
    'engage-on-all-fronts': ('Engage on All Fronts', [
        line('A presence in three table quarters (units wholly within, not within 6" of the centre)', 'turn', 'presence', 3,
             either='quarters', count=3),
        line('A presence in all four table quarters', 'turn', 'presence', 5, either='quarters', count=4)], {}),
    'secure-no-mans-land': ("Secure No Man's Land", [
        line("Hold two or more objectives within No Man's Land (not your home)", 'turn', 'nml_objectives', 5, count=2)], {}),
    'forward-position': ('Forward Position', [
        line("Hold your opponent's home objective, or every expansion objective", 'turn', 'forward_position', 5)],
        {'redraw': 'round_1'}),
    'centre-ground': ('Centre Ground', [
        line('A unit of yours within 3" of the centre, no enemy within 3"', 'turn', 'centre_ground', 3, either='centre', clear=3),
        line('A unit of yours within 3" of the centre, no enemy within 6"', 'turn', 'centre_ground', 5, either='centre', clear=6)], {}),
    'assassination': ('Assassination', [
        line('An enemy Character model destroyed this turn, or every enemy Character destroyed in the battle', 'any_turn',
             'assassination_tactical', 5)], {}),
    'a-tempting-target': ('A Tempting Target', [
        line('Hold your tempting target (an objective in No Man\'s Land your opponent chose)', 'turn', 'tempting', 5)],
        {'choose': 'tempting'}),
    'behind-enemy-lines': ('Behind Enemy Lines', [
        line("Each unit wholly within your opponent's deployment zone, up to 5VP", 'turn', 'behind_lines', 3, turn_cap=5)],
        {'redraw': 'round_1'}),
    'bring-it-down': ('Bring It Down', [
        line(f'Each enemy model with {BIG_MODEL_WOUNDS}+ wounds destroyed this turn, up to 5VP', 'any_turn',
             'per_big_model_killed', 5, turn_cap=5)], {'redraw': 'no_big_models'}),
    'burden-of-trust': ('Burden of Trust', [
        line('Each objective guarded by your army, up to 5VP', 'opp_turn_or_r5', 'guarded', 2, turn_cap=5)], {'choose': 'guards'}),
}
TACTICAL_PREFIX = 'tactical:'  # A Tactical card's key in a game, apart from the Fixed card of the same name


def tactical(key):
    """{'key', 'name', 'lines', 'when_drawn'} for a Tactical card (its key with or without TACTICAL_PREFIX)."""
    key = key[len(TACTICAL_PREFIX):] if key.startswith(TACTICAL_PREFIX) else key
    name, lines, when_drawn = TACTICAL[key]
    full = TACTICAL_PREFIX + key
    return {'key': full, 'name': name, 'when_drawn': when_drawn,
            'lines': [dict(entry, id=f'{full}:{i}') for i, entry in enumerate(lines)]}


def primary(key):
    """{'key', 'name', 'disposition', 'lines': [line with 'id']} for a primary mission's key."""
    name, disposition, lines = PRIMARIES[key]
    return {'key': key, 'name': name, 'disposition': disposition,
            'lines': [dict(entry, id=f'{key}:{i}') for i, entry in enumerate(lines)]}


def secondary(key):
    """{'key', 'name', 'lines': [line with 'id']} for a secondary mission's key."""
    name, lines = SECONDARIES[key]
    return {'key': key, 'name': name, 'lines': [dict(entry, id=f'{key}:{i}') for i, entry in enumerate(lines)]}


def card(key):
    """A primary, Fixed or Tactical (TACTICAL_PREFIX) mission card by its key."""
    if key.startswith(TACTICAL_PREFIX):
        return tactical(key)
    return primary(key) if key in PRIMARIES else secondary(key)


def scores_each(entry):
    """Whether a line scores its VP for each one (objective held, unit destroyed...) rather than once."""
    return (entry['score'] or '').startswith('per_')


def timing(entry):
    """When a line scores, for people: 'End of your turn · rounds 2-5' and so on."""
    when = {'turn': 'End of your turn', 'any_turn': 'End of every turn', 'battle': 'End of the battle',
            'command': 'End of your Command phase', 'opp_turn': "End of your opponent's turn",
            'opp_turn_or_r5': "End of your opponent's turn (or of round 5)"}[entry['when']]
    if entry['when'] == 'command' and entry['round5_end']:
        when += ' (round 5: end of your turn)'
    first, last = entry['rounds']
    if entry['when'] == 'battle' or (first, last) == (1, 5):
        return when
    return f'{when} · round {first}' if first == last else f'{when} · rounds {first}-{last}'


def due(entry, when, round_number):
    """Whether a line scores at this timing ('turn', 'any_turn', 'command', 'opp_turn', 'r5_end' (the end of
    round 5, at the end of your own turn) or 'battle') in this round."""
    first, last = entry['rounds']
    if when == 'battle':
        return entry['when'] == 'battle'
    if entry['when'] == 'battle' or not first <= round_number <= last:
        return False
    if entry['when'] == 'command':
        if entry['round5_end'] and round_number == 5:
            return when == 'turn'
        return when == 'command'
    if entry['when'] == 'opp_turn_or_r5':
        return when in ('opp_turn', 'r5_end')
    return entry['when'] == when


def score_lines(key, when, round_number, ctx):
    """[(line, VP, how)] for the lines of a mission card due now, each 'either' group's best only, each
    line within its `turn_cap`."""
    found, best = [], {}
    for entry in card(key)['lines']:
        if entry['needs'] or not entry['score'] or not due(entry, when, round_number):
            continue
        vp, how = SCORERS[entry['score']](ctx, entry['vp'], **entry['params'])
        if entry['turn_cap'] is not None and vp > entry['turn_cap']:
            vp, how = entry['turn_cap'], f'{how}, at most {entry["turn_cap"]}VP a turn'
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
    for entry in card(key)['lines']:
        if entry['needs'] or not entry['score']:
            continue
        vp, how = SCORERS[entry['score']](ctx, entry['vp'], **entry['params'])
        out[entry['id']] = (min(vp, entry['turn_cap']) if entry['turn_cap'] is not None else vp, how)
    return out


# --- Twists ------------------------------------------------------------------------------------------------------
# Optional cards both players agree on before the battle: (name, the rule in brief, whether it needs missions).
TWISTS = {
    'nowhere-to-hide': ('Nowhere to Hide', 'Terrain features lose the Solid rule (no change on this flat board, where '
                        'dense terrain blocks sight like light terrain).', False),
    'mirrored-world': ('Mirrored World', 'Both players play the same primary mission: Battlefield Dominance, Meatgrinder, '
                       'Outmanoeuvre, Gather Intel or Sabotage, agreed or rolled.', True),
    'scrambled-communications': ('Scrambled Communications', 'The players swap primary missions.', True),
    'martial-pride': ('Martial Pride', 'Battleline units can start an action after Advancing, and can shoot after '
                      'starting an action.', False),
    'ruinscape': ('Ruinscape', 'Models count as Mobile while making a Normal or Advance move (so tall dense terrain '
                  "doesn't stop them).", False),
    'night-fighting': ('Night Fighting', 'Units are not visible to enemy models more than 18" away.', False),
}
MIRRORED_PRIMARIES = ['battlefield-dominance', 'meatgrinder', 'outmanoeuvre', 'gather-intel', 'sabotage']  # D6: 1-5 (6 rerolls)
NIGHT_VISIBILITY = 18
