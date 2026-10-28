"""
Proposed edits on a self-hosted copy (proposals.py): a player's Data-tab change waits for the host, who sees what
it changes and whether the record changed since, then approves (made like a direct edit) or rejects it.
"""

import json

import pytest

from conftest import welcome
from combat_sim import accounts, paths

APP_DATA = {'schema_version': 2, 'source': None, 'factions': [], 'units': [], 'ranged_weapons': [], 'melee_weapons': [],
            'faction_rules': [], 'stratagems': [], 'unit_modifiers': [], 'keywords': [], 'target_profiles': []}


@pytest.fixture
def copy(make_client, monkeypatch):
    """(host browser, player browser, the app data JSON), with a custom faction 'Homebrew' made by the host."""
    monkeypatch.setenv(paths.HOSTED_ENV, '1')
    accounts._failed.clear()
    host, data_file = make_client(APP_DATA)
    welcome(host, {'name': 'Hosty', 'email': 'h@example.com', 'password': 'throwaway-1'})
    link = host.post('/api/players', json={'name': 'Pat', 'email': 'p@example.com'}).get_json()['link']
    player = host.application.test_client()
    player.post('/api/auth/join', json={'token': link.rsplit('/', 1)[1], 'password': 'throwaway-2'})
    assert host.post('/api/data/factions', json={'name': 'Homebrew'}).status_code == 200  # The host's edits apply at once
    return host, player, data_file


def faction_id(browser, name):
    return next(f['id'] for f in browser.get('/api/data/factions').get_json() if f['name'] == name)


def names(browser):
    return {f['name'] for f in browser.get('/api/data/factions').get_json()}


def saved_names(data_file):
    return {f['name'] for f in json.loads(data_file.read_text(encoding='utf-8'))['factions']}


def test_a_players_edit_waits_for_the_host(copy):
    host, player, data_file = copy
    homebrew = faction_id(host, 'Homebrew')
    resp = player.put(f'/api/data/factions/{homebrew}', json={'name': 'Brewed'})
    assert resp.status_code == 202 and resp.get_json()['proposed'] is True
    assert 'Homebrew' in names(host) and 'Homebrew' in saved_names(data_file)  # Not made yet
    waiting = host.get('/api/proposals').get_json()
    assert len(waiting) == 1
    proposal = waiting[0]
    assert proposal['summary'] == "Edit faction 'Brewed'" and proposal['by'] == 'Pat' and proposal['status'] == 'pending'
    assert proposal['changes'] == [{'field': 'name', 'now': 'Homebrew', 'proposed': 'Brewed'}]
    assert proposal['changed_since'] == []
    # The player sees their own
    assert [p['status'] for p in player.get('/api/proposals').get_json()] == ['pending']
    approved = host.post(f"/api/proposals/{proposal['id']}/approve")
    assert approved.status_code == 200 and approved.get_json()['status'] == 'approved'
    assert 'Brewed' in names(host) and 'Brewed' in saved_names(data_file)  # Made and saved like a direct edit
    assert player.get('/api/proposals').get_json()[0]['status'] == 'approved'


def test_the_host_sees_when_the_record_changed_since_and_decides(copy):
    host, player, _ = copy
    homebrew = faction_id(host, 'Homebrew')
    player.put(f'/api/data/factions/{homebrew}', json={'name': 'Brewed'})
    host.put(f'/api/data/factions/{homebrew}', json={'name': 'Hosts Brew'})
    proposal = host.get('/api/proposals').get_json()[0]
    assert proposal['changed_since'] == [{'field': 'name', 'then': 'Homebrew', 'now': 'Hosts Brew'}]
    assert proposal['changes'] == [{'field': 'name', 'now': 'Hosts Brew', 'proposed': 'Brewed'}]
    assert host.post(f"/api/proposals/{proposal['id']}/approve").status_code == 200  # Still the host's call
    assert 'Brewed' in names(host)
    assert host.post(f"/api/proposals/{proposal['id']}/approve").status_code == 409  # Answered once


def test_new_records_and_deletions_are_proposed_too(copy):
    host, player, _ = copy
    assert player.post('/api/data/factions', json={'name': 'Pats Own'}).status_code == 202
    assert player.delete(f"/api/data/factions/{faction_id(host, 'Homebrew')}").status_code == 202
    summaries = {p['summary']: p['id'] for p in host.get('/api/proposals').get_json()}
    assert set(summaries) == {"Add faction 'Pats Own'", "Delete faction 'Homebrew'"}
    for summary in ("Add faction 'Pats Own'", "Delete faction 'Homebrew'"):
        assert host.post(f'/api/proposals/{summaries[summary]}/approve').status_code == 200
    assert 'Pats Own' in names(host) and 'Homebrew' not in names(host)


def test_rejecting_with_a_reason(copy):
    host, player, _ = copy
    player.post('/api/data/factions', json={'name': 'Silly'})
    proposal = host.get('/api/proposals').get_json()[0]
    rejected = host.post(f"/api/proposals/{proposal['id']}/reject", json={'note': 'Not in our group'}).get_json()
    assert rejected['status'] == 'rejected'
    assert player.get('/api/proposals').get_json()[0]['note'] == 'Not in our group'
    assert 'Silly' not in names(host)


def test_a_proposal_that_cant_be_applied_fails_cleanly(copy):
    host, player, _ = copy
    homebrew = faction_id(host, 'Homebrew')
    player.put(f'/api/data/factions/{homebrew}', json={'name': 'Brewed'})
    host.delete(f'/api/data/factions/{homebrew}')
    proposal = host.get('/api/proposals').get_json()[0]
    assert proposal['gone'] is True
    failed = host.post(f"/api/proposals/{proposal['id']}/approve")
    assert failed.status_code == 400 and failed.get_json()['status'] == 'failed'
    assert player.get('/api/proposals').get_json()[0]['note']


def test_players_withdraw_their_own_and_cant_approve(copy):
    host, player, _ = copy
    player.post('/api/data/factions', json={'name': 'Oops'})
    proposal = player.get('/api/proposals').get_json()[0]
    assert player.post(f"/api/proposals/{proposal['id']}/approve").status_code == 403
    assert player.post(f"/api/proposals/{proposal['id']}/reject").status_code == 403
    assert host.delete(f"/api/proposals/{proposal['id']}").status_code == 403  # Not the host's to withdraw
    assert player.delete(f"/api/proposals/{proposal['id']}").get_json()['status'] == 'withdrawn'
    assert host.post(f"/api/proposals/{proposal['id']}/approve").status_code == 409


def test_importing_app_data_stays_the_hosts(copy):
    _, player, _ = copy
    assert player.post('/api/data/import', json=APP_DATA).status_code == 403
    assert player.get('/data').status_code == 200
    assert 'Import JSON' not in player.get('/data').get_data(as_text=True)


def test_proposals_page_and_badge(copy):
    host, player, _ = copy
    player.post('/api/data/factions', json={'name': 'Badge'})
    page = host.get('/proposals').get_data(as_text=True)
    assert 'nav-badge">1<' in page
    assert player.get('/proposals').status_code == 200
