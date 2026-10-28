"""
Changes to the shared app data at their own interface (app_data_edits.py): an edit applied and saved, previewed
without changing anything (each column before and after), refused when it can't be made, and proposals made before
edits were stored still approved through their route.
"""

import json

import pytest

from combat_sim import accounts, app_data_edits as edits, paths
from combat_sim.models import Faction, Proposal, RangedWeapon, db
from conftest import BSDATA_FIXTURES, welcome

APP_DATA = {'schema_version': 2, 'source': None, 'factions': [], 'units': [], 'ranged_weapons': [], 'melee_weapons': [],
            'faction_rules': [], 'stratagems': [], 'unit_modifiers': [], 'keywords': [], 'target_profiles': []}
ZAPPER = {'type': 'ranged', 'name': 'Zapper', 'range': 24, 'attacks': '2', 'skill': 3, 'strength': 5, 'ap': -1, 'damage': '1'}


@pytest.fixture
def app(make_client, monkeypatch):
    monkeypatch.setenv(paths.HOSTED_ENV, '')
    client, data_file = make_client(APP_DATA, bsdata=BSDATA_FIXTURES)
    app = client.application
    app.config['DATA_FILE_PATH'] = data_file
    return app


def saved_factions(app):
    return {f['name'] for f in json.loads(app.config['DATA_FILE_PATH'].read_text(encoding='utf-8'))['factions']}


def test_apply_makes_and_saves_the_edit(app):
    with app.test_request_context():
        answer = edits.apply(app, edits.make('faction', 'add', fields={'name': 'Homebrew'}))
        assert answer['name'] == 'Homebrew' and db.session.get(Faction, answer['id']) is not None
    assert 'Homebrew' in saved_factions(app)


def test_preview_reads_every_column_before_and_after_and_changes_nothing(app):
    with app.test_request_context():
        faction = edits.apply(app, edits.make('faction', 'add', fields={'name': 'Homebrew'}))['id']
        weapon = edits.apply(app, edits.make('weapon', 'add', fields=dict(ZAPPER, faction_id=faction)))['id']
        change = edits.make('weapon', 'change', weapon, dict(ZAPPER, faction_id=faction, strength=6, range='30'), 'ranged')
        preview = edits.preview(change)
        assert preview.summary == "Edit ranged weapon 'Zapper' (Homebrew)"
        assert {c['field']: (c['now'], c['proposed']) for c in preview.changes} == {'strength': (5, 6), 'range_inches': (24, 30)}
        assert preview.before['strength'] == 5
        assert db.session.get(RangedWeapon, weapon).strength == 5  # undone


def test_edits_that_cant_be_made_are_refused(app):
    with app.test_request_context():
        bsdata = Faction.query.filter_by(source='bsdata').first()
        for edit, status in ((edits.make('faction', 'change', bsdata.id, {'name': 'Mine'}), 403),  # BSData: read-only
                             (edits.make('faction', 'change', 999_999, {'name': 'Gone'}), 404),
                             (edits.make('faction', 'add', fields={}), 400),
                             (edits.make('unit', 'add', fields={'faction_id': bsdata.id, 'name': 'X'}), 403)):
            with pytest.raises(edits.EditError) as refused:
                edits.preview(edit)
            assert refused.value.status == status
            with pytest.raises(edits.EditError):
                edits.apply(app, edit)
        assert Faction.query.filter_by(name='Mine').count() == 0


def test_an_edit_survives_being_stored(app):
    edit = edits.make('weapon', 'change', 7, {'name': 'x'}, 'ranged')
    assert edits.Edit.from_dict(json.loads(json.dumps(edit.to_dict()))) == edit


def test_a_proposal_saved_before_edits_were_stored_still_approves(make_client, monkeypatch):
    monkeypatch.setenv(paths.HOSTED_ENV, '1')
    accounts._failed.clear()
    host, _ = make_client(APP_DATA)
    welcome(host, {'name': 'Hosty', 'email': 'h@example.com', 'password': 'throwaway-1'})
    faction = host.post('/api/data/factions', json={'name': 'Homebrew'}).get_json()['id']
    with host.application.app_context():  # as 2.8's first proposals were saved: the route and body, no edit
        db.session.add(Proposal(owner_id=1, method='PUT', path=f'/api/data/factions/{faction}', body={'name': 'Brewed'},
                                summary="Edit faction 'Brewed'"))
        db.session.commit()
    proposal = host.get('/api/proposals').get_json()[0]
    assert proposal['changes'] == [{'field': 'name', 'now': 'Homebrew', 'proposed': 'Brewed'}]
    assert host.post(f"/api/proposals/{proposal['id']}/approve").status_code == 200
    assert 'Brewed' in {f['name'] for f in host.get('/api/data/factions').get_json()}
