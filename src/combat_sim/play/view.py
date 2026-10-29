"""
What the Play page draws a game from (architecture review, candidate 8): `board(state, armies, last_event)` is
everything the board needs beside the game's state, worked out next to the engine so the page only reads it: the
board and zones, terrain, outlines and moves, what each unit can do now, the mission and VP against its caps, which
stratagems and rules can be used now (and on which units), and the rules' texts. The routes add the game's own
fields (its connection, whether Undo can be pressed). A game waiting for the other player's army has no state:
`pending_summary` says what it will be.
"""

from .. import contagion
from . import mission_state, missions, tactical
from .game import (MANUAL_EFFECTS, MARKER_MM, OBJECTIVE_RANGE, afflictions, allocation_groups, can_fly, contagion_ranges,
                   deployment_zone, disembark_options, embark_options, grant_options, has_contagion, infiltrators,
                   ingress_rules, move_characteristic, objective_control, objectives_in_range, outline, ready_units,
                   rerollable, score, scouts, stratagem_targets, terrain_areas, usable_now, window_options)
from .geometry import BOARD_DEPTH, BOARD_WIDTH
from .seats import waiting_on
from .snapshot import rule_texts


def board(state, armies, last_event=None):
    """Everything the board needs beside `state` (the frozen `armies` give the rules' texts; `last_event` what can
    be re-rolled)."""
    afflicted = afflictions(state)
    afflicted = afflictions(state)
    return {'waiting_on': waiting_on(state),
            'board': {'width': BOARD_WIDTH, 'depth': BOARD_DEPTH},
            'zones': [deployment_zone(state, p) for p in (0, 1)],
            'terrain': [{**piece, 'points': [list(pt) for pt in shape.points]} for piece, shape in terrain_areas(state)],
            'outlines': {u['id']: [outline(p) for p in u['parts']] for u in state['units']},
            'moves': {u['id']: [move_characteristic(p, afflicted.get(u['id'])) for p in u['parts']]
                      for u in state['units']},
            'fly': [u['id'] for u in state['units'] if can_fly(u)],
            'allocation': {u['id']: allocation_groups(u) for u in state['units'] if len(allocation_groups(u)) > 1},
            'score': score(state),
            'ready': ready_units(state),
            'reaction': window_options(state),
            'ingress': [ingress_rules(state, p) for p in (0, 1)],
            'scouts': {u['id']: scouts(u, state) for u in state['units'] if scouts(u, state)},
            'embark': embark_options(state), 'disembark': disembark_options(state),
            'infiltrators': [u['id'] for u in state['units'] if infiltrators(u)],
            # Death Guard's Nurgle's Gift: Afflicted units, Contagion Ranges, the Plagues; Verminous Haze
            'afflicted': afflicted,
            'contagion': {u['id']: ranges for u in state['units'] if (ranges := contagion_ranges(state, u))},
            'plagues': {'players': [p for p in (0, 1) if has_contagion(state, p)],
                        'choices': [{'key': k, **v} for k, v in contagion.PLAGUES.items()]},
            'grants': [grant_options(state, p) for p in (0, 1)],
            'rerollable': rerollable(state, last_event),
            'objective_oc': {o['id']: objective_control(state, o) for o in state['objectives']},
            'unit_objectives': {u['id']: objectives_in_range(state, u) for u in state['units']},
            'objective_size': {'marker_mm': MARKER_MM, 'range': OBJECTIVE_RANGE},
            'manual_effects': [{'key': k, 'name': n, 'side': side} for k, (n, side, _) in MANUAL_EFFECTS.items()],
            'mission': mission(state), 'twist': twist(state),
            'usable': usable_now(state), 'stratagem_targets': stratagem_targets(state),
            'texts': rule_texts(armies)}


def mission(state):
    """The Mission panel's data: both primaries and each player's secondaries (just 'chosen' until both
    have chosen) with their lines and timings, what each line would score now, VP and the caps (and each player's
    VP against them: 'standing'), the Fixed
    cards to choose from, and Punishment's condemned units and choice; None without missions."""
    mission = state.get('mission')
    if not mission:
        return None

    def described(card):
        return {'key': card['key'], 'name': card['name'],
                'lines': [{'id': entry['id'], 'text': entry['text'], 'timing': missions.timing(entry),
                           'needs': entry['needs'], 'either': entry['either'], 'vp': entry['vp'],
                           'each': missions.scores_each(entry), 'turn_cap': entry['turn_cap']} for entry in card['lines']]}

    # Each player's own force disposition (a twist can give them another disposition's primary)
    primaries = [dict(described(missions.primary(key)),
                      disposition=state['players'][p].get('disposition') or missions.DISPOSITIONS[missions.primary(key)['disposition']])
                 for p, key in enumerate(mission['primaries'])]
    chosen = mission.get('secondaries') or {}
    revealed = all(chosen.get(str(p)) for p in (0, 1))
    secondaries = []
    for player in (0, 1):
        mine = chosen.get(str(player))
        if not mine:
            secondaries.append(None)
        elif mine['mode'] == 'tactical':
            deck = tactical.mine(state, player) or {}
            secondaries.append({'mode': 'tactical', 'chosen': True, 'deck': len(deck.get('deck', [])),
                                'discarded': len(deck.get('discarded', [])), 'redraw_used': deck.get('redraw_used', False),
                                'beacon': deck.get('beacon'), 'tempting': deck.get('tempting'), 'guards': deck.get('guards', {}),
                                'cards': [dict(described(missions.tactical(c['key'])), hold=c['hold'], discard=c['discard'],
                                               redraw=c['redraw'], short=c['key'])
                                          for c in deck.get('active', [])] if revealed else None})
        else:
            secondaries.append({'mode': mine['mode'], 'chosen': True,
                                'cards': [described(missions.secondary(k)) for k in mine['cards']] if revealed else None})
    now = []
    for player, key in enumerate(mission['primaries']):
        context = mission_state.context(state, player)
        keys = [key] + ((chosen.get(str(player)) or {}).get('cards', []) if revealed else [])
        keys += [missions.TACTICAL_PREFIX + k for k in tactical.active_keys(state, player)] if revealed else []
        now.append({line_id: {'vp': vp, 'how': how} for k in keys for line_id, (vp, how) in missions.preview(k, context).items()})
    vp = [dict(mission_state.new_vp(), **(p.get('vp') or {})) for p in state['players']]
    return {'primaries': primaries, 'secondaries': secondaries, 'now': now,
            'standing': [standing(v, state['turn']['round']) for v in vp],
            'secondary_options': [described(missions.secondary(k)) for k in missions.SECONDARIES],
            'vp': vp,
            'caps': {'round': missions.PRIMARY_ROUND_CAP, 'game': missions.PRIMARY_GAME_CAP},
            'secondary_caps': {'round': missions.SECONDARY_ROUND_CAP, 'game': missions.SECONDARY_GAME_CAP,
                               'card': missions.FIXED_CARD_CAP, 'cards': missions.FIXED_CARDS},
            'condemned': mission['condemned'], 'choice': mission['choice'], 'condemn_options': mission_state.condemn_options(state),
            'markers': mission.get('markers', []), 'actions': mission.get('actions', []),
            'beacon_options': tactical._own_units(state, mission['choice']['player'])
            if (mission.get('choice') or {}).get('kind') == 'beacon' else None,
            'tempting_options': tactical.tempting_options(state, mission['choice']['for'])
            if (mission.get('choice') or {}).get('kind') == 'tempting' else None,
            'locate_options': mission_state.locate_options(state, mission['choice']['player'])
            if (mission.get('choice') or {}).get('kind') == 'locate' else None}

def standing(vp, round_number):
    """A player's VP ({primary, rounds, secondary, secondary_rounds, adjust}) against the caps, for the page:
    {'primary': {total, primary, round, round_cap, game_cap, round_full}, 'secondary': {total, round, round_cap,
    game_cap, card_cap, round_full}}."""
    key = str(round_number)
    primary_round = (vp.get('rounds') or {}).get(key, (vp.get('rounds') or {}).get(round_number, 0)) or 0
    secondary_round = (vp.get('secondary_rounds') or {}).get(key, (vp.get('secondary_rounds') or {}).get(round_number, 0)) or 0
    return {'primary': {'total': vp.get('primary', 0) + (vp.get('adjust') or 0), 'primary': vp.get('primary', 0),
                        'round': primary_round, 'round_cap': missions.PRIMARY_ROUND_CAP,
                        'game_cap': missions.PRIMARY_GAME_CAP, 'round_full': primary_round >= missions.PRIMARY_ROUND_CAP},
            'secondary': {'total': vp.get('secondary') or 0, 'round': secondary_round,
                          'round_cap': missions.SECONDARY_ROUND_CAP, 'game_cap': missions.SECONDARY_GAME_CAP,
                          'card_cap': missions.FIXED_CARD_CAP, 'round_full': secondary_round >= missions.SECONDARY_ROUND_CAP}}


def twist(state):
    """The game's Twist with its name and text, or None."""
    twist = state.get('twist')
    if not twist:
        return None
    name, text, _ = missions.TWISTS[twist['key']]
    return dict(twist, name=name, text=text)


def pending_summary(pending):
    """What a game waiting for the other player's army will be, for the page: {'battlefield', 'twist'} as names."""
    if not pending:
        return None
    battlefield = pending.get('battlefield')
    twist = pending.get('twist')
    twist_key = twist.get('key') if isinstance(twist, dict) else twist
    return {'battlefield': ('Their pairing\'s Event Companion layout' if isinstance(battlefield, str)
                            else (battlefield or {}).get('name') or 'The default map'),
            'twist': 'Random' if twist_key == 'random' else missions.TWISTS[twist_key][0] if twist_key in missions.TWISTS else None}
