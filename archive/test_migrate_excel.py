"""
scripts/migrate_excel.py against the real v2 workbook: what it imports, stable IDs, and that it
never overwrites the app's data file by accident. Output always goes to a temporary folder.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WORKBOOK = ROOT / '40KCombatSimulator_v2.xlsx'

pytestmark = [
    pytest.mark.skipif(not WORKBOOK.exists(), reason='v2 workbook not present'),
    pytest.mark.filterwarnings('ignore::UserWarning'),  # openpyxl: unsupported Excel extensions
]


@pytest.fixture(scope='module')
def migrate():
    spec = importlib.util.spec_from_file_location('migrate_excel', ROOT / 'scripts' / 'migrate_excel.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(migrate, monkeypatch, *args):
    monkeypatch.setattr(sys, 'argv', ['migrate_excel.py', '--workbook', str(WORKBOOK), *args])
    return migrate.main()


@pytest.fixture(scope='module')
def built(migrate):
    return migrate.build(WORKBOOK, {}, migrate.Report())


def test_imports_the_workbook(built):
    names = [f['name'] for f in built['factions']]
    assert 'DeathGuard' in names and 'Imperial Guard' in names
    units = {u['name']: u for u in built['units']}
    weapons = {w['id']: w['name'] for w in built['melee_weapons']}
    deathshroud = units['Deathshroud Terminators']
    assert (deathshroud['toughness'], deathshroud['save'], deathshroud['invulnerable_save']) == (7, 2, 4)
    assert deathshroud['points_by_size'] == [{'models': 3, 'points': 150}]
    assert {weapons[e['weapon_id']]: e['qty'] for e in deathshroud['melee_weapons']} == {
        'Manreaper - Strike': 3, 'Manreaper - Sweep': 3}
    assert len([s for s in built['stratagems'] if s['faction_id'] is None]) == 10  # core stratagems
    assert len(built['keywords']) == 51
    assert {t['kind'] for t in built['target_profiles']} == {'generic', 'role'}


def test_weapon_values_are_normalised(built):
    spewer = next(w for w in built['ranged_weapons'] if w['name'] == 'Plague Spewer')
    assert spewer['ballistic_skill'] is None  # "-" in the sheet
    assert spewer['armor_penetration'] == 1  # AP -1 stored positive
    assert spewer['attacks'] == 'D6' and spewer['range_inches'] is None


def test_ids_are_stable_across_rebuilds(migrate, built):
    rebuilt = migrate.build(WORKBOOK, built, migrate.Report())
    for section in ('factions', 'units', 'ranged_weapons', 'melee_weapons', 'stratagems', 'keywords', 'target_profiles'):
        assert [r['id'] for r in rebuilt[section]] == [r['id'] for r in built[section]], section
        assert [r.get('name') for r in rebuilt[section]] == [r.get('name') for r in built[section]], section


def test_refuses_to_overwrite_without_force(migrate, monkeypatch, tmp_path):
    output = tmp_path / 'data.json'
    output.write_text('{"keep": true}', encoding='utf-8')
    assert run(migrate, monkeypatch, '--output', str(output)) == 1
    assert json.loads(output.read_text(encoding='utf-8')) == {'keep': True}


def test_reference_only_keeps_other_data(migrate, monkeypatch, tmp_path):
    output = tmp_path / 'data.json'
    assert run(migrate, monkeypatch, '--output', str(output)) == 0
    data = json.loads(output.read_text(encoding='utf-8'))
    data['units'][0]['name'] = 'Edited in the app'
    data['keywords'] = []
    output.write_text(json.dumps(data), encoding='utf-8')

    assert run(migrate, monkeypatch, '--output', str(output), '--reference-only') == 0
    refreshed = json.loads(output.read_text(encoding='utf-8'))
    assert refreshed['units'][0]['name'] == 'Edited in the app'
    assert len(refreshed['keywords']) == 51
