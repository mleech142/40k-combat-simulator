"""
Play mode's page and API (/play, /api/play/...). A game is stored as its frozen armies and event
log (SavedGame); its state is always their replay, so a game reads the same however it was made.
"""

from flask import current_app, jsonify, render_template, request

from .. import armies
from ..models import SavedArmy, SavedBattlefield, SavedGame, db
from . import battlefields
from .dice import Dice
from ..settings import update_settings
from .game import (MANUAL_EFFECTS, MARKER_MM, OBJECTIVE_RANGE, ActionError, charge_options,
                   deployment_zone, fight_options, move_characteristic, objective_control, outline, ready_units, replay, resolve_action,
                   score, shooting_options, terrain_areas)
from .geometry import BOARD_DEPTH, BOARD_WIDTH
from .snapshot import freeze_army


def game_response(saved):
    """A game with its state, and what the page needs to draw it: the board and each part's outline."""
    state = replay(saved.armies, saved.events or [], saved.battlefield)
    return jsonify({'id': saved.id, 'name': saved.name, 'events': saved.events or [], 'state': state,
                    'board': {'width': BOARD_WIDTH, 'depth': BOARD_DEPTH},
                    'zones': [deployment_zone(state, p) for p in (0, 1)],
                    'terrain': [{**piece, 'points': [list(pt) for pt in shape.points]} for piece, shape in terrain_areas(state)],
                    'outlines': {u['id']: [outline(p) for p in u['parts']] for u in state['units']},
                    'moves': {u['id']: [move_characteristic(p) for p in u['parts']] for u in state['units']},
                    'score': score(state),
                    'ready': ready_units(state),
                    'objective_oc': {o['id']: objective_control(state, o) for o in state['objectives']},
                    'objective_size': {'marker_mm': MARKER_MM, 'range': OBJECTIVE_RANGE},
                    'manual_effects': [{'key': k, 'name': n, 'side': side} for k, (n, side, _) in MANUAL_EFFECTS.items()]})


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
        if data.get('battlefield'):
            battlefield = find_battlefield(data['battlefield'])
            if battlefield is None:
                return jsonify({'error': 'That battlefield is no longer saved.'}), 400
        name = str(data.get('name') or '').strip()[:200] or f'{frozen[0]["name"]} vs {frozen[1]["name"]}'
        saved = SavedGame(name=name, armies=frozen, events=[], battlefield=battlefield)
        db.session.add(saved)
        db.session.commit()
        return game_response(saved)

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
        state = replay(saved.armies, saved.events or [], saved.battlefield)
        try:
            event = resolve_action(state, action, Dice())
        except ActionError as error:
            return jsonify({'error': str(error)}), 400
        saved.events = (saved.events or []) + [event]
        db.session.commit()
        return game_response(saved)

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

    OPTIONS = {'shooting': shooting_options, 'charge': charge_options, 'fight': fight_options}

    @app.route('/api/play/games/<int:game_id>/<any(shooting, charge, fight):kind>/<unit_id>')
    def api_options(game_id, kind, unit_id):
        """What a unit can do now in the Shooting, Charge or Fight phase, and why not."""
        saved = db.get_or_404(SavedGame, game_id)
        try:
            return jsonify(OPTIONS[kind](replay(saved.armies, saved.events or [], saved.battlefield), unit_id))
        except ActionError as error:
            return jsonify({'error': str(error)}), 404

    @app.route('/api/play/battlefields')
    def api_list_battlefields():
        """The built-in deployment maps, then saved battlefields."""
        listed = [{'key': f'builtin:{k}', 'name': b['name'], 'source': 'builtin', 'terrain': len(b['terrain'])}
                  for k, b in battlefields.BUILTIN.items()]
        listed += [{'key': f'saved:{b.id}', 'name': b.name, 'source': 'custom', 'terrain': len(b.data.get('terrain') or [])}
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
