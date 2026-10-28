"""
Play mode's page and API (/play, /api/play/...). A game is stored as its frozen armies and event
log (SavedGame); its state is always their replay, so a game reads the same however it was made.
"""

import json
import os
import random
import re
import uuid

from flask import Response, current_app, jsonify, render_template, request

from .. import __version__, armies
from ..match_view import split_abilities
from ..models import SavedArmy, SavedBattlefield, SavedGame, Unit, db
from . import battlefields, game_file, mission_state, missions, relay_client, tactical
from .dice import Dice
from .seats import actor, check_seat, waiting_on
from ..settings import load_settings, update_settings
from .game import (MANUAL_EFFECTS, MARKER_MM, OBJECTIVE_RANGE, ActionError, allocation_groups, apply_event, can_fly, charge_options,
                   deployment_zone, fight_options, measure, move_characteristic, objective_control, objectives_in_range, outline, ready_units, replay, resolve_action,
                   ingress_rules, redo_history, rerollable, score, shooting_options, terrain_areas, window_options)
from .geometry import BOARD_DEPTH, BOARD_WIDTH, FOOTPRINTS, footprint_corners
from .snapshot import freeze_army, rule_texts


def waiting_for_army(saved):
    """An online game whose other player hasn't joined with their army yet."""
    return any(army is None for army in saved.armies)


def online_response(saved):
    return {'code': saved.relay_code, 'joined': bool(saved.relay_joined),
            'names': saved.relay_names or [None, None]} if saved.relay_code else None


def game_response(saved, state=None):
    """A game with its state (`state`, else replayed), and what the page needs to draw it: the board and each part's outline.
    A game still waiting for the other player's army has no state: it says so ('waiting_for_army')."""
    if waiting_for_army(saved):
        return jsonify({'id': saved.id, 'name': saved.name, 'events': [], 'state': None, 'waiting_for_army': True,
                        'uid': saved.uid, 'seat': saved.seat, 'players': [a and a['name'] for a in saved.armies],
                        'online': online_response(saved), 'app_version': saved.app_version, 'needs_update': needs_update(saved),
                        'options': pending_summary(saved.pending)})
    if state is None:
        state = replay(saved.armies, saved.events or [], saved.battlefield, bool(saved.setup), saved.twist)
    return jsonify({'id': saved.id, 'name': saved.name, 'events': saved.events or [], 'state': state,
                    'uid': saved.uid, 'seat': saved.seat, 'waiting_on': waiting_on(state),
                    'online': online_response(saved),
                    'app_version': saved.app_version, 'needs_update': needs_update(saved),
                    'board': {'width': BOARD_WIDTH, 'depth': BOARD_DEPTH},
                    'zones': [deployment_zone(state, p) for p in (0, 1)],
                    'terrain': [{**piece, 'points': [list(pt) for pt in shape.points]} for piece, shape in terrain_areas(state)],
                    'outlines': {u['id']: [outline(p) for p in u['parts']] for u in state['units']},
                    'moves': {u['id']: [move_characteristic(p) for p in u['parts']] for u in state['units']},
                    'fly': [u['id'] for u in state['units'] if can_fly(u)],
                    'allocation': {u['id']: allocation_groups(u) for u in state['units'] if len(allocation_groups(u)) > 1},
                    'score': score(state),
                    'ready': ready_units(state),
                    'reaction': window_options(state),
                    'ingress': [ingress_rules(state, p) for p in (0, 1)],
                    'rerollable': rerollable(state, (saved.events or [None])[-1]),
                    'objective_oc': {o['id']: objective_control(state, o) for o in state['objectives']},
                    'unit_objectives': {u['id']: objectives_in_range(state, u) for u in state['units']},
                    'objective_size': {'marker_mm': MARKER_MM, 'range': OBJECTIVE_RANGE},
                    'manual_effects': [{'key': k, 'name': n, 'side': side} for k, (n, side, _) in MANUAL_EFFECTS.items()],
                    'mission': mission_response(state), 'twist': twist_response(state),
                    'texts': rule_texts(saved.armies)})


def needs_update(saved):
    """Whether the game has been played on a newer version of the app than this one (then this one can't play it)."""
    return game_file.newer(saved.app_version, __version__)


def update_message(saved):
    return (f'This game has been played on version {saved.app_version} of the app, and this is version {__version__}: '
            'update the app to carry on playing it.')


def game_data(saved):
    """A saved game as game_file.export_game reads it (a game saved before uids gets one now)."""
    if not saved.uid:
        saved.uid = uuid.uuid4().hex
        db.session.commit()
    return {'uid': saved.uid, 'name': saved.name, 'app_version': saved.app_version or __version__, 'armies': saved.armies,
            'events': saved.events or [], 'battlefield': saved.battlefield, 'setup': bool(saved.setup), 'twist': saved.twist,
            'pending': saved.pending}


MAX_IMPORT = 10 * 1024 * 1024  # Bytes: the largest game file opened
DEFAULT_RELAY_URL = 'https://tabletop-relay.tabletop-relay.workers.dev'  # The project's own relay (docs/relay.md)


def relay_url():
    """The relay's address: the Setup page's, else RELAY_URL, else the project's own."""
    return load_settings(current_app.config['SETTINGS_JSON']).get('relay_url') or os.environ.get('RELAY_URL') or DEFAULT_RELAY_URL


def clean_code(text):
    """An invite code as typed (any case, spaces or dashes) as the relay writes it."""
    return ''.join(c for c in str(text or '').upper() if c.isalnum())


def pull(saved):
    """
    Add the moves the relay has and this copy doesn't (an online game); the number added. They are checked first
    (game_file.check_more): moves this app can't read are refused (RelayError) and the game is left as it was.
    """
    waiting = waiting_for_army(saved)
    answer = relay_client.read(relay_url(), saved.relay_code, saved.relay_token, saved.relay_count or 0, file=waiting)
    if waiting and answer.get('joined'):
        take_completed(saved, answer.get('game'))
    saved.relay_joined = answer.get('joined')
    saved.relay_names = answer.get('names') or saved.relay_names
    if game_file.newer(answer.get('app_version'), saved.app_version) and game_file.plausible_version(answer['app_version'], __version__):
        saved.app_version = answer['app_version']
    new = answer.get('events') or []
    if new:
        mine = (saved.events or [])[:saved.relay_count or 0]
        try:
            game_file.check_more(dict(game_data(saved), events=mine), new)
        except game_file.GameFileError as error:
            db.session.commit()
            raise relay_client.RelayError(f"The other player's latest moves can't be read here, so they weren't added: {error}", 422)
        saved.events = mine + new
        saved.relay_count = answer['count']
    db.session.commit()
    return len(new)


def take_completed(saved, file):
    """The game the other player's app completed with their army (on joining), checked: everything as before but
    their army and the choices that waited for it (RelayError if it isn't)."""
    try:
        game = dict(file or {}, events=[])
        if not isinstance(game.get('armies'), list) or len(game['armies']) != 2 or game['armies'][saved.seat] != saved.armies[saved.seat] \
                or game.get('uid') != saved.uid:
            raise game_file.GameFileError('it isn\'t the game you started, with your army')
        game['setup'] = bool(game.get('setup'))
        game_file.check_game(game, __version__)
    except game_file.GameFileError as error:
        raise relay_client.RelayError(f"The other player's army can't be read here, so the game can't start: {error}", 422)
    saved.armies, saved.battlefield, saved.twist, saved.setup = game['armies'], game['battlefield'], game['twist'], game['setup']
    saved.name, saved.pending = game['name'], None


def relay_failure(saved, error):
    """An answer for a relay request that failed: the other player moved first (409, with the game brought up to
    date), the game needs a newer app (409), or the relay couldn't be reached (503)."""
    if error.status == 404:  # Closed: the other player moved the game to a new invite code
        return jsonify({'error': 'This game has a new invite code: ask the other player for it, then join with it '
                                 '(Games, Join an online game) to carry on.'}), 410
    if error.status == 409 or error.status == 426:
        try:
            pull(saved)
        except relay_client.RelayError:
            pass
        message = str(error) if error.status == 426 else 'The other player moved first: their moves are on the board now.'
        return jsonify(dict(game_response(saved).get_json(), error=message)), 409
    return jsonify({'error': str(error)}), 503 if error.status is None else 502


ACTION_NAMES = {'shoot': 'shooting', 'move': 'move', 'advance': 'Advance roll', 'charge': 'charge roll',
                'charge_move': 'charge move', 'fight': 'fight', 'pile_in': 'pile in', 'consolidate': 'consolidation',
                'next_phase': 'next phase', 'end_deployment': 'end of deployment', 'place': 'placing a unit',
                'stratagem': 'stratagem', 'rule': 'rule', 'effect': 'effect', 'stationary': 'Remain stationary',
                'reset_move': 'resetting a move', 'tactical': 'tactical card', 'start_action': 'action'}


def undo_rewind(saved, state, action):
    """For accepting an Undo request: the game replayed to just before the action being taken back, else None."""
    pending = state.get('pending') or {}
    if action.get('type') != 'accept' or not pending.get('undo'):
        return None
    target = pending['undo']['event']
    events = saved.events or []
    if not 0 <= target < len(events):
        return None
    return replay(saved.armies, events[:target], saved.battlefield, bool(saved.setup), saved.twist)


def redo_for(saved, action):
    """For a Command Re-roll: what it works out again (game.redo_history), else None."""
    if action.get('type') != 'command_reroll':
        return None
    events = saved.events or []
    return redo_history(events, lambda n: replay(saved.armies, events[:n], saved.battlefield, bool(saved.setup), saved.twist))


def player_name():
    """The name this player gives online (Setup page), or None."""
    return load_settings(current_app.config['SETTINGS_JSON']).get('player_name')


def put_online(saved):
    """Put a game this app plays one side of on the relay under a new invite code (RelayError if it can't)."""
    answer = relay_client.create(relay_url(), game_file.export_game(game_data(saved), saved.seat), saved.seat, player_name())
    names = [None, None]
    names[saved.seat] = player_name()
    saved.relay_code, saved.relay_token = answer['code'], answer['token']
    saved.relay_count, saved.relay_joined, saved.relay_names = len(saved.events or []), False, names


def read_seat(value):
    """A seat from a request: 0 or 1, or None (hot-seat); anything else is a ValueError."""
    if value is None or value == '':
        return None
    seat = int(value)
    if seat not in (0, 1):
        raise ValueError(seat)
    return seat


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


def finish_setup(frozen, options):
    """
    The choices that need both armies, from the new-game form's (or, for a game the other player joined online,
    the creator's): {'battlefield' (a key, 'pairing:<A|B|C|random>' or a battlefield), 'twist', 'setup', 'name'}.
    Answers ({'battlefield', 'twist', 'name'}, None) or (None, an error).
    """
    battlefield = options.get('battlefield')
    if isinstance(battlefield, str) and battlefield.startswith('pairing:'):  # The two dispositions' Event Companion layout
        layouts = pairing_layouts(frozen[0].get('disposition'), frozen[1].get('disposition'))
        if not layouts:
            return None, "Both armies need a force disposition to play their pairing's layouts."
        letter = battlefield.split(':', 1)[1]
        chosen = random.choice(layouts) if letter == 'random' else next((b for b in layouts if b['layout'] == letter), None)
        if chosen is None:
            return None, 'Choose layout A, B or C.'
        battlefield = f'builtin:{chosen["id"]}'
    if isinstance(battlefield, str) and battlefield:
        battlefield = find_battlefield(battlefield)
        if battlefield is None:
            return None, 'That battlefield is no longer saved.'
    twist, error = pick_twist(options.get('twist'), all(a.get('disposition') for a in frozen), options.get('setup', True) is not False)
    if error:
        return None, error
    name = str(options.get('name') or '').strip()[:200] or f'{frozen[0]["name"]} vs {frozen[1]["name"]}'
    return {'battlefield': battlefield or None, 'twist': twist, 'name': name}, None


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


def pick_twist(asked, missions_on, setup):
    """(the game's Twist {'key', 'primary'} or None, an error or None) from the new-game form's choice: a
    twist's key or 'random', with Mirrored World's primary (one of its five, or 'roll')."""
    if not asked:
        return None, None
    key, primary = (asked.get('key'), asked.get('primary')) if isinstance(asked, dict) else (asked, None)
    usable = [k for k, (_, _, needs) in missions.TWISTS.items() if missions_on or not needs]
    if key == 'random':
        key = random.choice(usable)
        # Mirrored World's primary is rolled during setup; with no setup it is picked now
        primary = ('roll' if setup else random.choice(missions.MIRRORED_PRIMARIES)) if key == 'mirrored-world' else None
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

    @app.route('/api/play/relay', methods=['GET', 'POST'])
    def api_play_relay():
        """Online play's settings (Setup page): the relay's address ('url'; empty goes back to the project's own) and
        the name you play under ('name', shown to the other player)."""
        if request.method == 'POST':
            data = request.get_json(silent=True) or {}
            changes = {}
            if 'url' in data:
                url = str(data.get('url') or '').strip().rstrip('/')
                if url and not url.startswith(('https://', 'http://127.0.0.1', 'http://localhost')):
                    return jsonify({'error': 'The relay\'s address starts with https://'}), 400
                changes['relay_url'] = url or None
            if 'name' in data:
                changes['player_name'] = ''.join(c for c in str(data.get('name') or '') if c not in '<>"\'`' and ord(c) >= 32).strip()[:40] or None
            update_settings(current_app.config['SETTINGS_JSON'], **changes)
        return jsonify({'url': relay_url(), 'default': DEFAULT_RELAY_URL, 'name': player_name()})

    @app.route('/api/play/games')
    def api_list_games():
        games = SavedGame.query.order_by(SavedGame.updated_at.desc(), SavedGame.id.desc()).all()
        return jsonify([{'id': g.id, 'name': g.name, 'players': [a['name'] if a else 'joining online' for a in g.armies], 'seat': g.seat,
                         'online': bool(g.relay_code),
                         'updated_at': (g.updated_at or g.created_at).isoformat()} for g in games])

    @app.route('/api/play/games', methods=['POST'])
    def api_create_game():
        """
        A new game from two saved armies ({'armies': [id, id]}), or from yours alone with the other player joining
        online with theirs ({'armies': [id, None]} or [None, id], playing the seat whose army is given): it goes
        online at once, and the choices that need both armies wait for theirs (finish_setup).
        """
        data = request.get_json(silent=True) or {}
        army_ids = data.get('armies')
        if not isinstance(army_ids, list) or len(army_ids) != 2 or army_ids.count(None) > 1:
            return jsonify({'error': 'Choose two saved armies, or yours and "joins online".'}), 400
        try:
            seat = read_seat(data.get('seat'))
        except (TypeError, ValueError):
            return jsonify({'error': 'Choose which player this app plays, or both.'}), 400
        open_seat = army_ids.index(None) if None in army_ids else None
        if open_seat is not None and seat != 1 - open_seat:
            return jsonify({'error': 'When the other player joins online with their army, this PC plays the other side.'}), 400
        frozen = []
        for army_id in army_ids:
            if army_id is None:
                frozen.append(None)
                continue
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
        setup = data.get('setup', True) is not False  # Skipped only when asked
        options = {'battlefield': data.get('battlefield') or None, 'twist': data.get('twist'), 'setup': setup,
                   'name': str(data.get('name') or '').strip()[:200] or None}
        if open_seat is not None:
            return open_game(frozen, options, seat)
        chosen, error = finish_setup(frozen, options)
        if error:
            return jsonify({'error': error}), 400
        saved = SavedGame(name=chosen['name'], armies=frozen, events=[], battlefield=chosen['battlefield'], twist=chosen['twist'],
                          uid=uuid.uuid4().hex, seat=seat, app_version=__version__, setup=setup)
        db.session.add(saved)
        db.session.commit()
        return game_response(saved)

    def open_game(frozen, options, seat):
        """A game with your army alone, put online for the other player to join with theirs. Your own saved
        battlefield goes with it now (their PC doesn't have it); a pairing layout waits for both dispositions."""
        battlefield = options['battlefield']
        if isinstance(battlefield, str) and not battlefield.startswith('pairing:'):
            battlefield = find_battlefield(battlefield)
            if battlefield is None:
                return jsonify({'error': 'That battlefield is no longer saved.'}), 400
        _, error = pick_twist(options['twist'], True, options['setup'])  # Checked again once both armies are known
        if error:
            return jsonify({'error': error}), 400
        mine = frozen[seat]['name']
        saved = SavedGame(name=options['name'] or f'{mine} vs (joining online)', armies=frozen, events=[], battlefield=None,
                          twist=None, setup=options['setup'], uid=uuid.uuid4().hex, seat=seat, app_version=__version__,
                          pending=dict(options, battlefield=battlefield))
        try:
            put_online(saved)
        except relay_client.RelayError as error:
            return jsonify({'error': f'{error} The game needs the relay: the other player joins it there.'}), 503 if error.status is None else 502
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
        return perform_action(saved, action)

    def perform_action(saved, action, from_undo=False):
        """Resolve an action on a saved game and log it: the action route's work, and Undo's request online."""
        if needs_update(saved):
            return jsonify({'error': update_message(saved)}), 409
        if waiting_for_army(saved):
            return jsonify({'error': 'The other player hasn\'t joined with their army yet.'}), 409
        state = replay(saved.armies, saved.events or [], saved.battlefield, bool(saved.setup), saved.twist)
        try:
            check_seat(state, action, saved.seat)
        except ActionError as error:
            return jsonify({'error': str(error)}), 403
        if action.get('type') == 'correction' and 'undo' in action and not from_undo:
            return jsonify({'error': 'Press Undo to ask to take back your last action.'}), 400
        if saved.relay_code:  # Online: the other player's moves first, so the action is made on the latest board
            try:
                if pull(saved):
                    return jsonify(dict(game_response(saved).get_json(),
                                        error='The other player has moved: their moves are on the board now.')), 409
            except relay_client.RelayError as error:
                return relay_failure(saved, error)
        try:
            event = resolve_action(state, action, Dice(), rewind=undo_rewind(saved, state, action),
                                   redo=redo_for(saved, action))
        except ActionError as error:
            return jsonify({'error': str(error)}), 400
        event['by'] = actor(state, action)  # Whose app sent it (undo, and the other player's notice)
        if saved.relay_code:
            try:
                saved.relay_count = relay_client.append(relay_url(), saved.relay_code, saved.relay_token, saved.relay_count or 0, [event])['count']
            except relay_client.RelayError as error:
                return relay_failure(saved, error)
        saved.events = (saved.events or []) + [event]
        saved.app_version = __version__
        db.session.commit()
        return game_response(saved, apply_event(state, event))

    @app.route('/api/play/games/<int:game_id>/undo', methods=['POST'])
    def api_undo(game_id):
        """
        Take back the last action. Hot-seat: at once, if no dice were rolled for it. Against someone on another PC:
        ask them (a Correction they accept or decline), dice or not ('requested' in the answer).
        """
        saved = db.get_or_404(SavedGame, game_id)
        events = [] if waiting_for_army(saved) else saved.events or []
        if not events:
            return jsonify({'error': 'Nothing to undo.'}), 400
        if needs_update(saved):
            return jsonify({'error': update_message(saved)}), 409
        by = events[-1].get('by')
        if saved.seat is not None and by is not None and by != saved.seat:
            name = saved.armies[by]['name']
            return jsonify({'error': f"The last action was {name}'s: only they can take it back."}), 403
        if saved.relay_code:
            try:  # The other player's moves first: the last action may not be ours any more
                if pull(saved):
                    return jsonify(dict(game_response(saved).get_json(),
                                        error='The other player has moved: their moves are on the board now.')), 409
            except relay_client.RelayError as error:
                return relay_failure(saved, error)
            last = events[-1]['action'].get('type')
            if last in ('correction', 'accept', 'decline'):
                return jsonify({'error': "A correction can't be undone: propose another with Correct the game."}), 400
            player = saved.seat if saved.seat is not None else by
            rolled = len(events[-1]['rolls'])
            note = f'Undo: take back the last action ({ACTION_NAMES.get(last, last)}' + (
                f', with its {rolled} roll{"s" if rolled != 1 else ""})' if rolled else ')')
            return perform_action(saved, {'type': 'correction', 'player': player, 'undo': {'event': len(events) - 1, 'note': note}},
                                  from_undo=True)
        if events[-1]['rolls']:
            return jsonify({'error': "Dice were rolled for the last action: it can't be undone."}), 400
        saved.events = events[:-1]
        db.session.commit()
        return game_response(saved)

    @app.route('/api/play/games/<int:game_id>/seat', methods=['PUT'])
    def api_game_seat(game_id):
        """Which player this app plays in the game: 0, 1 or None (both, hot-seat)."""
        saved = db.get_or_404(SavedGame, game_id)
        if saved.relay_code:
            return jsonify({'error': 'This game is played online: its seats are the relay\'s.'}), 409
        try:
            saved.seat = read_seat((request.get_json(silent=True) or {}).get('seat'))
        except (TypeError, ValueError):
            return jsonify({'error': 'Choose player 1, player 2 or both.'}), 400
        db.session.commit()
        return game_response(saved)

    @app.route('/api/play/games/<int:game_id>/file')
    def api_game_file(game_id):
        """The game as a file to send to the other player (game_file.py)."""
        saved = db.get_or_404(SavedGame, game_id)
        if waiting_for_army(saved):
            return jsonify({'error': 'The other player joins this game online, with its invite code.'}), 409
        body = json.dumps(game_file.export_game(game_data(saved), saved.seat))
        filename = re.sub(r'[^\w\- ]+', '', saved.name).strip() or 'game'
        return Response(body, mimetype='application/json',
                        headers={'Content-Disposition': f'attachment; filename="{filename}.json"'})

    @app.route('/api/play/games/import', methods=['POST'])
    def api_import_game():
        """
        Open a game file ({'file', 'seat', 'replace'}). A game this app hasn't seen is added, playing the seat
        the sender doesn't (else 'seat'); one it has is brought up to date when the file carries on from it.
        Answers the game with 'imported': 'new', 'newer', 'same', 'older' or 'replaced'; copies that went
        different ways are refused (409, 'conflict') unless 'replace' says to take the file's.
        """
        if (request.content_length or 0) > MAX_IMPORT:
            return jsonify({'error': 'That file is too big to be a game.'}), 400
        data = request.get_json(silent=True) or {}
        try:
            game = game_file.read_game(data.get('file'), __version__)
        except game_file.GameFileError as error:
            return jsonify({'error': str(error)}), 400
        saved = SavedGame.query.filter_by(uid=game['uid']).first()
        if saved is None:
            try:
                seat = read_seat(data['seat']) if 'seat' in data else (None if game['sent_by'] is None else 1 - game['sent_by'])
            except (TypeError, ValueError):
                return jsonify({'error': 'Choose player 1, player 2 or both.'}), 400
            if 'seat' not in data and seat is None:
                return jsonify({'error': 'Choose which seat this app plays: the file comes from a game played on one PC.',
                                'players': [a['name'] for a in game['armies']]}), 400
            saved = SavedGame(name=game['name'], armies=game['armies'], events=game['events'], battlefield=game['battlefield'],
                              setup=game['setup'], twist=game['twist'], uid=game['uid'], seat=seat,
                              app_version=game['app_version'] or __version__)
            db.session.add(saved)
            how = 'new'
        elif saved.relay_code:
            return jsonify({'error': 'This game is played online: its moves come from the relay, not from files.'}), 409
        else:
            how = game_file.compare(saved.events or [], game['events'])
            if how == 'conflict' and not data.get('replace'):
                return jsonify({'error': 'Your copy of this game and the file went different ways: each has moves the other '
                                         "doesn't. Keep yours, or replace it with the file's.", 'conflict': True}), 409
            if how in ('newer', 'conflict'):
                saved.events = game['events']
                how = 'replaced' if how == 'conflict' else how
            if game_file.newer(game['app_version'], saved.app_version):
                saved.app_version = game['app_version']
        db.session.commit()
        return jsonify(dict(game_response(saved).get_json(), imported=how))

    @app.route('/api/play/games/<int:game_id>/online', methods=['POST'])
    def api_game_online(game_id):
        """Put a game this app plays one side of on the relay: answers the game, whose 'online' has the invite code."""
        saved = db.get_or_404(SavedGame, game_id)
        if saved.relay_code:
            return game_response(saved)
        if saved.seat is None:
            return jsonify({'error': 'Choose which player this PC plays first: online, each PC plays one side.'}), 400
        try:
            put_online(saved)
        except relay_client.RelayError as error:
            return jsonify({'error': str(error)}), 503 if error.status is None else 502
        db.session.commit()
        return game_response(saved)

    @app.route('/api/play/games/<int:game_id>/new-code', methods=['POST'])
    def api_game_new_code(game_id):
        """
        Move an online game to a new invite code: the relay's copy under the old one is deleted, so whoever holds
        it (someone who took the code before the other player could) can't carry on. The other player joins again
        with the new code. The other player's moves are fetched first, so none is lost.
        """
        saved = db.get_or_404(SavedGame, game_id)
        if not saved.relay_code:
            return jsonify({'error': 'This game isn\'t online.'}), 400
        old_code, old_token = saved.relay_code, saved.relay_token
        try:
            pull(saved)
        except relay_client.RelayError as error:
            if error.status != 404:  # Already closed: nothing more to fetch
                return relay_failure(saved, error)
        try:
            put_online(saved)
        except relay_client.RelayError as error:
            return jsonify({'error': str(error)}), 503 if error.status is None else 502
        db.session.commit()
        try:
            relay_client.close(relay_url(), old_code, old_token)
        except relay_client.RelayError:
            pass  # It expires on its own; the new code works either way
        return game_response(saved)

    @app.route('/api/play/games/join', methods=['POST'])
    def api_join_game():
        """
        Join an online game with its invite code ({'code'}), playing the side the other player doesn't. A game that
        waits for your army answers {'needs_army', 'opponent', 'options'} until it's sent too ({'code', 'army'}: a
        saved army's ID): this app freezes it, makes the choices that waited for it, and joins with the whole game.
        """
        data = request.get_json(silent=True) or {}
        code = clean_code(data.get('code'))
        if not code:
            return jsonify({'error': 'Enter the invite code the other player sent you.'}), 400
        if SavedGame.query.filter_by(relay_code=code).first():
            return jsonify({'error': 'That game is already on this PC.'}), 409
        completed = None
        try:
            invited = relay_client.invite(relay_url(), code)
            if invited.get('needs_army'):
                seat = invited['seat']
                game_file.check_invite(invited['game'], seat)
                theirs = invited['game']['armies'][1 - seat]
                if data.get('army') is None:
                    return jsonify({'needs_army': True, 'code': code, 'options': pending_summary(invited['game']['pending']),
                                    'opponent': {'army': theirs['name'], 'faction': theirs.get('faction'),
                                                 'disposition': theirs.get('disposition'), 'player': invited['names'][1 - seat]}})
                completed, error = complete_invite(invited['game'], seat, data['army'])
                if error:
                    return jsonify({'error': error}), 400
            answer = relay_client.join(relay_url(), code, player_name(), completed)
            game = game_file.read_game(answer['game'], __version__)
        except relay_client.RelayError as error:
            return jsonify({'error': str(error)}), 503 if error.status is None else 400
        except game_file.GameFileError as error:
            return jsonify({'error': str(error)}), 400
        saved = SavedGame.query.filter_by(uid=game['uid']).first() or SavedGame(uid=game['uid'])
        saved.name, saved.armies, saved.events, saved.battlefield = game['name'], game['armies'], game['events'], game['battlefield']
        saved.setup, saved.twist, saved.seat = game['setup'], game['twist'], answer['seat']
        saved.app_version = game['app_version'] or __version__
        saved.relay_code, saved.relay_token, saved.relay_count, saved.relay_joined = code, answer['token'], len(game['events']), True
        saved.relay_names = answer.get('names') or [None, None]
        db.session.add(saved)
        db.session.commit()
        return game_response(saved)

    @app.route('/api/play/games/<int:game_id>/sync', methods=['POST'])
    def api_sync_game(game_id):
        """An online game brought up to date from the relay ('moved': how many of the other player's moves came)."""
        saved = db.get_or_404(SavedGame, game_id)
        if not saved.relay_code:
            return jsonify(dict(game_response(saved).get_json(), moved=0))
        try:
            moved = pull(saved)
        except relay_client.RelayError as error:
            return relay_failure(saved, error)
        return jsonify(dict(game_response(saved).get_json(), moved=moved))

    def complete_invite(invited, seat, army_id):
        """The invited game completed with your saved army in `seat`, and the choices that waited for it: (its game
        file, None) or (None, an error)."""
        try:
            saved_army = db.session.get(SavedArmy, int(army_id))
        except (TypeError, ValueError):
            saved_army = None
        if saved_army is None:
            return None, 'Choose one of your saved armies.'
        try:
            mine = freeze_army(saved_army.to_dict())
        except armies.ArmyError as error:
            return None, str(error)
        frozen = list(invited['armies'])
        frozen[seat] = mine
        chosen, error = finish_setup(frozen, invited['pending'])
        if error:
            return None, f'{error} (The other player chose this when they started the game: choose another army, or ask them.)'
        version = __version__ if game_file.newer(__version__, invited.get('app_version')) else invited.get('app_version')
        game = {'uid': invited['uid'], 'name': chosen['name'], 'app_version': version, 'armies': frozen, 'events': [],
                'battlefield': chosen['battlefield'], 'setup': bool(invited['pending'].get('setup')), 'twist': chosen['twist']}
        try:
            game_file.check_game(game, __version__)
        except game_file.GameFileError as error:
            return None, str(error)
        return game_file.export_game(game, seat), None

    OPTIONS = {'shooting': shooting_options, 'charge': charge_options, 'fight': fight_options,
               'mission-actions': lambda state, unit_id: {'actions': mission_state.action_options(state, unit_id)}}

    @app.route('/api/play/games/<int:game_id>/<any(shooting, charge, fight, "mission-actions"):kind>/<unit_id>')
    def api_options(game_id, kind, unit_id):
        """What a unit can do now in the Shooting, Charge or Fight phase, and why not."""
        saved = db.get_or_404(SavedGame, game_id)
        if waiting_for_army(saved):
            return jsonify({'error': "The other player hasn't joined with their army yet."}), 409
        try:
            return jsonify(OPTIONS[kind](replay(saved.armies, saved.events or [], saved.battlefield, bool(saved.setup), saved.twist), unit_id))
        except ActionError as error:
            return jsonify({'error': str(error)}), 404

    @app.route('/api/play/abilities')
    def api_abilities():
        """
        A game part's abilities from this PC's data, for the board's hover card (games carry no rule text): by its
        BSData ID (`bsdata`), else its database ID (`unit`). {'found', 'abilities': [{name, text}], 'rules': [names]}.
        """
        unit = None
        if request.args.get('bsdata'):
            unit = Unit.query.filter_by(bsdata_id=request.args['bsdata']).first()
        if unit is None and request.args.get('unit', '').isdigit():
            unit = db.session.get(Unit, int(request.args['unit']))
        if unit is None:
            return jsonify({'found': False, 'abilities': [], 'rules': []})
        abilities, rules = split_abilities(unit.abilities)
        return jsonify({'found': True, 'abilities': abilities, 'rules': rules})

    @app.route('/api/play/measure', methods=['POST'])
    def api_measure():
        """The Measure tool's distance between two ends (game.measure): {'distance'} in inches."""
        body = request.get_json(silent=True) or {}
        try:
            return jsonify({'distance': round(measure(body.get('ends') or []), 2)})
        except ActionError as error:
            return jsonify({'error': str(error)}), 400

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
