"""
The Play relay in memory, for tests: the same rules as relay/src/relay.js (create, invite, join, read, append,
close), behind the same interface as connections.RelayClient. Put it in an app's config (`app.config['RELAY']`)
and both test installs share one relay without Node. tests/test_play_online.py keeps one game going through the
real dev relay (relay/dev-server.mjs) to check the two agree.
"""

import hashlib
import json
import re
import secrets

from combat_sim.play.game_file import VERSION, newer, plausible_version as _plausible_than
from combat_sim.play.relay_client import RelayError

FORMAT = 'combat-sim-game'
CODE_LETTERS = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'


def _copy(value):
    return json.loads(json.dumps(value))


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def plausible_version(version, than=None):
    """As relay.js: X.Y.Z, and (with `than`) not more than one major version past it."""
    return bool(VERSION.fullmatch(str(version or ''))) if than is None else _plausible_than(version, than)


def _hash(token):
    return hashlib.sha256(str(token).encode()).hexdigest()


def _name(text):
    return re.sub(r'[<>"\'`\x00-\x1f]', '', str(text or '')).strip()[:40] or None


class MemoryRelay:
    def __init__(self):
        self.games = {}
        self.down = False  # True: every request fails as if the relay couldn't be reached

    def _reach(self):
        if self.down:
            raise RelayError("Couldn't reach the relay: check your internet connection.")

    def _game(self, code):
        self._reach()
        game = self.games.get(code)
        if game is None:
            raise RelayError('There is no game with that code (games are deleted after 90 days without a move).', 404)
        return game

    def _state(self, game):
        return {'count': len(game['events']), 'app_version': game['app_version'],
                'joined': all(s is not None for s in game['seats']), 'names': list(game['names'])}

    def _who(self, game, token):
        seat = next((s for s in (0, 1) if token and game['seats'][s] == _hash(token)), None)
        if seat is None:
            raise RelayError("This app isn't a player in that game.", 403)
        return seat

    def create(self, file, seat, name=None):
        self._reach()
        if not file or file.get('format') != FORMAT or not isinstance(file.get('events'), list) or not isinstance(file.get('armies'), list):
            raise RelayError('Send a game file.', 400)
        if seat not in (0, 1):
            raise RelayError('Say which player you play.', 400)
        if not plausible_version(file.get('app_version')):
            raise RelayError('That game file has no app version.', 400)
        armies = file['armies']
        if len(armies) != 2 or not armies[seat] or (not armies[1 - seat] and file['events']):
            raise RelayError("Send your army, and the other player's or none.", 400)
        code = ''.join(secrets.choice(CODE_LETTERS) for _ in range(10))
        token = secrets.token_hex(32)
        stored = _copy({k: v for k, v in file.items() if k != 'events'})
        seats, names = [None, None], [None, None]
        seats[seat], names[seat] = _hash(token), _name(name)
        self.games[code] = {'file': stored, 'events': _copy(file['events']), 'seats': seats, 'names': names,
                            'open': not armies[1 - seat], 'app_version': file['app_version']}
        return {'code': code, 'token': token, 'seat': seat}

    def invite(self, code):
        game = self._game(code)
        free = next((s for s in (0, 1) if game['seats'][s] is None), None)
        if free is None:
            raise RelayError('Both players have already joined that game.', 409)
        return {'code': code, 'seat': free, 'needs_army': not game['file']['armies'][free], 'names': list(game['names']),
                'game': _copy(game['file'])}

    def join(self, code, name=None, completed=None):
        game = self._game(code)
        free = next((s for s in (0, 1) if game['seats'][s] is None), None)
        if free is None:
            raise RelayError('Both players have already joined that game.', 409)
        file = game['file']
        if not file['armies'][free]:  # The game as the joiner's app completed it with their army: nothing else may change
            ok = (completed and completed.get('format') == FORMAT and isinstance(completed.get('armies'), list)
                  and len(completed['armies']) == 2 and completed['armies'][free]
                  and _canonical(completed['armies'][1 - free]) == _canonical(file['armies'][1 - free])
                  and completed.get('uid') == file.get('uid') and not completed.get('events')
                  and plausible_version(completed.get('app_version'), game['app_version']))
            if not ok:
                raise RelayError('Join with your army in the game you were invited to.', 400)
            game['file'] = _copy({k: v for k, v in completed.items() if k != 'events'})
            game['open'] = False
        token = secrets.token_hex(32)
        game['seats'][free], game['names'][free] = _hash(token), _name(name)
        return {'code': code, 'token': token, 'seat': free, 'names': list(game['names']),
                'game': dict(_copy(game['file']), events=_copy(game['events']))}

    def read(self, code, token, since, file=False):
        game = self._game(code)
        self._who(game, token)
        since = max(0, int(since or 0))
        answer = dict(self._state(game), since=since, events=_copy(game['events'][since:]))
        if file:
            answer['game'] = _copy(game['file'])
        return answer

    def append(self, code, token, base, events):
        from combat_sim import __version__
        game = self._game(code)
        self._who(game, token)
        state = self._state(game)
        if newer(game['app_version'], __version__):
            raise RelayError(f"This game has been played on version {game['app_version']} of the app: update to carry on.", 426, state)
        if game['open']:
            raise RelayError("The other player hasn't joined with their army yet.", 409, state)
        if base != len(game['events']):
            raise RelayError('The other player has moved since: get their moves first.', 409, state)
        game['events'] += _copy(events)
        if newer(__version__, game['app_version']):
            game['app_version'] = __version__
        return dict(self._state(game), count=len(game['events']))

    def close(self, code, token):
        game = self._game(code)
        self._who(game, token)
        del self.games[code]
        return {'closed': code}
