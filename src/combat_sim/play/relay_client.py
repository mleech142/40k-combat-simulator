"""
Talking to the Play relay (relay/, docs/relay.md): it keeps an online game's event log in order between the
two players' apps. Only the app's server talks to it, so a game's secret token never reaches a page.
"""

import json
import urllib.error
import urllib.request

from .. import __version__

TIMEOUT = 10  # Seconds
USER_AGENT = f'combat-sim/{__version__}'


class RelayError(Exception):
    """The relay refused a request (`status`, its answer in `body`) or couldn't be reached (status None)."""

    def __init__(self, message, status=None, body=None):
        super().__init__(message)
        self.status = status
        self.body = body or {}


def _call(base_url, method, path, body=None, token=None):
    if not base_url:
        raise RelayError('No relay is set: add its address on the Setup page.')
    headers = {'User-Agent': USER_AGENT, 'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    data = json.dumps(body).encode('utf-8') if body is not None else None
    request = urllib.request.Request(base_url.rstrip('/') + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as error:
        try:
            answer = json.loads(error.read().decode('utf-8'))
        except ValueError:
            answer = {}
        raise RelayError(answer.get('error') or f'The relay answered {error.code}.', error.code, answer)
    except (urllib.error.URLError, OSError, ValueError):
        raise RelayError("Couldn't reach the relay: check your internet connection.")


def create(base_url, game_file, seat, name=None):
    """Put a game on the relay, playing `seat` as `name`: {'code' (the invite code), 'token', 'seat'}."""
    return _call(base_url, 'POST', '/v1/games', {'game': game_file, 'seat': seat, 'name': name})


def join(base_url, code, name=None):
    """Take the free seat in a game as `name`: {'code', 'token', 'seat', 'names' (each seat's player, or None),
    'game' (its game file, every event so far)}."""
    return _call(base_url, 'POST', f'/v1/games/{code}/join', {'name': name})


def read(base_url, code, token, since):
    """The events from the `since`-th on: {'count', 'events', 'app_version', 'joined', 'names'}."""
    return _call(base_url, 'GET', f'/v1/games/{code}?since={int(since)}', token=token)


def append(base_url, code, token, base, events):
    """Add events on top of the relay's `base`-th (409 when the other player has moved since): {'count', ...}."""
    return _call(base_url, 'POST', f'/v1/games/{code}/events', {'base': base, 'events': events, 'app_version': __version__}, token)


def retract(base_url, code, token, count):
    """Take back the last event, while the relay has `count` (and it is ours, with no dice)."""
    return _call(base_url, 'POST', f'/v1/games/{code}/retract', {'count': count}, token)


def close(base_url, code, token):
    """Delete a game from the relay (its code stops working; a new code replaces it)."""
    return _call(base_url, 'POST', f'/v1/games/{code}/close', {}, token)
