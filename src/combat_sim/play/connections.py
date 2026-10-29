"""
How a game's two sides reach each other: its **Connection** (GLOSSARY.md). Four kinds, each an adapter here:

- **Hot-seat**: one app plays both sides.
- **Game file**: this app plays one side; the players swap game files by hand (game_file.py).
- **Relay**: this app plays one side; moves go through the relay (relay_client.py, docs/relay.md). Its "not joined
  yet" state is a game started with your army alone, waiting for the other player's.
- **Same copy**: two players of one self-hosted copy, each in their own browser.

A game's kind is worked out from what it stores (`kind_of`): no stored kind, so games saved before need nothing.
Each adapter answers what differs by kind: which side the request's player plays (`seat`), fetching the other
player's moves before an action (`fetch`) and sending one after (`publish`), how Undo works (`undo_asks`: the
other player accepts it, or it's taken back at once), whether a turn change is emailed (`notifies`), and what the
page is told (`status`). The game session (session.py) talks only to this interface; switching kind (choosing a
side, going online, a new invite code) is here too.

The relay is a port: `relay()` gives the app's `RelayClient` (HTTP), or the adapter in `app.config['RELAY']`
(tests use an in-memory one, tests/memory_relay.py).
"""

import os
import uuid

from flask import current_app

from .. import __version__, access
from ..models import User, db
from ..settings import load_settings
from . import game_file, relay_client

HOT_SEAT, GAME_FILE, RELAY, SAME_COPY = 'hot_seat', 'game_file', 'relay', 'same_copy'
DEFAULT_RELAY_URL = 'https://tabletop-relay.tabletop-relay.workers.dev'  # The project's own relay (docs/relay.md)


# --- The relay port ---------------------------------------------------------------------------

def relay_url():
    """The relay's address: the Setup page's, else RELAY_URL, else the project's own."""
    return load_settings(current_app.config['SETTINGS_JSON']).get('relay_url') or os.environ.get('RELAY_URL') or DEFAULT_RELAY_URL


class RelayClient:
    """The relay over HTTP (relay_client.py), at one address."""

    def __init__(self, base_url):
        self.base_url = base_url

    def create(self, file, seat, name=None):
        return relay_client.create(self.base_url, file, seat, name)

    def invite(self, code):
        return relay_client.invite(self.base_url, code)

    def join(self, code, name=None, game=None):
        return relay_client.join(self.base_url, code, name, game)

    def read(self, code, token, since, file=False):
        return relay_client.read(self.base_url, code, token, since, file)

    def append(self, code, token, base, events):
        return relay_client.append(self.base_url, code, token, base, events)

    def close(self, code, token):
        return relay_client.close(self.base_url, code, token)


def relay():
    return current_app.config.get('RELAY') or RelayClient(relay_url())


def player_name():
    """The name this player gives online (Setup page; on a self-hosted copy, their account's), or None."""
    return access.principal().player_name


def clean_code(text):
    """An invite code as typed (any case, spaces or dashes) as the relay writes it."""
    return ''.join(c for c in str(text or '').upper() if c.isalnum())


def game_data(saved):
    """A saved game as game_file.export_game reads it (a game saved before uids gets one now)."""
    if not saved.uid:
        saved.uid = uuid.uuid4().hex
        db.session.commit()
    return {'uid': saved.uid, 'name': saved.name, 'app_version': saved.app_version or __version__, 'armies': saved.armies,
            'events': saved.events or [], 'battlefield': saved.battlefield, 'setup': bool(saved.setup), 'twist': saved.twist,
            'pending': saved.pending}


# --- The kinds --------------------------------------------------------------------------------

def kind_of(saved):
    if saved.seat_users:
        return SAME_COPY
    if saved.relay_code:
        return RELAY
    return HOT_SEAT if saved.seat is None else GAME_FILE


def connect(saved):
    return {HOT_SEAT: HotSeat, GAME_FILE: GameFile, RELAY: Relay, SAME_COPY: SameCopy}[kind_of(saved)](saved)


class Refused(Exception):
    """A switch the game's connection doesn't allow (the message says why)."""


class Connection:
    kind = None
    online = False  # the other player's moves can arrive while this page is open (the page polls)
    undo_asks = False  # Undo is a Correction the other player accepts (else taken back at once, before dice)
    notifies = False  # turn changes are emailed (mail.py)
    takes_files = True  # game files can bring it up to date

    def __init__(self, saved):
        self.saved = saved

    def seat(self):
        """The side the request's player plays: 0, 1, or None for both."""
        return self.saved.seat

    def plays(self):
        """Whether the request's player plays in this game at all."""
        return True

    def waiting_for_army(self):
        return False

    def fetch(self, forget):
        """Add the other player's new moves (RelayError if they can't be fetched): how many came. `forget(saved)` is
        called when the game changes in a way its event log doesn't show (the other player's army arrived)."""
        return 0

    def publish(self, event):
        """Send an action's event to the other player before it is saved here (RelayError if refused): the
        game's fields to save with it."""
        return {}

    def status(self):
        """What the page is told about the connection."""
        return {'kind': self.kind, 'online': self.online, 'joined': True, 'names': [None, None], 'code': None}

    # Switching kind
    def choose_seat(self, seat):
        self.saved.seat = seat

    def go_online(self):
        raise Refused('Choose which player this PC plays first: online, each PC plays one side.')

    def new_code(self):
        raise Refused("This game isn't online.")


class HotSeat(Connection):
    kind = HOT_SEAT


class GameFile(Connection):
    kind = GAME_FILE

    def go_online(self):
        """Put the game on the relay under a new invite code (RelayError if it can't): it becomes a Relay game."""
        put_online(self.saved)


class Relay(Connection):
    kind = RELAY
    online = True
    undo_asks = True
    takes_files = False

    def waiting_for_army(self):
        return any(army is None for army in self.saved.armies)

    def fetch(self, forget):
        """
        Add the moves the relay has and this copy doesn't. They are checked first (game_file.check_more): moves this
        app can't read are refused (RelayError) and the game is left as it was.
        """
        saved = self.saved
        waiting = self.waiting_for_army()
        answer = relay().read(saved.relay_code, saved.relay_token, saved.relay_count or 0, file=waiting)
        if waiting and answer.get('joined'):
            take_completed(saved, answer.get('game'))
            forget(saved)
        saved.relay_joined = answer.get('joined')
        saved.relay_names = answer.get('names') or saved.relay_names
        if game_file.newer(answer.get('app_version'), saved.app_version) and game_file.plausible_version(answer['app_version'], __version__):
            saved.app_version = answer['app_version']
        new = answer.get('events') or []
        if new:
            mine = (saved.events or [])[:saved.relay_count or 0]
            try:
                game_file.check_more(dict(game_data(saved), events=mine), new)
            except game_file.GameFileError as error:
                db.session.commit()
                raise relay_client.RelayError(f"The other player's latest moves can't be read here, so they weren't added: {error}", 422)
            saved.events = mine + new
            saved.relay_count = answer['count']
        db.session.commit()
        return len(new)

    def publish(self, event):
        saved = self.saved
        count = relay().append(saved.relay_code, saved.relay_token, saved.relay_count or 0, [event])['count']
        return {'relay_count': count}  # saved with the event (session._save); not set here, or it would be saved first

    def status(self):
        saved = self.saved
        return {'kind': self.kind, 'online': True, 'joined': bool(saved.relay_joined),
                'names': saved.relay_names or [None, None], 'code': saved.relay_code}

    def choose_seat(self, seat):
        raise Refused("This game is played online: its seats are the relay's.")

    def go_online(self):
        pass  # already

    def new_code(self):
        """
        Move the game to a new invite code: the relay's copy under the old one is deleted, so whoever holds it
        (someone who took the code before the other player could) can't carry on. The other player's moves are
        fetched first by the caller, so none is lost.
        """
        old_code, old_token = self.saved.relay_code, self.saved.relay_token
        put_online(self.saved)
        db.session.commit()
        try:
            relay().close(old_code, old_token)
        except relay_client.RelayError:
            pass  # It expires on its own; the new code works either way


class SameCopy(Connection):
    kind = SAME_COPY
    online = True
    undo_asks = True
    notifies = True
    takes_files = False

    def seat(self):
        user = access.viewer()
        return self.saved.seat_users.index(user.id) if user and user.id in self.saved.seat_users else None

    def plays(self):
        return self.seat() is not None

    def status(self):
        names = {u.id: u.name for u in User.query.filter(User.id.in_(self.saved.seat_users))}
        return {'kind': self.kind, 'online': True, 'joined': True, 'names': [names.get(i) for i in self.saved.seat_users],
                'code': None}

    def choose_seat(self, seat):
        raise Refused('Each player of this game plays their own side.')

    def go_online(self):
        raise Refused('Both players are on this copy: the game needs no relay.')


# --- Relay games joining --------------------------------------------------------------------

def put_online(saved):
    """Put a game this app plays one side of on the relay under a new invite code (RelayError if it can't)."""
    answer = relay().create(game_file.export_game(game_data(saved), saved.seat), saved.seat, player_name())
    names = [None, None]
    names[saved.seat] = player_name()
    saved.relay_code, saved.relay_token = answer['code'], answer['token']
    saved.relay_count, saved.relay_joined, saved.relay_names = len(saved.events or []), False, names


def take_completed(saved, file):
    """The game the other player's app completed with their army (on joining), checked: everything as before but
    their army and the choices that waited for it (RelayError if it isn't)."""
    try:
        game = dict(file or {}, events=[])
        if not isinstance(game.get('armies'), list) or len(game['armies']) != 2 or game['armies'][saved.seat] != saved.armies[saved.seat] \
                or game.get('uid') != saved.uid:
            raise game_file.GameFileError('it isn\'t the game you started, with your army')
        game['setup'] = bool(game.get('setup'))
        game_file.check_game(game, __version__)
    except game_file.GameFileError as error:
        raise relay_client.RelayError(f"The other player's army can't be read here, so the game can't start: {error}", 422)
    saved.armies, saved.battlefield, saved.twist, saved.setup = game['armies'], game['battlefield'], game['twist'], game['setup']
    saved.name, saved.pending = game['name'], None
