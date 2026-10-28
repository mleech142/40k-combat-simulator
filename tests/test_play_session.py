"""
The game session (play/session.py) and connections (play/connections.py) at their own interfaces, without the web
routes: which kind each game is, acting and the "moved on" check, Undo by kind, the state cache, polling and
turn-change notices. The routes' end-to-end behaviour is in test_play_api.py, test_play_online.py and
test_shared_play.py.
"""

import pytest

from combat_sim.models import SavedGame, db
from combat_sim.play import connections, session as sessions
from combat_sim.play.session import DONE, MOVED_ON, NOT_YOURS, REFUSED, GameSession, Notifier
from conftest import BSDATA_FIXTURES
from test_play_api import APP_DATA, saved_army

ROW = [(10 + 1.5 * i, 6) for i in range(5)]


@pytest.fixture
def app(make_client):
    client = make_client(APP_DATA, bsdata=BSDATA_FIXTURES)[0]
    red = saved_army(client, 'Red', {'uid': 'w', 'unit': 'Void Warriors'}, {'uid': 'c', 'unit': 'Star Captain'})
    blue = saved_army(client, 'Blue', {'uid': 'g', 'unit': 'Gun Crew'})
    game = client.post('/api/play/games', json={'name': 'Practice', 'armies': [red, blue], 'setup': False}).get_json()
    client.application.config['GAME_ID'] = game['id']
    return client.application


def place(unit, spots, state):
    models = [m['id'] for u in state['units'] if u['id'] == unit for m in u['models']]
    return {'type': 'place', 'unit': unit, 'models': [{'id': m, 'x': x, 'y': y, 'angle': 0} for m, (x, y) in zip(models, spots)]}


def game_session(app, notifier=None):
    return GameSession(db.session.get(SavedGame, app.config['GAME_ID']), notifier)


@pytest.mark.parametrize('fields,kind', [
    ({}, connections.HOT_SEAT), ({'seat': 0}, connections.GAME_FILE),
    ({'seat': 0, 'relay_code': 'ABCDEFGHJK'}, connections.RELAY), ({'seat_users': [1, 2]}, connections.SAME_COPY),
])
def test_each_game_is_one_kind_worked_out_from_what_it_stores(fields, kind):
    saved = SavedGame(name='x', armies=[{}, {}], **fields)
    assert connections.kind_of(saved) == kind and connections.connect(saved).kind == kind


def test_how_each_kind_differs():
    hot, file, relay, same = (connections.connect(SavedGame(name='x', armies=[{}, {}], **f)) for f in
                              ({}, {'seat': 1}, {'seat': 0, 'relay_code': 'C'}, {'seat_users': [1, 2]}))
    assert [c.undo_asks for c in (hot, file, relay, same)] == [False, False, True, True]
    assert [c.online for c in (hot, file, relay, same)] == [False, False, True, True]
    assert [c.notifies for c in (hot, file, relay, same)] == [False, False, False, True]
    assert [c.takes_files for c in (hot, file, relay, same)] == [True, True, False, False]
    assert hot.seat() is None and file.seat() == 1
    for connection in (hot, same):
        with pytest.raises(connections.Refused):
            connection.go_online()


def test_acting_saves_the_event_and_the_state_follows(app):
    with app.test_request_context():
        play = game_session(app)
        result = play.act(place('p1-w', ROW, play.state()))
        assert result.kind == DONE and len(play.saved.events) == 1
        assert result.state == game_session(app).state()  # the cached state is the replayed one
        assert play.act({'type': 'next_phase'}).kind in (REFUSED, NOT_YOURS)  # not now: the engine says why


def test_saving_on_an_old_read_of_the_game_is_moved_on(app):
    with app.test_request_context():
        mine = game_session(app)
        read_before = mine.saved.updated_at  # this request read the game...
        theirs = game_session(app)
        assert theirs.act(place('p1-w', ROW, theirs.state())).kind == DONE  # ...then another saved a move
        with pytest.raises(sessions.MovedOn):
            mine._save(read_before, [])
        assert len(db.session.get(SavedGame, mine.saved.id).events) == 1  # nothing lost


def test_hot_seat_undo_takes_back_at_once_until_dice(app):
    with app.test_request_context():
        play = game_session(app)
        play.act(place('p1-w', ROW, play.state()))
        assert play.can_undo()
        assert play.undo().kind == DONE and play.saved.events == []
        assert not play.can_undo() and play.undo().kind == REFUSED  # nothing to undo
        play.act(place('p1-w', ROW, play.state()))
        assert play.state() == game_session(app).state()  # the earlier moves changed: replayed, not reused


def test_polling_with_nothing_new_is_unchanged(app):
    with app.test_request_context():
        play = game_session(app)
        quiet = play.sync(since=0)
        assert quiet.kind == DONE and quiet.extra == {'moved': 0, 'unchanged': True} and quiet.state is None
        assert play.sync(since=None).state is not None  # without `since`, the whole game


def test_turn_changes_are_announced_only_for_same_copy_games(app):
    class Told(Notifier):
        def __init__(self):
            self.moves = []

        def your_move(self, saved, seat):
            self.moves.append(seat)

    with app.test_request_context():
        told = Told()
        play = game_session(app, told)
        play.act(place('p1-w', ROW, play.state()))
        assert told.moves == []  # a hot-seat game emails nobody


def test_the_cache_is_dropped_with_the_game(app):
    with app.test_request_context():
        play = game_session(app)
        play.state()
        assert sessions.cache_key(play.saved) in sessions._cache
        sessions.forget(play.saved)
        assert sessions.cache_key(play.saved) not in sessions._cache
