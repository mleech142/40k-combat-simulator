"""
Play mode's page and API (/play, /api/play/...). A game is stored as its frozen armies and event
log (SavedGame); its state is always their replay, so a game reads the same however it was made.
"""

import random

from flask import current_app, jsonify, render_template, request

from .. import armies
from ..models import SavedArmy, SavedBattlefield, SavedGame, db
from . import battlefields, mission_state, missions, tactical
from .dice import Dice
from ..settings import update_settings
from .game import (MANUAL_EFFECTS, MARKER_MM, OBJECTIVE_RANGE, ActionError, allocation_groups, apply_event, can_fly, charge_options,
                   deployment_zone, fight_options, move_characteristic, objective_control, objectives_in_range, outline, ready_units, replay, resolve_action,
                   score, shooting_options, terrain_areas)
from .geometry import BOARD_DEPTH, BOARD_WIDTH, FOOTPRINTS, footprint_corners
from .snapshot import freeze_army


def game_response(saved, state=None):
    """A game with its state (`state`, else replayed), and what the page needs to draw it: the board and each part's outline."""
    if state is None:
        state = replay(saved.armies, saved.events or [], saved.battlefield, bool(saved.setup), saved.twist)
    return jsonify({'id': saved.id, 'name': saved.name, 'events': saved.events or [], 'state': state,
                    'board': {'width': BOARD_WIDTH, 'depth': BOARD_DEPTH},
                    'zones': [deployment_zone(state, p) for p in (0, 1)],
                    'terrain': [{**piece, 'points': [list(pt) for pt in shape.points]} for piece, shape in terrain_areas(state)],
                    'outlines': {u['id']: [outline(p) for p in u['parts']] for u in state['units']},
                    'moves': {u['id']: [move_characteristic(p) for p in u['parts']] for u in state['units']},
                    'fly': [u['id'] for u in state['units'] if can_fly(u)],
                    'allocation': {u['id']: allocation_groups(u) for u in state['units'] if len(allocation_groups(u)) > 1},
                    'score': score(state),
                    'ready': ready_units(state),
                    'objective_oc': {o['id']: objective_control(state, o) for o in state['objectives']},
                    'unit_objectives': {u['id']: objectives_in_range(state, u) for u in state['units']},
                    'objective_size': {'marker_mm': MARKER_MM, 'range': OBJECTIVE_RANGE},
                    'manual_effects': [{'key': k, 'name': n, 'side': side} for k, (n, side, _) in MANUAL_EFFECTS.items()],
                    'mission': mission_response(state), 'twist': twist_response(state)})


def mission_response(state):
    """The Mission panel's data: both primaries and each player's secondaries (just 'chosen' until both
    have chosen) with their lines and timings, what each line would score now, VP and the caps, the Fixed
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
    return {'primaries': primaries, 'secondaries': secondaries, 'now': now,
            'secondary_options': [described(missions.secondary(k)) for k in missions.SECONDARIES],
            'vp': [dict(mission_state.new_vp(), **(p.get('vp') or {})) for p in state['players']],
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


def pick_twist(asked, missions_on, setup):
    """(the game's Twist {'key', 'primary'} or None, an error or None) from the new-game form's choice: a
    twist's key or 'random', with Mirrored World's primary (one of its five, or 'roll')."""
    if not asked:
        return None, None
    key, primary = (asked.get('key'), asked.get('primary')) if isinstance(asked, dict) else (asked, None)
    usable = [k for k, (_, _, needs) in missions.TWISTS.items() if missions_on or not needs]
    if key == 'random':
        key = random.choice(usable)
        primary = 'roll' if key == 'mirrored-world' else None
    if key not in missions.TWISTS:
        return None, 'Choose a twist, or none.'
    if key not in usable:
        return None, f'{missions.TWISTS[key][0]} needs both armies to have a force disposition.'
    if key == 'mirrored-world':
        primary = primary or 'roll'
        if primary not in missions.MIRRORED_PRIMARIES + ['roll']:
            return None, "Choose one of Mirrored World's primary missions, or roll for it."
        if primary == 'roll' and not setup:
            return None, "Mirrored World's roll happens during setup."
        return {'key': key, 'primary': primary}, None
    return {'key': key}, None


def twist_response(state):
    twist = state.get('twist')
    if not twist:
        return None
    name, text, _ = missions.TWISTS[twist['key']]
    return dict(twist, name=name, text=text)


def pairing_layouts(first, second):
    """The Event Companion layouts (A, B, C) for a pair of force dispositions (keys or names), in order."""
    if not (first and second):
        return []
    pair = sorted(missions.BY_NAME.get(d, d) for d in (first, second))
    return sorted((b for b in battlefields.BUILTIN.values() if b['kind'] == 'layout' and sorted(b['dispositions']) == pair),
                  key=lambda b: b['layout'])


def find_battlefield(key):
    """A battlefield by its key ('builtin:<id>' or 'saved:<id>'), with its key and source, or None."""
    kind, _, ident = str(key).partition(':')
    if kind == 'builtin' and ident in battlefields.BUILTIN:
        return dict(battlefields.BUILTIN[ident], key=key)
    if kind == 'saved' and ident.isdigit():
        saved = db.session.get(SavedBattlefield, int(ident))
        if saved is not None:
            return dict(saved.data, name=saved.name, key=key, id=key, source='custom')
    return None


def register_play_routes(app):

    @app.route('/play')
    def play_page():
        return render_template('play.html')

    @app.route('/api/play/setting', methods=['POST'])
    def api_play_setting():
        """Show or hide Play in the menu (Setup page)."""
        enabled = bool((request.get_json(silent=True) or {}).get('enabled'))
        update_settings(current_app.config['SETTINGS_JSON'], show_play=True if enabled else None)
        return jsonify({'enabled': enabled})

    @app.route('/api/play/games')
    def api_list_games():
        games = SavedGame.query.order_by(SavedGame.updated_at.desc(), SavedGame.id.desc()).all()
        return jsonify([{'id': g.id, 'name': g.name, 'players': [a['name'] for a in g.armies],
                         'updated_at': (g.updated_at or g.created_at).isoformat()} for g in games])

    @app.route('/api/play/games', methods=['POST'])
    def api_create_game():
        data = request.get_json(silent=True) or {}
        army_ids = data.get('armies')
        if not isinstance(army_ids, list) or len(army_ids) != 2:
            return jsonify({'error': 'Choose two saved armies.'}), 400
        frozen = []
        for army_id in army_ids:
            try:
                saved = db.session.get(SavedArmy, int(army_id))
            except (TypeError, ValueError):
                saved = None
            if saved is None:
                return jsonify({'error': 'That army is no longer saved.'}), 400
            try:
                frozen.append(freeze_army(saved.to_dict()))
            except armies.ArmyError as error:
                return jsonify({'error': str(error)}), 400
        battlefield = None
        if str(data.get('battlefield') or '').startswith('pairing:'):  # The two dispositions' Event Companion layout
            layouts = pairing_layouts(frozen[0].get('disposition'), frozen[1].get('disposition'))
            if not layouts:
                return jsonify({'error': "Both armies need a force disposition to play their pairing's layouts."}), 400
            letter = data['battlefield'].split(':', 1)[1]
            chosen = random.choice(layouts) if letter == 'random' else next((b for b in layouts if b['layout'] == letter), None)
            if chosen is None:
                return jsonify({'error': 'Choose layout A, B or C.'}), 400
            data = dict(data, battlefield=f'builtin:{chosen["id"]}')
        if data.get('battlefield'):
            battlefield = find_battlefield(data['battlefield'])
            if battlefield is None:
                return jsonify({'error': 'That battlefield is no longer saved.'}), 400
        twist, error = pick_twist(data.get('twist'), all(a.get('disposition') for a in frozen), data.get('setup', True) is not False)
        if error:
            return jsonify({'error': error}), 400
        name = str(data.get('name') or '').strip()[:200] or f'{frozen[0]["name"]} vs {frozen[1]["name"]}'
        saved = SavedGame(name=name, armies=frozen, events=[], battlefield=battlefield, twist=twist,
                          setup=data.get('setup', True) is not False)  # Skipped only when asked
        db.session.add(saved)
        db.session.commit()
        return game_response(saved)

    @app.route('/api/play/twists')
    def api_twists():
        """The Twist cards (for the new-game form) and Mirrored World's primaries."""
        return jsonify({'twists': [{'key': k, 'name': n, 'text': t, 'needs_missions': needs} for k, (n, t, needs) in missions.TWISTS.items()],
                        'mirrored': [{'key': k, 'name': missions.primary(k)['name']} for k in missions.MIRRORED_PRIMARIES]})

    @app.route('/api/play/matchup')
    def api_matchup():
        """For the new-game form: two saved armies' force dispositions, the primary missions they give, and
        their pairing's Event Companion layouts."""
        found = []
        for army_id in request.args.getlist('armies')[:2]:
            saved = db.session.get(SavedArmy, int(army_id)) if str(army_id).isdigit() else None
            found.append(armies.price_army(saved.to_dict())['disposition'] if saved else None)
        if len(found) != 2:
            return jsonify({'error': 'Choose two saved armies.'}), 400
        keys = [missions.primary_for(found[0], found[1]), missions.primary_for(found[1], found[0])] if all(found) else None
        return jsonify({'dispositions': found,
                        'primaries': [missions.primary(k)['name'] for k in keys] if keys and all(keys) else None,
                        'layouts': [{'key': f'builtin:{b["id"]}', 'name': b['name'], 'layout': b['layout']}
                                    for b in (pairing_layouts(*found) if all(found) else [])]})

    @app.route('/api/play/games/<int:game_id>')
    def api_get_game(game_id):
        return game_response(db.get_or_404(SavedGame, game_id))

    @app.route('/api/play/games/<int:game_id>', methods=['DELETE'])
    def api_delete_game(game_id):
        db.session.delete(db.get_or_404(SavedGame, game_id))
        db.session.commit()
        return jsonify({'deleted': game_id})

    @app.route('/api/play/games/<int:game_id>/actions', methods=['POST'])
    def api_game_action(game_id):
        """Resolve an action (rolling any dice) and add its event to the game."""
        saved = db.get_or_404(SavedGame, game_id)
        action = request.get_json(silent=True)
        if not isinstance(action, dict):
            return jsonify({'error': 'Send the action as a JSON object.'}), 400
        state = replay(saved.armies, saved.events or [], saved.battlefield, bool(saved.setup), saved.twist)
        try:
            event = resolve_action(state, action, Dice())
        except ActionError as error:
            return jsonify({'error': str(error)}), 400
        saved.events = (saved.events or []) + [event]
        db.session.commit()
        return game_response(saved, apply_event(state, event))

    @app.route('/api/play/games/<int:game_id>/undo', methods=['POST'])
    def api_undo(game_id):
        """Take back the last action, if no dice were rolled for it."""
        saved = db.get_or_404(SavedGame, game_id)
        events = saved.events or []
        if not events:
            return jsonify({'error': 'Nothing to undo.'}), 400
        if events[-1]['rolls']:
            return jsonify({'error': "Dice were rolled for the last action: it can't be undone."}), 400
        saved.events = events[:-1]
        db.session.commit()
        return game_response(saved)

    OPTIONS = {'shooting': shooting_options, 'charge': charge_options, 'fight': fight_options,
               'mission-actions': lambda state, unit_id: {'actions': mission_state.action_options(state, unit_id)}}

    @app.route('/api/play/games/<int:game_id>/<any(shooting, charge, fight, "mission-actions"):kind>/<unit_id>')
    def api_options(game_id, kind, unit_id):
        """What a unit can do now in the Shooting, Charge or Fight phase, and why not."""
        saved = db.get_or_404(SavedGame, game_id)
        try:
            return jsonify(OPTIONS[kind](replay(saved.armies, saved.events or [], saved.battlefield, bool(saved.setup), saved.twist), unit_id))
        except ActionError as error:
            return jsonify({'error': str(error)}), 404

    @app.route('/api/play/footprints')
    def api_footprints():
        """Each terrain footprint's corners from its own corner, in inches (geometry.footprint_corners)."""
        return jsonify({name: [list(p) for p in footprint_corners(name)] for name in FOOTPRINTS})

    @app.route('/api/play/battlefields')
    def api_list_battlefields():
        """The built-in deployment maps and Event Companion layouts, then saved battlefields."""
        listed = [{'key': f'builtin:{k}', 'name': b['name'], 'source': 'builtin', 'kind': b['kind'], 'terrain': len(b['terrain']),
                   **({'dispositions': b['dispositions']} if b['kind'] == 'layout' else {})}
                  for k, b in battlefields.BUILTIN.items()]
        listed += [{'key': f'saved:{b.id}', 'name': b.name, 'source': 'custom', 'kind': 'saved', 'terrain': len(b.data.get('terrain') or [])}
                   for b in SavedBattlefield.query.order_by(SavedBattlefield.id)]
        return jsonify(listed)

    @app.route('/api/play/battlefields/<key>')
    def api_get_battlefield(key):
        battlefield = find_battlefield(key)
        return jsonify(battlefield) if battlefield else (jsonify({'error': 'No such battlefield.'}), 404)

    @app.route('/api/play/battlefields', methods=['POST'])
    def api_save_battlefield():
        """Save a new battlefield (also how one is imported from a file)."""
        try:
            data = battlefields.clean(request.get_json(silent=True))
        except battlefields.BattlefieldError as error:
            return jsonify({'error': str(error)}), 400
        saved = SavedBattlefield(name=data['name'], data=data)
        db.session.add(saved)
        db.session.commit()
        return jsonify(find_battlefield(f'saved:{saved.id}'))

    @app.route('/api/play/battlefields/<key>', methods=['PUT'])
    def api_update_battlefield(key):
        if key.startswith('builtin:'):
            return jsonify({'error': 'Built-in maps can\'t be changed: save a copy instead.'}), 403
        battlefield = find_battlefield(key)
        if battlefield is None:
            return jsonify({'error': 'No such battlefield.'}), 404
        try:
            data = battlefields.clean(request.get_json(silent=True))
        except battlefields.BattlefieldError as error:
            return jsonify({'error': str(error)}), 400
        saved = db.session.get(SavedBattlefield, int(key.split(':')[1]))
        saved.name, saved.data = data['name'], data
        db.session.commit()
        return jsonify(find_battlefield(key))

    @app.route('/api/play/battlefields/<key>', methods=['DELETE'])
    def api_delete_battlefield(key):
        if not key.startswith('saved:') or find_battlefield(key) is None:
            return jsonify({'error': 'No such saved battlefield.'}), 404
        db.session.delete(db.session.get(SavedBattlefield, int(key.split(':')[1])))
        db.session.commit()
        return jsonify({'deleted': key})
