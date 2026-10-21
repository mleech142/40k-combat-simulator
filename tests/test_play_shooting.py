"""
Play mode's Shooting phase (src/combat_sim/play/game.py): who can shoot and at what, range and
conditions per model, which models take the wounds, Hazardous, through resolve_action / apply_event.
"""

from combat_sim.play.game import unit_by_id
from test_play_game import army, gun, part
from test_play_turns import act, put, refused

from combat_sim.play.game import new_game

BASE = {'shape': 'round', 'length_mm': 32, 'width_mm': 32}  # 1.26" across


def model(name, models=1, wounds=1, **extra):
    return part(name, models, wounds, base=BASE, **extra)


def game(red, blue, player=1, phase='shooting'):
    """Red's units (bottom) and Blue's (top) as given, `player`'s Shooting phase. Units: (part, ...spots)."""
    state = new_game([army('Red', *[(u[0],) for u in red]), army('Blue', *[(u[0],) for u in blue])])
    for owner, units in (('p1', red), ('p2', blue)):
        for spec in units:
            put(state, f'{owner}-{spec[0]["uid"]}', *spec[1:])
    state['turn'] = {'phase': phase, 'round': 1, 'player': player}
    return state


def shoot(unit, target=None, weapons=None):
    action = {'type': 'shoot', 'unit': unit}
    if target:
        action['target'] = target
    if weapons is not None:
        action['weapons'] = weapons
    return action


def alive(state, unit_id):
    return [m['wounds_left'] if m['alive'] else 'dead' for m in unit_by_id(state, unit_id)['models']]


SQUAD = (model('Squad', 3), (10, 10), (11.5, 10), (13, 10))  # Red, Sv3+
BOLTER = gun(attacks='1', skill=3, strength=4, range=24)


def gunner(*weapons, name='Gunner', models=1, spots=((11.5, 20),), **extra):
    return (model(name, models, 3, weapons=list(weapons) or [BOLTER], **extra), *spots)


# --- Who can shoot ----------------------------------------------------------------------------------

def test_only_the_active_player_shoots_in_the_shooting_phase_once_per_unit():
    state = game([SQUAD], [gunner()])
    assert "Blue's turn" in refused(state, shoot('p1-squad', 'p2-gunner'))
    assert 'shooting phase' in refused(game([SQUAD], [gunner()], phase='movement'), shoot('p2-gunner', 'p1-squad'))
    state, _ = act(state, shoot('p2-gunner', 'p1-squad'), 1)  # Misses
    assert unit_by_id(state, 'p2-gunner')['activity'] == {'shot': True}
    assert 'already shot' in refused(state, shoot('p2-gunner', 'p1-squad'), 1)


def test_a_unit_that_fell_back_cant_shoot_and_one_that_advanced_fires_only_assault_weapons():
    state = game([SQUAD], [gunner(BOLTER, gun('Carbine', attacks='1', range=24, abilities=['Assault']))])
    unit_by_id(state, 'p2-gunner')['activity'] = {'moved': 'fall_back'}
    assert 'Fell Back' in refused(state, shoot('p2-gunner', 'p1-squad'))
    unit_by_id(state, 'p2-gunner')['activity'] = {'moved': 'advance', 'advance_roll': 3}
    assert 'Assault' in refused(state, shoot('p2-gunner', 'p1-squad'))  # Both weapons by default
    after, event = act(state, shoot('p2-gunner', 'p1-squad', [{'part': 0, 'weapon': 1}]), 1)
    assert [a['name'] for a in event['attacks']] == ['Carbine']


def test_engaged_units_fire_pistols_at_the_unit_theyre_fighting():
    pistol = gun('Pistol', attacks='1', range=12, abilities=['Pistol'])
    squad = (model('Squad', 3), (10, 18.2), (11.5, 18.2), (13, 18.2))  # 0.54" from the gunner: engaged
    other = (model('Other', 1), (40, 10))
    state = game([squad, other], [gunner(BOLTER, pistol)])
    assert 'Pistol' in refused(state, shoot('p2-gunner', 'p1-squad', [{'part': 0, 'weapon': 0}]))
    assert 'engaged' in refused(state, shoot('p2-gunner', 'p1-other', [{'part': 0, 'weapon': 1}]))
    _, event = act(state, shoot('p2-gunner', 'p1-squad', [{'part': 0, 'weapon': 1}]), 1)
    assert event['attacks'][0]['name'] == 'Pistol'


def test_engaged_monsters_and_vehicles_fire_everything_at_minus_one_to_hit():
    squad = (model('Squad', 3), (10, 18.2), (11.5, 18.2), (13, 18.2))
    tank = gunner(BOLTER, name='Tank', keywords=['Vehicle'])
    _, event = act(game([squad], [tank]), shoot('p2-tank', 'p1-squad'), 1)
    assert event['rolls'][0]['purpose'] == 'hit' and event['rolls'][0]['need'] == 4  # BS3+, -1


def test_units_fighting_your_own_cant_be_shot_unless_monsters_or_vehicles():
    squad = (model('Squad', 3), (10, 10), (11.5, 10), (13, 10))
    blue_brawler = (model('Brawler', 1), (11.5, 11.7))  # In engagement range of Red's Squad
    state = game([squad], [gunner(), blue_brawler])
    assert 'engagement range' in refused(state, shoot('p2-gunner', 'p1-squad'))
    walker = (model('Walker', 3, keywords=['Vehicle']), (10, 10), (11.5, 10), (13, 10))
    act(game([walker], [gunner(), blue_brawler]), shoot('p2-gunner', 'p1-walker'), 1)


# --- Range and conditions, model by model -----------------------------------------------------------

def test_only_models_with_a_target_in_range_fire():
    # Three gunners with 12" guns: the Squad's nearest model is 9.4", 11.4" and 25" away (base to base)
    guns = gunner(gun('Short gun', attacks='1', range=12, qty=3), name='Gunners', models=3,
                  spots=((11.5, 20.5), (11.5, 22.5), (40, 30)))
    state = game([SQUAD], [guns])
    _, event = act(state, shoot('p2-gunners', 'p1-squad'), 1, 1)
    assert sum(a['models'] for a in event['attacks']) == 2
    far = game([SQUAD], [gunner(gun('Short gun', attacks='1', range=12), spots=((40, 30),))])
    assert 'in range' in refused(far, shoot('p2-gunner', 'p1-squad'))


def test_models_within_half_range_roll_separately_for_rapid_fire():
    rapid = gun('Rifle', attacks='1', range=24, abilities=['Rapid Fire 1'], qty=2)
    guns = gunner(rapid, name='Gunners', models=2, spots=((11.5, 20), (11.5, 30)))  # 8.7" and 18.7" away
    _, event = act(game([SQUAD], [guns]), shoot('p2-gunners', 'p1-squad'), 1, 1, 1)
    assert sorted((a['half_range'], a['models'], a['attacks']) for a in event['attacks']) == [(False, 1, 1), (True, 1, 2)]


def test_heavy_weapons_hit_more_easily_when_the_unit_stayed_still():
    heavy = gun('Cannon', attacks='1', skill=4, range=36, abilities=['Heavy'])
    state = game([SQUAD], [gunner(heavy)])
    _, event = act(state, shoot('p2-gunner', 'p1-squad'), 1)
    assert event['rolls'][0]['need'] == 3  # Didn't move: stationary
    unit_by_id(state, 'p2-gunner')['activity'] = {'moved': 'normal'}
    _, event = act(state, shoot('p2-gunner', 'p1-squad'), 1)
    assert event['rolls'][0]['need'] == 4


# --- Which models take the wounds ---------------------------------------------------------------------

def test_casualties_come_from_farthest_from_the_shooter_unless_the_player_chooses_closest():
    # The gunner is level with the Squad's right-hand model, so the left-hand one is farthest
    state = game([SQUAD], [gunner(spots=((13, 20),))])
    after, _ = act(state, shoot('p2-gunner', 'p1-squad'), 3, 4, 1)  # Hit, wound, failed save
    assert alive(after, 'p1-squad') == ['dead', 1, 1]
    state, _ = act(state, {'type': 'orders', 'player': 0, 'casualties': 'closest'})
    assert state['players'][0]['orders'] == {'casualties': 'closest'}
    after, _ = act(state, shoot('p2-gunner', 'p1-squad'), 3, 4, 1)
    assert alive(after, 'p1-squad') == [1, 1, 'dead']


def test_special_weapon_carriers_are_removed_last():
    # The left-hand model carries the squad's one heavy gun; it is farthest, but survives the first casualty
    squad = (model('Squad', 3, weapons=[gun('Heavy gun', qty=1), gun('Rifle', qty=3)]), (10, 10), (11.5, 10), (13, 10))
    after, _ = act(game([squad], [gunner(spots=((13, 20),))]), shoot('p2-gunner', 'p1-squad'), 3, 4, 1)
    assert alive(after, 'p1-squad') == [1, 'dead', 1]


# --- Hazardous ------------------------------------------------------------------------------------------

def test_hazardous_weapons_can_wound_the_model_firing_them():
    hot = gun('Plasma', attacks='1', range=24, abilities=['Hazardous'], qty=2)
    guns = gunner(hot, name='Gunners', models=2, spots=((11.5, 20), (13, 20)))
    # Both miss (1, 1); Hazardous rolls 1 (3 mortal wounds to that model: W3, so it dies) and 4
    after, event = act(game([SQUAD], [guns]), shoot('p2-gunners', 'p1-squad'), 1, 1, 1, 4)
    assert [g['purpose'] for g in event['rolls']][-1] == 'hazardous'
    assert alive(after, 'p2-gunners') == ['dead', 3]


# --- What the page offers ------------------------------------------------------------------------------

def test_shooting_options_say_which_weapons_can_fire_at_which_units_and_why_not():
    from combat_sim.play.game import shooting_options
    short = gun('Short gun', attacks='1', range=6)
    squad_far = (model('Far', 1), (40, 10))
    state = game([SQUAD, squad_far], [gunner(BOLTER, short)])
    options = shooting_options(state, 'p2-gunner')
    assert options['reason'] is None
    bolter, short_gun = options['weapons']
    assert (bolter['name'], bolter['targets']['p1-squad']) == ('Bolter', {'ok': True, 'models': 1, 'reason': None})
    assert bolter['targets']['p1-far']['ok'] is False and 'in range' in bolter['targets']['p1-far']['reason']
    assert short_gun['targets']['p1-squad']['ok'] is False
    unit_by_id(state, 'p2-gunner')['activity'] = {'shot': True}
    assert 'already shot' in shooting_options(state, 'p2-gunner')['reason']
