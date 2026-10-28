"""
A game in progress (architecture review, candidates 2 and 3; decided with the owner): the one module that changes
a saved game's event log. Four calls on a `GameSession(saved, notifier)`:

- `act(action)`: the other player's moves first (the game's connection), the action resolved with dice, sent to
  the other player, then saved;
- `undo()`: take back the last action, as the connection says (at once before dice, or asked as a Correction);
- `sync(since)`: the other player's moves; with `since` (the moves the page has) "unchanged" costs no replay;
- `state()`: the game now, from a cache of recent games (only new moves are replayed).

Each answers a `Result` (done, or why not); play/routes.py turns it into a web answer. Saving checks the game is
still as the request read it (its `updated_at`), so two actions sent together can't both add to the same old log:
the second is answered "moved on" and its page refreshes.

The cache keeps up to CACHE_SIZE games' states by event count and last event; a game whose earlier moves changed
(Undo, a game file replacing them) is replayed in full. Callers must not change a state they're given. With
`app.config['PLAY_STATE_CHECK']` (the tests set it) every cached state is checked against a full replay.
"""

import copy
import hashlib
import json
import threading
from collections import OrderedDict
from datetime import datetime

from flask import current_app
from sqlalchemy import update

from .. import __version__
from ..models import SavedGame, db
from . import game_file, relay_client
from .connections import connect
from .dice import Dice
from .game import ActionError, _apply, apply_event, redo_history, replay, resolve_action
from .seats import actor, check_seat, waiting_on

CACHE_SIZE = 32
DONE, REFUSED, NOT_YOURS, NOT_NOW, MOVED_ON, CLOSED, UNREACHABLE, RELAY_REFUSED = (
    'done', 'refused', 'not_yours', 'not_now', 'moved_on', 'closed', 'unreachable', 'relay_refused')

ACTION_NAMES = {'shoot': 'shooting', 'move': 'move', 'advance': 'Advance roll', 'charge': 'charge roll',
                'charge_move': 'charge move', 'fight': 'fight', 'pile_in': 'pile in', 'consolidate': 'consolidation',
                'next_phase': 'next phase', 'end_deployment': 'end of deployment', 'place': 'placing a unit',
                'stratagem': 'stratagem', 'rule': 'rule', 'effect': 'effect', 'stationary': 'Remain stationary',
                'reset_move': 'resetting a move', 'tactical': 'tactical card', 'start_action': 'action'}

_cache = OrderedDict()  # cache_key(game) -> (event count, last event's hash, state)
_cache_lock = threading.Lock()


class Result:
    """What a session call came to: `kind` (DONE, or why not), a `message` for the player, the game's `state`
    after it (DONE), and `extra` fields for the answer (e.g. 'moved')."""

    def __init__(self, kind, message=None, state=None, **extra):
        self.kind, self.message, self.state, self.extra = kind, message, state, extra

    @property
    def done(self):
        return self.kind == DONE


class MovedOn(Exception):
    """The game changed after this request read it."""


def needs_update(saved):
    """Whether the game has been played on a newer version of the app than this one (then this one can't play it)."""
    return game_file.newer(saved.app_version, __version__)


def update_message(saved):
    return (f'This game has been played on version {saved.app_version} of the app, and this is version {__version__}: '
            'update the app to carry on playing it.')


def _hash(event):
    return hashlib.sha1(json.dumps(event, sort_keys=True, default=str).encode('utf-8')).hexdigest() if event else None


def cache_key(saved):
    """A game's key in the cache: its ID with its own uid and creation time, since a deleted game's ID can be given
    to a new one."""
    return (saved.id, saved.uid, str(saved.created_at))


def forget(saved):
    """Drop a game's cached state (its armies or battlefield changed, or it was replaced)."""
    with _cache_lock:
        _cache.pop(cache_key(saved), None)


def _replay(saved, events=None):
    return replay(saved.armies, saved.events or [] if events is None else events, saved.battlefield, bool(saved.setup), saved.twist)


class Notifier:
    """Tells a player it's their move (mail.py's adapter in play/routes.py); this one tells nobody."""

    def seen(self, saved):
        """The request's player has the game open."""

    def your_move(self, saved, seat):
        """It has just become the move of the player in `seat`."""


class GameSession:
    def __init__(self, saved, notifier=None):
        self.saved = saved
        self.connection = connect(saved)
        self.notifier = notifier or Notifier()

    @property
    def seat(self):
        return self.connection.seat()

    def waiting_for_army(self):
        return self.connection.waiting_for_army()

    # --- The game now -----------------------------------------------------------------------

    def state(self):
        """The game's state now (None while it waits for the other player's army). Don't change it."""
        saved = self.saved
        if self.waiting_for_army():
            return None
        events = saved.events or []
        key = cache_key(saved)
        with _cache_lock:
            cached = _cache.get(key)
            if cached is not None:
                _cache.move_to_end(key)
        state = None
        if cached is not None:
            count, last, cached_state = cached
            if count == len(events) and last == _hash(events[-1] if events else None):
                state = cached_state
            elif count < len(events) and last == _hash(events[count - 1] if count else None):
                state = copy.deepcopy(cached_state)
                for event in copy.deepcopy(events[count:]):
                    _apply(state, event)
        if state is None:
            state = _replay(saved)
        if current_app.config.get('PLAY_STATE_CHECK'):
            assert state == _replay(saved), f'game {saved.id}: the cached state differs from a full replay'
        self._remember(len(events), events[-1] if events else None, state)
        return state

    def _remember(self, count, last, state):
        key = cache_key(self.saved)
        with _cache_lock:
            _cache[key] = (count, _hash(last), state)
            _cache.move_to_end(key)
            while len(_cache) > CACHE_SIZE:
                _cache.popitem(last=False)

    def can_undo(self, state=None):
        """Whether the request's player can press Undo now (the page's button)."""
        events = self.saved.events or []
        if not events or self.waiting_for_army() or not self.connection.plays():
            return False
        state = self.state() if state is None else state
        if state.get('pending'):
            return False
        last, seat = events[-1], self.seat
        if seat is not None and last.get('by') is not None and last['by'] != seat:
            return False
        if self.connection.undo_asks:
            return last['action'].get('type') not in ('correction', 'accept', 'decline')
        return not last['rolls']

    # --- Saving -------------------------------------------------------------------------------

    def _save(self, version, events, **fields):
        """Save the event log (and `fields`) only if the game is still as it was read (`version`: its updated_at)."""
        saved = self.saved
        same = SavedGame.updated_at.is_(None) if version is None else SavedGame.updated_at == version
        done = db.session.execute(update(SavedGame).where(SavedGame.id == saved.id, same)
                                  .values(events=events, updated_at=datetime.utcnow(), **fields))
        if done.rowcount != 1:
            db.session.rollback()
            db.session.refresh(saved)
            raise MovedOn()
        db.session.commit()
        db.session.refresh(saved)

    def relay_failed(self, error):
        """A relay request that failed: the other player moved first (the game brought up to date), the game needs a
        newer app, it moved to a new invite code, or the relay couldn't be reached."""
        if error.status == 404:  # Closed: the other player moved the game to a new invite code
            return Result(CLOSED, 'This game has a new invite code: ask the other player for it, then join with it '
                                  '(Games, Join an online game) to carry on.')
        if error.status in (409, 426):
            try:
                self.connection.fetch(forget)
            except relay_client.RelayError:
                pass
            message = str(error) if error.status == 426 else 'The other player moved first: their moves are on the board now.'
            return Result(MOVED_ON, message)
        return Result(UNREACHABLE if error.status is None else RELAY_REFUSED, str(error))

    # --- Acting -------------------------------------------------------------------------------

    def act(self, action, _undo=False):
        saved = self.saved
        if needs_update(saved):
            return Result(NOT_NOW, update_message(saved))
        if self.waiting_for_army():
            return Result(NOT_NOW, "The other player hasn't joined with their army yet.")
        if not self.connection.plays():
            return Result(NOT_YOURS, "You don't play in this game.")
        if action.get('type') == 'correction' and 'undo' in action and not _undo:
            return Result(REFUSED, 'Press Undo to ask to take back your last action.')
        try:  # The other player's moves first, so the action is made on the latest board
            if self.connection.fetch(forget):
                return Result(MOVED_ON, 'The other player has moved: their moves are on the board now.')
        except relay_client.RelayError as error:
            return self.relay_failed(error)
        version, events = saved.updated_at, list(saved.events or [])
        state = self.state()
        try:
            check_seat(state, action, self.seat)
        except ActionError as error:
            return Result(NOT_YOURS, str(error))
        try:
            event = resolve_action(state, action, Dice(), rewind=self._undo_rewind(state, action), redo=self._redo(action))
        except ActionError as error:
            return Result(REFUSED, str(error))
        event['by'] = actor(state, action)  # Whose app sent it (undo, and the other player's notice)
        try:
            fields = self.connection.publish(event)
        except relay_client.RelayError as error:
            return self.relay_failed(error)
        try:
            self._save(version, events + [event], app_version=__version__, **fields)
        except MovedOn:
            return Result(MOVED_ON, 'The game moved on while you acted: it is up to date now, so try again.')
        after = apply_event(state, event)
        self._remember(len(events) + 1, event, after)
        self.notifier.seen(saved)
        if self.connection.notifies:
            for seat in waiting_on(after):
                if seat not in waiting_on(state) and seat != self.seat:
                    self.notifier.your_move(saved, seat)
        return Result(DONE, state=after)

    def _undo_rewind(self, state, action):
        """For accepting an Undo request: the game replayed to just before the action being taken back, else None."""
        pending = state.get('pending') or {}
        if action.get('type') != 'accept' or not pending.get('undo'):
            return None
        target, events = pending['undo']['event'], self.saved.events or []
        return _replay(self.saved, events[:target]) if 0 <= target < len(events) else None

    def _redo(self, action):
        """For a Command Re-roll: what it works out again (game.redo_history), else None."""
        if action.get('type') != 'command_reroll':
            return None
        events = self.saved.events or []
        return redo_history(events, lambda n: _replay(self.saved, events[:n]))

    def undo(self):
        """
        Take back the last action. Hot-seat and game files: at once, if no dice were rolled for it (game files: your
        own). Against someone else (relay, same copy): ask them, a Correction they accept or decline, dice or not.
        """
        saved = self.saved
        events = [] if self.waiting_for_army() else list(saved.events or [])
        if not events:
            return Result(REFUSED, 'Nothing to undo.')
        if needs_update(saved):
            return Result(NOT_NOW, update_message(saved))
        if not self.connection.plays():
            return Result(NOT_YOURS, "You don't play in this game.")
        by, seat = events[-1].get('by'), self.seat
        if seat is not None and by is not None and by != seat:
            return Result(NOT_YOURS, f"The last action was {saved.armies[by]['name']}'s: only they can take it back.")
        if self.connection.undo_asks:
            try:  # The other player's moves first: the last action may not be ours any more
                if self.connection.fetch(forget):
                    return Result(MOVED_ON, 'The other player has moved: their moves are on the board now.')
            except relay_client.RelayError as error:
                return self.relay_failed(error)
            last = events[-1]['action'].get('type')
            if last in ('correction', 'accept', 'decline'):
                return Result(REFUSED, "A correction can't be undone: propose another with Correct the game.")
            rolled = len(events[-1]['rolls'])
            note = f'Undo: take back the last action ({ACTION_NAMES.get(last, last)}' + (
                f', with its {rolled} roll{"s" if rolled != 1 else ""})' if rolled else ')')
            player = seat if seat is not None else by
            return self.act({'type': 'correction', 'player': player, 'undo': {'event': len(events) - 1, 'note': note}}, _undo=True)
        if events[-1]['rolls']:
            return Result(REFUSED, "Dice were rolled for the last action: it can't be undone.")
        try:
            self._save(saved.updated_at, events[:-1])
        except MovedOn:
            return Result(MOVED_ON, 'The game moved on: it is up to date now.')
        return Result(DONE, state=self.state())

    # --- Syncing ----------------------------------------------------------------------------

    def sync(self, since=None):
        """
        The other player's moves ('moved': how many came). With `since` (the moves the page has), a game with nothing
        new answers 'unchanged' without its state, so polling costs nothing.
        """
        saved = self.saved
        self.notifier.seen(saved)
        moved = 0
        if self.connection.kind == 'relay':
            joined = bool(saved.relay_joined)
            try:
                moved = self.connection.fetch(forget)
            except relay_client.RelayError as error:
                return self.relay_failed(error)
            if bool(saved.relay_joined) != joined:
                since = None  # They've just joined: the page needs the whole game
        elif self.connection.notifies and since is not None:
            moved = max(0, len(saved.events or []) - since)
        if not moved and since == len(saved.events or []) and not self.waiting_for_army():
            return Result(DONE, moved=0, unchanged=True)
        return Result(DONE, state=self.state(), moved=moved)
