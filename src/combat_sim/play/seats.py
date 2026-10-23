"""
Seats: when each player's app holds one side of a game (docs/play-mode-design.md, Connection), whose
each action is and whose input the game is waiting for. A hot-seat game has no seat: one app plays both.
The engine still checks whether an action is allowed now; this only says which player's app sends it.
"""

from .game import ActionError, mover, unit_by_id

PLAYER_ACTIONS = {'stratagem', 'rule', 'orders', 'correction', 'accept', 'decline', 'choose_secondaries', 'tactical',
                  'set_guards'}  # Name their player ('player')
CHOICE_ACTIONS = {'condemn', 'place_markers', 'beacon', 'tempting'}  # Answer the mission's current choice
UNIT_ACTIONS = {'place', 'move', 'advance', 'stationary', 'reset_move', 'charge', 'charge_move', 'pile_in', 'fight', 'overrun',
                'consolidate', 'shoot', 'start_action', 'effect', 'remove_effect'}  # Are their unit's player's
FLOW_ACTIONS = {'end_deployment', 'next_phase', 'roll_objectives', 'roll_off', 'roll_mirrored', 'choose_role'}  # Whoever the game waits on
ACTION_TYPES = PLAYER_ACTIONS | CHOICE_ACTIONS | UNIT_ACTIONS | FLOW_ACTIONS  # Every action the engine takes


def controller(state):
    """Who moves the game on (next phase, setup rolls): whoever is moving or picking in the Fight phase's
    steps, else the player whose turn or step it is."""
    turn = state['turn']
    if turn['phase'] == 'fight' and turn.get('step') == 'fight':
        picker = (turn.get('fight') or {}).get('picker')
        return turn['player'] if picker is None else picker
    if turn['phase'] == 'fight' and turn.get('step') in ('pile_in', 'consolidate'):
        return mover(turn)
    return turn['player']


def waiting_on(state):
    """The players whose input the game needs now (both while each still chooses secondaries; none once it's over)."""
    turn = state['turn']
    if turn['phase'] == 'ended':
        return []
    if state.get('pending'):
        return [1 - state['pending']['player']]
    mission = state.get('mission') or {}
    if mission.get('choice'):
        return [mission['choice']['player']]
    if turn['phase'] == 'setup' and turn.get('step') == 'secondaries':
        return [p for p in (0, 1) if not mission['secondaries'].get(str(p))]
    return [controller(state)]


def actor(state, action):
    """The player whose action this is, or None when it names no player or unit the game has (the engine refuses it)."""
    kind = action.get('type')
    if kind in PLAYER_ACTIONS:
        player = action.get('player')
        return player if player in (0, 1) else None
    if kind in CHOICE_ACTIONS:
        choice = (state.get('mission') or {}).get('choice')
        return choice['player'] if choice else None
    if kind in UNIT_ACTIONS:
        try:
            return unit_by_id(state, action.get('unit'))['player']
        except (KeyError, StopIteration, TypeError):
            return None
    return controller(state)


def check_seat(state, action, seat):
    """Refuse an action that isn't the seat's player's (a hot-seat game, seat None, sends anything)."""
    player = actor(state, action)
    if seat is None or player is None or player == seat:
        return
    names = [p['name'] for p in state['players']]
    raise ActionError(f"That is {names[player]}'s to do: this app plays {names[seat]}.")
