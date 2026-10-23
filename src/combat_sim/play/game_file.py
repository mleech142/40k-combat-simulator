"""
A game as a file the players pass back and forth by hand (Discord, email): the backup for when there's no
relay (docs/play-mode-design.md, Connection). It is the game itself, frozen armies and event log, so any
install replays it the same; `uid` says which game it is, so the copy on each PC can be brought up to date.

Everything that comes from the other player's app (a game file, a game joined on the relay, the moves fetched
from it) is checked here before it is saved (check_game, check_events): plain JSON, no '<' or '>' in any text
(so nothing in it can become HTML on the page), identifiers that are simple names, numbers that are numbers,
and events that only name models and units the game has. Rule text is never sent (strip_text).
Plain data, like the rest of the play package.
"""

import copy
import json
import math
import re

from .game import MODEL_FIELDS, PHASES, new_game, replay
from .seats import ACTION_TYPES

FORMAT = 'combat-sim-game'
FORMAT_VERSION = 1
SAFE_ID = re.compile(r'[A-Za-z0-9_.:+-]{1,80}')  # Units', models', objectives' and terrain's IDs, stratagem keys
VERSION = re.compile(r'(\d{1,3})\.(\d{1,3})\.(\d{1,3})')
TURN_PHASES = set(PHASES) | {'setup', 'deployment', 'ended'}
MAX_TEXT = 2000  # Characters in any one string
MAX_DEPTH = 16


class GameFileError(ValueError):
    pass


def version_key(version):
    """'2.10.1' -> (2, 10, 1), to compare app versions by number (anything unreadable counts as 0)."""
    key = []
    for piece in str(version or '').split('.'):
        digits = ''.join(c for c in piece if c.isdigit())
        key.append(int(digits) if digits else 0)
    return tuple(key)


def newer(version, than):
    return version_key(version) > version_key(than)


def plausible_version(version, than):
    """Whether a version another app claims is one: X.Y.Z, and not a leap of more than one major version past
    `than` (a made-up '99.0.0' would otherwise stop everyone else playing until they 'update')."""
    match = VERSION.fullmatch(str(version or ''))
    return bool(match) and int(match.group(1)) <= version_key(than)[0] + 1


def strip_text(game):
    """A copy of a game without rule text: games frozen before stratagems and rules had refs kept it in each
    entry and in the events that used one."""
    game = copy.deepcopy(game)
    for army in game.get('armies') or []:
        for entry in [] if army is None else (army.get('stratagems') or []) + (army.get('rules') or []):
            if isinstance(entry, dict):
                entry.pop('text', None)
    for event in game.get('events') or []:
        if isinstance(event, dict) and ('stratagem' in event or 'rule' in event):
            event.pop('text', None)
    return game


def export_game(game, sent_by):
    """The file for a game ({'uid', 'name', 'app_version', 'armies', 'events', 'battlefield', 'setup', 'twist'}, and
    'pending' while an online game waits for the other player's army: see check_invite), saying which seat sent it
    (None from a hot-seat game)."""
    game = strip_text(game)
    return {'format': FORMAT, 'format_version': FORMAT_VERSION, 'sent_by': sent_by, 'pending': game.get('pending'),
            **{k: game[k] for k in ('uid', 'name', 'app_version', 'armies', 'events', 'battlefield', 'setup', 'twist')}}


def read_game(data, version):
    """A game file's game, checked (check_game) for this app (`version`): it must be a game it can read."""
    if not isinstance(data, dict) or data.get('format') != FORMAT:
        raise GameFileError('That is not a game file.')
    if not isinstance(data.get('format_version'), int) or data['format_version'] > FORMAT_VERSION:
        raise GameFileError('That game file was saved by a newer version of the app: update to open it.')
    game = strip_text({k: data.get(k) for k in ('uid', 'name', 'app_version', 'armies', 'events', 'battlefield', 'setup', 'twist')})
    game['sent_by'] = data.get('sent_by') if data.get('sent_by') in (0, 1) else None
    game['setup'] = bool(game['setup'])
    check_game(game, version)
    return game


def compare(mine, theirs):
    """How a file's events stand against this copy's: 'same', 'newer' (they carry on from mine), 'older'
    (mine carry on from theirs) or 'conflict' (each has events the other doesn't). Rule text is ignored."""
    mine = strip_text({'events': mine})['events']
    shared = min(len(mine), len(theirs))
    if mine[:shared] != theirs[:shared]:
        return 'conflict'
    if len(theirs) == len(mine):
        return 'same'
    return 'newer' if len(theirs) > len(mine) else 'older'


# --- Checks -------------------------------------------------------------------------------------------------

def _damaged(what):
    return GameFileError(f'That game is damaged or not safe to open ({what}).')


def _plain(value, depth=0):
    """Plain JSON only: no '<' or '>' in text (nothing can become HTML), finite numbers, not too deep."""
    if depth > MAX_DEPTH:
        raise _damaged('too deep')
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise _damaged('a key that is not text')
            _plain(key, depth + 1)
            _plain(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _plain(item, depth + 1)
    elif isinstance(value, str):
        if '<' in value or '>' in value or len(value) > MAX_TEXT or any(ord(c) < 32 and c not in '\n\t' for c in value):
            raise _damaged(f'text it may not contain: {value[:40]!r}')
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise _damaged('a number that is not one')
    elif value is not None and not isinstance(value, (bool, int)):
        raise _damaged('something that is not JSON')


def _id(value, what):
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise _damaged(f'{what} {str(value)[:40]!r}')
    return value


def _number(value, what, none=False):
    if (value is None and none) or (isinstance(value, (int, float)) and not isinstance(value, bool)):
        return value
    raise _damaged(f'{what} is not a number')


def _int(value, what, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise _damaged(f'{what} out of range')
    return value


def _list(value, what):
    if not isinstance(value, list):
        raise _damaged(f'{what} is not a list')
    return value


def _dict(value, what):
    if not isinstance(value, dict):
        raise _damaged(f'{what} is not an object')
    return value


def _check_army(army):
    _dict(army, 'an army')
    if not isinstance(army.get('name'), str):
        raise _damaged('an army with no name')
    uids = set()
    for unit in _list(army.get('units'), 'units'):
        _dict(unit, 'a unit')
        uids.add(_id(unit.get('uid'), 'a unit ID'))
        for part in _list(unit.get('parts'), 'a unit\'s parts') or [None]:
            _dict(part, 'a unit\'s part')
            _int(part.get('models'), 'a model count', 1, 500)
            _number(part.get('points'), 'points', none=True)
            for name, stat in _dict(part.get('stats'), 'stats').items():
                if stat is not None and not isinstance(stat, (int, float, str)):
                    raise _damaged(f'the {name} stat')
            for weapon in _list(part.get('weapons'), 'weapons'):
                _dict(weapon, 'a weapon')
                _int(weapon.get('qty'), 'a weapon count', 0, 500)
    if len(uids) != len(army['units']):
        raise _damaged('two units with one ID')
    for entry in _list(army.get('stratagems', []), 'stratagems') + _list(army.get('rules', []), 'rules'):
        _dict(entry, 'a stratagem or rule')
        _id(entry.get('key'), 'a stratagem key')
        if entry.get('kind') not in ('stratagem', 'rule'):
            raise _damaged('a stratagem that is not one')
        _int(entry.get('cp', 0), 'a stratagem\'s CP', 0, 99)


def _check_battlefield(battlefield):
    if battlefield is None:
        return
    _dict(battlefield, 'the battlefield')
    for piece in _list(battlefield.get('objectives', []), 'objectives') + _list(battlefield.get('terrain', []), 'terrain'):
        _dict(piece, 'an objective or terrain piece')
        _id(piece.get('id'), 'an objective or terrain ID')
        _number(piece.get('x'), 'a position')
        _number(piece.get('y'), 'a position')


def _check_position(position):
    if position is None:
        return
    _dict(position, 'a position')
    _number(position.get('x'), 'a position')
    _number(position.get('y'), 'a position')
    _number(position.get('angle'), 'an angle', none=True)


def _check_rolls(rolls):
    for group in _list(rolls, 'dice'):
        _dict(group, 'a dice roll')
        for key in ('need', 'crit_on', 'size', 'modifier', 'total'):
            if key in group:
                _number(group[key], 'a dice roll', none=True)
        for die in _list(group.get('dice', []), 'dice'):
            _dict(die, 'a die')
            _int(die.get('face'), 'a die', 1, 100)
            if die.get('rerolled_from') is not None:
                _int(die['rerolled_from'], 'a die', 1, 100)


def check_events(state, events):
    """Each event, against the game it extends (`state`, as new_game makes it: unit and model IDs never change)."""
    units = {u['id'] for u in state['units']}
    models = {m['id'] for u in state['units'] for m in u['models']}
    for event in _list(events, 'the moves'):
        _dict(event, 'a move')
        action = _dict(event.get('action'), 'a move\'s action')
        if action.get('type') not in ACTION_TYPES:
            raise _damaged(f'an unknown action {str(action.get("type"))[:30]!r}')
        if event.get('by') not in (0, 1, None):
            raise _damaged('a move by nobody')
        _check_rolls(event.get('rolls', []))
        for model_id, change in _dict(event.get('models', {}), 'models').items():
            if model_id not in models:
                raise _damaged(f'a model the game doesn\'t have: {model_id[:40]!r}')
            _dict(change, 'a model\'s change')
            if set(change) - set(MODEL_FIELDS):
                raise _damaged('a model change it can\'t make')
            if 'wounds_left' in change:
                _int(change['wounds_left'], 'wounds', -1000, 1000)
            if 'alive' in change and not isinstance(change['alive'], bool):
                raise _damaged('a model neither alive nor dead')
            _check_position(change.get('position'))
        for unit_id in _dict(event.get('units', {}), 'units'):
            if unit_id not in units:
                raise _damaged(f'a unit the game doesn\'t have: {unit_id[:40]!r}')
        if 'turn' in event:
            turn = _dict(event['turn'], 'the turn')
            if turn.get('phase') not in TURN_PHASES or turn.get('player') not in (0, 1):
                raise _damaged('a turn that is not one')
            _int(turn.get('round', 0), 'the battle round', 0, 99)
        for player in _list(event.get('players', []), 'players')[:2]:
            _int(_dict(player, 'a player').get('cp', 0), 'Command points', -99, 999)
        for objective in _list(event.get('layout', []), 'objectives'):
            _dict(objective, 'an objective')
            _id(objective.get('id'), 'an objective ID')
            _number(objective.get('x'), 'a position')
            _number(objective.get('y'), 'a position')
        for objective_id, controller in _dict(event.get('objectives', {}), 'objectives').items():
            _id(objective_id, 'an objective ID')
            if controller not in (0, 1, None):
                raise _damaged('an objective held by nobody')


def check_game(game, version):
    """
    A game from another app, checked before it is saved: plain JSON with no HTML, simple IDs and real numbers
    in the armies, battlefield and events, an app version that can be one, and a log that replays (else
    GameFileError). `version` is this app's.
    """
    _guarded(_check_game, game, version)


def check_invite(game, seat):
    """
    An online game that waits for the joiner's army (seat `seat` empty), as the relay hands it out: the creator's
    army checked as check_game checks one, no moves, and `pending`, the choices that wait for both armies:
    {'battlefield' (None, 'pairing:<A|B|C|random>' or a battlefield), 'twist' (as the new-game form sends it),
    'setup', 'name' (None: "Red vs Blue")}.
    """
    _guarded(_check_invite, game, seat)


def _check_invite(game, seat):
    _plain(game)
    if not isinstance(game.get('uid'), str) or not SAFE_ID.fullmatch(game['uid']):
        raise _damaged('its ID')
    armies = _list(game.get('armies'), 'armies')
    if len(armies) != 2 or armies[seat] is not None or game.get('events'):
        raise _damaged('not a game waiting for your army')
    _check_army(armies[1 - seat])
    pending = _dict(game.get('pending'), 'its setup')
    battlefield = pending.get('battlefield')
    if isinstance(battlefield, dict):
        _check_battlefield(battlefield)
    elif battlefield is not None and not (isinstance(battlefield, str) and battlefield.startswith('pairing:')):
        raise _damaged('its battlefield')


def check_more(game, events):
    """Moves another app added (fetched from the relay) to a game already saved here (`game`: its armies,
    battlefield, setup, twist and events so far), checked as check_game checks a whole game's."""
    _guarded(_check_more, game, events)


def _guarded(check, *args):
    try:
        check(*args)
    except GameFileError:
        raise
    except Exception:  # Whatever is wrong in it, the log doesn't replay
        raise GameFileError('That game is damaged: its moves could not be replayed.')


def _check_more(game, events):
    _plain(events)
    check_events(new_game(game['armies'], game['battlefield'], setup=bool(game['setup']), twist=game['twist']), events)
    replay(game['armies'], list(game['events']) + list(events), game['battlefield'], bool(game['setup']), game['twist'])


def _check_game(game, version):
    _plain(game)
    if not isinstance(game.get('uid'), str) or not SAFE_ID.fullmatch(game['uid']):
        raise _damaged('its ID')
    if not isinstance(game.get('name'), str) or not game['name'].strip() or len(game['name']) > 200:
        raise _damaged('its name')
    if not plausible_version(game.get('app_version'), version):
        raise _damaged(f'an app version that isn\'t one: {str(game.get("app_version"))[:20]!r}')
    armies = _list(game.get('armies'), 'armies')
    if len(armies) != 2:
        raise _damaged('not two armies')
    for army in armies:
        _check_army(army)
    _check_battlefield(game.get('battlefield'))
    twist = game.get('twist')
    if twist is not None and not isinstance(_dict(twist, 'the twist').get('key'), str):
        raise _damaged('the twist')
    json.dumps(game)
    check_events(new_game(armies, game['battlefield'], setup=game['setup'], twist=twist), game.get('events'))
    replay(armies, game['events'], game['battlefield'], game['setup'], twist)
