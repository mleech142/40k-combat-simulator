"""
access.py at its own interface: who may read and change each saved record kind, what a player sees listed, the
owner of a new record, the desktop install as its own host, and the start-up checks on route labels and rules.
The routes' end-to-end behaviour is in test_accounts.py, test_shared_play.py and test_proposals.py.
"""

import pytest
from flask import Flask, g
from werkzeug.exceptions import Forbidden, NotFound

from combat_sim import access, paths
from combat_sim.models import (Proposal, SavedArmy, SavedBattlefield, SavedComparison, SavedGame, SavedScenario, User,
                               db)

APP_DATA = {'schema_version': 2, 'source': None, 'factions': [], 'units': [], 'ranged_weapons': [], 'melee_weapons': [],
            'faction_rules': [], 'stratagems': [], 'unit_modifiers': [], 'keywords': [], 'target_profiles': []}


def make_app(make_client, monkeypatch, hosted):
    monkeypatch.setenv(paths.HOSTED_ENV, '1' if hosted else '')
    return make_client(APP_DATA)[0].application


@pytest.fixture
def copy(make_client, monkeypatch):
    """A self-hosted copy with a host, two players (Pat, Sam) and one record of each kind: Pat's, plus a row from
    before the copy had players (the host's) and a game Pat started against Sam."""
    app = make_app(make_client, monkeypatch, hosted=True)
    with app.app_context():
        people = {name: User(name=name, name_key=name.lower(), password_hash='x', is_host=name == 'Host')
                  for name in ('Host', 'Pat', 'Sam')}
        db.session.add_all(people.values())
        db.session.flush()
        pat = people['Pat'].id
        records = {
            'army': SavedArmy(name='Pat list', faction_id=1, units=[], owner_id=pat),
            'old army': SavedArmy(name='Old list', faction_id=1, units=[], owner_id=None),
            'scenario': SavedScenario(name='Pat plan', attacker_config={}, defender_config={}, battle_conditions={}, owner_id=pat),
            'comparison': SavedComparison(name='Pat matrix', attackers=[], defenders=[], battle_conditions={}, owner_id=pat),
            'battlefield': SavedBattlefield(name='Pat table', data={}, owner_id=pat),
            'game': SavedGame(name='Grudge', armies=[], events=[], owner_id=pat, seat_users=[pat, people['Sam'].id]),
            'solo game': SavedGame(name='Practice', armies=[], events=[], owner_id=pat),
            'proposal': Proposal(owner_id=pat, method='POST', path='/api/data/factions', summary='Add faction'),
        }
        db.session.add_all(records.values())
        db.session.commit()
        ids = {key: (type(r), r.id) for key, r in records.items()}
        users = {name: u.id for name, u in people.items()}
    return app, users, ids


def as_player(app, users, name):
    """A request context in which `name` is logged in."""
    ctx = app.test_request_context()
    ctx.push()
    g.user = db.session.get(User, users[name])
    return ctx


# Who may read / change each kind: Pat owns them all; Sam is the other player in "game"
RULES = [
    # record,       Pat (read, write), Sam (read, write), Host (read, write)
    ('army',        (True, True), (True, False), (True, False)),     # shared
    ('old army',    (True, False), (True, False), (True, True)),     # from before the copy had players: the host's
    ('scenario',    (True, True), (False, False), (False, False)),   # private, the host included
    ('comparison',  (True, True), (False, False), (False, False)),
    ('battlefield', (True, True), (False, False), (False, False)),
    ('game',        (True, True), (True, False), (False, False)),    # its players; only the starter deletes it
    ('solo game',   (True, True), (False, False), (False, False)),
    ('proposal',    (True, True), (False, False), (True, False)),    # its author and the host
]


@pytest.mark.parametrize('key,pat,sam,host', RULES, ids=[r[0] for r in RULES])
def test_who_may_read_and_change_each_kind(copy, key, pat, sam, host):
    app, users, ids = copy
    model, ident = ids[key]
    for name, (read, write) in (('Pat', pat), ('Sam', sam), ('Host', host)):
        ctx = as_player(app, users, name)
        try:
            record = db.session.get(model, ident)
            assert (access.can_read(record), access.can_write(record)) == (read, write), name
        finally:
            ctx.pop()


def test_get_says_not_found_or_not_allowed_with_the_reason(copy):
    app, users, ids = copy
    ctx = as_player(app, users, 'Sam')
    try:
        assert access.get(*ids['army']).name == 'Pat list'  # readable
        with pytest.raises(Forbidden) as refused:
            access.get(*ids['army'], write=True)
        assert 'copy it' in refused.value.description
        with pytest.raises(NotFound):
            access.get(*ids['scenario'])  # private: as good as missing
        with pytest.raises(NotFound):
            access.get(SavedArmy, 999_999)
        with pytest.raises(Forbidden) as refused:
            access.get(*ids['game'], write=True)
        assert 'started this game' in refused.value.description
    finally:
        ctx.pop()


def test_listed_shows_what_each_player_can_see(copy):
    app, users, _ = copy
    seen = {}
    for name in ('Pat', 'Sam', 'Host'):
        ctx = as_player(app, users, name)
        try:
            seen[name] = {model.__name__: sorted(r.name if hasattr(r, 'name') else r.summary for r in access.listed(model))
                          for model in (SavedArmy, SavedScenario, SavedGame, Proposal)}
        finally:
            ctx.pop()
    assert seen['Sam'] == {'SavedArmy': ['Old list', 'Pat list'], 'SavedScenario': [], 'SavedGame': ['Grudge'], 'Proposal': []}
    assert seen['Host'] == {'SavedArmy': ['Old list', 'Pat list'], 'SavedScenario': [], 'SavedGame': [],
                            'Proposal': ['Add faction']}
    assert seen['Pat']['SavedGame'] == ['Grudge', 'Practice']


def test_new_records_are_stamped_with_their_player(copy):
    app, users, _ = copy
    ctx = as_player(app, users, 'Sam')
    try:
        assert access.stamp() == users['Sam']
        assert access.owner_of(SavedArmy(owner_id=None)) == users['Host']  # old rows are the host's
    finally:
        ctx.pop()


def test_removed_players_are_labelled(copy):
    app, users, _ = copy
    with app.app_context():
        db.session.get(User, users['Pat']).removed = True
        db.session.commit()
        names = access.owner_names()
    assert names[users['Pat']] == 'Pat (removed)' and names[None] == 'Host'


def test_the_desktop_install_is_its_own_host(make_client, monkeypatch):
    app = make_app(make_client, monkeypatch, hosted=False)
    with app.test_request_context():
        record = SavedScenario(name='x', attacker_config={}, defender_config={}, battle_conditions={}, owner_id=None)
        assert access.is_host() and not access.hosted()
        assert access.can_read(record) and access.can_write(record)
        assert access.stamp() is None


# --- Start-up checks -------------------------------------------------------------------------

def test_a_data_tab_write_without_a_label_stops_the_app():
    app = Flask('check')

    @app.route('/api/data/things', methods=['POST'])
    def add_thing():
        return ''

    with pytest.raises(RuntimeError, match='/api/data/things'):
        access.check(app)


def test_labelled_routes_pass_and_proposable_routes_need_their_edit():
    from combat_sim.app_data_edits import edit_route
    app = Flask('check')

    @app.route('/api/data/factions', methods=['POST'])
    @edit_route('faction', 'add')
    def add_faction():
        return ''

    access.check(app)  # labelled with the edit it makes

    @app.route('/api/data/gizmos', methods=['POST'])
    @access.proposable
    def add_gizmo():
        return ''

    with pytest.raises(RuntimeError, match='gizmos'):
        access.check(app)


def test_every_saved_table_with_an_owner_has_a_rule():
    owned = {m for m in db.Model.__subclasses__() if 'owner_id' in m.__table__.columns}
    assert owned == set(access.POLICY)


def test_route_labels(copy):
    app, _, _ = copy
    label = lambda endpoint: access.route_label(app, endpoint)
    assert label('static') == access.OPEN and label('login_page') == access.OPEN
    assert label('setup_page') == access.HOST and label('api_data_import') == access.HOST
    assert label('api_data_create_faction') == access.PROPOSABLE
    assert label('api_bsdata_browse') == access.DESKTOP
    assert label('api_list_armies') == access.PLAYER  # unlabelled: any logged-in player
