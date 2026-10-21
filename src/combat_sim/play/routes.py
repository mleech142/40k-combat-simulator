"""
Play mode's page and API (/play, /api/play/...). A game is stored as its frozen armies and event
log (SavedGame); its state is always their replay, so a game reads the same however it was made.
"""

from flask import current_app, jsonify, render_template, request

from .. import armies
from ..models import SavedArmy, SavedGame, db
from .dice import Dice
from ..settings import update_settings
from .game import (DEPLOYMENT_DEPTH, MANUAL_EFFECTS, ActionError, charge_options, fight_options, move_characteristic,
                   outline, ready_units, replay, resolve_action, score, shooting_options)
from .geometry import BOARD_DEPTH, BOARD_WIDTH
from .snapshot import freeze_army


def game_response(saved):
    """A game with its state, and what the page needs to draw it: the board and each part's outline."""
    state = replay(saved.armies, saved.events or [])
    return jsonify({'id': saved.id, 'name': saved.name, 'events': saved.events or [], 'state': state,
                    'board': {'width': BOARD_WIDTH, 'depth': BOARD_DEPTH, 'deployment_depth': DEPLOYMENT_DEPTH},
                    'outlines': {u['id']: [outline(p) for p in u['parts']] for u in state['units']},
                    'moves': {u['id']: [move_characteristic(p) for p in u['parts']] for u in state['units']},
                    'score': score(state),
                    'ready': ready_units(state),
                    'manual_effects': [{'key': k, 'name': n, 'side': side} for k, (n, side, _) in MANUAL_EFFECTS.items()]})


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
        name = str(data.get('name') or '').strip()[:200] or f'{frozen[0]["name"]} vs {frozen[1]["name"]}'
        saved = SavedGame(name=name, armies=frozen, events=[])
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
        state = replay(saved.armies, saved.events or [])
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
            return jsonify(OPTIONS[kind](replay(saved.armies, saved.events or []), unit_id))
        except ActionError as error:
            return jsonify({'error': str(error)}), 404
