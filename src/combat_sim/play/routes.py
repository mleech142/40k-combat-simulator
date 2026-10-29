"""
Play mode's page and API (/play, /api/play/...). A game is stored as its frozen armies and event
log (SavedGame); its state is always their replay, so a game reads the same however it was made.

Changing a game (actions, Undo, the other player's moves) is the game session's (session.py), and what differs
between hot-seat, game-file, relay and same-copy games is the game's connection's (connections.py): these routes
turn web requests into their calls and their results into answers. What the board draws a game from is the
board view's (view.py), next to the engine.
"""

import json
import random
import re
import uuid

from flask import Response, current_app, jsonify, render_template, request

from .. import __version__, access, armies, mail
from ..access import host_only
from ..match_view import split_abilities
from ..models import SavedArmy, SavedBattlefield, SavedGame, Unit, User, db
from ..settings import update_settings
from . import battlefields, game_file, mission_state, missions, relay_client, view
from .connections import (DEFAULT_RELAY_URL, RELAY, Refused, clean_code, connect, game_data, kind_of, player_name,
                          put_online, relay, relay_url)
from .game import ActionError, charge_options, fight_options, measure, shooting_options
from .geometry import FOOTPRINTS, footprint_corners
from .session import (CLOSED, MOVED_ON, NOT_NOW, NOT_YOURS, REFUSED, RELAY_REFUSED, UNREACHABLE, GameSession, Notifier,
                      forget, needs_update)
from .snapshot import freeze_army


class MailNotifier(Notifier):
    """'Your move' emails for games between two players of this copy (mail.py)."""

    def seen(self, saved):
        if saved.seat_users and access.viewer():
            mail.seen(saved.id, access.viewer().id)

    def your_move(self, saved, seat):
        other, me = db.session.get(User, saved.seat_users[seat]), access.viewer()
        if other and other.email and other.notify is not False:
            mail.your_move(other, me.name, saved, request.host_url)


def session_for(game_id, write=False):
    """The game session of a game this player can open (404 otherwise; access.py)."""
    return GameSession(access.get(SavedGame, game_id, write=write), MailNotifier())


STATUS = {REFUSED: 400, NOT_YOURS: 403, NOT_NOW: 409, CLOSED: 410, UNREACHABLE: 503, RELAY_REFUSED: 502}


def answer(session, result):
    """A session call's result as a web answer: the game (with any extra fields), or why not."""
    if result.done:
        if result.extra.get('unchanged'):
            return jsonify({'id': session.saved.id, 'moved': 0, 'unchanged': True, 'waiting_for_army': False,
                            'connection': session.connection.status()})
        return game_response(session, result.state, **result.extra)
    if result.kind == MOVED_ON:  # with the game as it is now
        return jsonify(dict(game_response(session).get_json(), error=result.message)), 409
    return jsonify({'error': result.message}), STATUS[result.kind]


def game_response(session, state=None, **extra):
    """A game with its state (`state`, else the session's), and what the page needs to draw it: the board and each
    part's outline, its connection, and whether Undo can be pressed. A game still waiting for the other player's
    army has no state: it says so ('waiting_for_army')."""
    saved = session.saved
    if session.waiting_for_army():
        return jsonify({'id': saved.id, 'name': saved.name, 'events': [], 'state': None, 'waiting_for_army': True,
                        'uid': saved.uid, 'seat': session.seat, 'players': [a and a['name'] for a in saved.armies],
                        'connection': session.connection.status(), 'can_undo': False,
                        'app_version': saved.app_version, 'needs_update': needs_update(saved),
                        'options': view.pending_summary(saved.pending), **extra})
    state = session.state() if state is None else state
    return jsonify({**extra, 'id': saved.id, 'name': saved.name, 'events': saved.events or [], 'state': state,
                    'uid': saved.uid, 'seat': session.seat,
                    'connection': session.connection.status(), 'can_undo': session.can_undo(state),
                    'app_version': saved.app_version, 'needs_update': needs_update(saved),
                    **view.board(state, saved.armies, (saved.events or [None])[-1])})


MAX_IMPORT = 10 * 1024 * 1024  # Bytes: the largest game file opened


def read_seat(value):
    """A seat from a request: 0 or 1, or None (hot-seat); anything else is a ValueError."""
    if value is None or value == '':
        return None
    seat = int(value)
    if seat not in (0, 1):
        raise ValueError(seat)
    return seat


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
        if saved is not None and access.can_read(saved):
            return dict(saved.data, name=saved.name, key=key, id=key, source='custom')
    return None


def register_play_routes(app):

    @app.route('/play')
    def play_page():
        return render_template('play.html')

    @app.route('/api/play/setting', methods=['POST'])
    def api_play_setting():
        """Show or hide Play in your menu (the Setup page; a self-hosted copy's players, their Account page)."""
        enabled = bool((request.get_json(silent=True) or {}).get('enabled'))
        access.principal().set_preferences(show_play=enabled)
        return jsonify({'enabled': enabled})

    @app.route('/api/play/relay', methods=['GET', 'POST'])
    @host_only
    def api_play_relay():
        """Online play's settings (Setup page): the relay's address ('url'; empty goes back to the project's own) and
        the name you play under ('name', shown to the other player)."""
        if request.method == 'POST':
            data = request.get_json(silent=True) or {}
            if 'url' in data and not access.principal().local:
                return jsonify({'error': "The relay's address is set with RELAY_URL on a self-hosted copy (hosted mode)."}), 403
            if 'url' in data:
                url = str(data.get('url') or '').strip().rstrip('/')
                if url and not url.startswith(('https://', 'http://127.0.0.1', 'http://localhost')):
                    return jsonify({'error': 'The relay\'s address starts with https://'}), 400
                update_settings(current_app.config['SETTINGS_JSON'], relay_url=url or None)  # The install's
            if 'name' in data:  # Yours (a self-hosted copy's players go by their account's name)
                name = ''.join(c for c in str(data.get('name') or '') if c not in '<>"\'`' and ord(c) >= 32).strip()[:40]
                access.principal().set_preferences(player_name=name or None)
        return jsonify({'url': relay_url(), 'default': DEFAULT_RELAY_URL, 'name': player_name()})

    @app.route('/api/play/games')
    def api_list_games():
        games = access.listed(SavedGame, SavedGame.query.order_by(SavedGame.updated_at.desc(), SavedGame.id.desc()))
        return jsonify([{'id': g.id, 'name': g.name, 'players': [a['name'] if a else 'joining online' for a in g.armies],
                         'seat': connect(g).seat(), 'connection': kind_of(g),
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
        seat_users, error = game_players(data.get('opponent'), seat, open_seat)
        if error:
            return jsonify({'error': error}), 400
        frozen = []
        for side, army_id in enumerate(army_ids):
            if army_id is None:
                frozen.append(None)
                continue
            try:
                saved = db.session.get(SavedArmy, int(army_id))
            except (TypeError, ValueError):
                saved = None
            if saved is None:
                return jsonify({'error': 'That army is no longer saved.'}), 400
            if (access.owner_of(saved) != seat_users[side] if seat_users else not access.can_write(saved)):
                whose = 'theirs' if seat_users and side != seat else 'yours'
                return jsonify({'error': f"{saved.name} isn't {whose}: each player plays one of their own armies "
                                         '(copy one on the Armies page to make it yours).'}), 400
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
                          uid=uuid.uuid4().hex, seat=None if seat_users else seat, app_version=__version__, setup=setup,
                          owner_id=access.stamp(), seat_users=seat_users)
        db.session.add(saved)
        db.session.commit()
        return game_response(GameSession(saved))

    def game_players(opponent, seat, open_seat):
        """A self-hosted copy's game against another of its players ({'opponent': their ID}): (each seat's player
        ID, None), (None, None) for any other game, or (None, an error)."""
        if opponent in (None, ''):
            return None, None
        user = access.viewer()
        if user is None:  # A desktop install has no other players
            return None, 'Only a self-hosted copy has other players.'
        other = db.session.get(User, opponent) if isinstance(opponent, int) and not isinstance(opponent, bool) else None
        if other is None or other.id == user.id or other.password_hash is None:
            return None, 'Choose another player who has joined this copy.'
        if seat is None or open_seat is not None:
            return None, 'Against another player here, choose which side you play and an army for each side.'
        players = [None, None]
        players[seat], players[1 - seat] = user.id, other.id
        return players, None

    @app.route('/api/play/opponents')
    def api_opponents():
        """The other players of a self-hosted copy, to play against there (empty elsewhere)."""
        user = access.viewer()
        if user is None:  # A desktop install has no other players
            return jsonify([])
        others = User.query.filter(User.id != user.id, User.password_hash.isnot(None)).order_by(User.name_key)
        return jsonify([{'id': u.id, 'name': u.name} for u in others])

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
                          pending=dict(options, battlefield=battlefield), owner_id=access.stamp())
        try:
            put_online(saved)
        except relay_client.RelayError as error:
            return jsonify({'error': f'{error} The game needs the relay: the other player joins it there.'}), 503 if error.status is None else 502
        db.session.add(saved)
        db.session.commit()
        return game_response(GameSession(saved))

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
        return game_response(session_for(game_id))

    @app.route('/api/play/games/<int:game_id>', methods=['DELETE'])
    def api_delete_game(game_id):
        saved = access.get(SavedGame, game_id, write=True)
        forget(saved)
        db.session.delete(saved)
        db.session.commit()
        return jsonify({'deleted': game_id})

    @app.route('/api/play/games/<int:game_id>/actions', methods=['POST'])
    def api_game_action(game_id):
        """Resolve an action (rolling any dice) and add its event to the game."""
        session = session_for(game_id)
        action = request.get_json(silent=True)
        if not isinstance(action, dict):
            return jsonify({'error': 'Send the action as a JSON object.'}), 400
        return answer(session, session.act(action))

    @app.route('/api/play/games/<int:game_id>/undo', methods=['POST'])
    def api_undo(game_id):
        """Take back the last action, as the game's connection says (session.GameSession.undo)."""
        session = session_for(game_id)
        return answer(session, session.undo())

    @app.route('/api/play/games/<int:game_id>/seat', methods=['PUT'])
    def api_game_seat(game_id):
        """Which player this app plays in the game: 0, 1 or None (both, hot-seat)."""
        session = session_for(game_id)
        try:
            seat = read_seat((request.get_json(silent=True) or {}).get('seat'))
        except (TypeError, ValueError):
            return jsonify({'error': 'Choose player 1, player 2 or both.'}), 400
        try:
            session.connection.choose_seat(seat)
        except Refused as error:
            return jsonify({'error': str(error)}), 409
        db.session.commit()
        return game_response(GameSession(session.saved))

    @app.route('/api/play/games/<int:game_id>/file')
    def api_game_file(game_id):
        """The game as a file to send to the other player (game_file.py)."""
        session = session_for(game_id)
        if session.waiting_for_army():
            return jsonify({'error': 'The other player joins this game online, with its invite code.'}), 409
        body = json.dumps(game_file.export_game(game_data(session.saved), session.seat))
        filename = re.sub(r'[^\w\- ]+', '', session.saved.name).strip() or 'game'
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
        saved = access.mine(SavedGame.query.filter_by(uid=game['uid']), SavedGame).first()
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
                              app_version=game['app_version'] or __version__, owner_id=access.stamp())
            db.session.add(saved)
            how = 'new'
        elif not connect(saved).takes_files:
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
        forget(saved)
        return game_response(GameSession(saved), imported=how)

    @app.route('/api/play/games/<int:game_id>/online', methods=['POST'])
    def api_game_online(game_id):
        """Put a game this app plays one side of on the relay: its 'connection' then has the invite code."""
        session = session_for(game_id)
        try:
            session.connection.go_online()
        except Refused as error:
            return jsonify({'error': str(error)}), 400
        except relay_client.RelayError as error:
            return jsonify({'error': str(error)}), 503 if error.status is None else 502
        db.session.commit()
        return game_response(GameSession(session.saved))

    @app.route('/api/play/games/<int:game_id>/new-code', methods=['POST'])
    def api_game_new_code(game_id):
        """
        Move an online game to a new invite code (connections.Relay.new_code): the other player joins again with it.
        The other player's moves are fetched first, so none is lost.
        """
        session = session_for(game_id)
        if session.connection.kind != RELAY:
            return jsonify({'error': 'This game isn\'t online.'}), 400
        try:
            session.connection.fetch(forget)
        except relay_client.RelayError as error:
            if error.status != 404:  # Already closed: nothing more to fetch
                return answer(session, session.relay_failed(error))
        try:
            session.connection.new_code()
        except relay_client.RelayError as error:
            return jsonify({'error': str(error)}), 503 if error.status is None else 502
        return game_response(GameSession(session.saved))

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
        if access.mine(SavedGame.query.filter_by(relay_code=code), SavedGame).first():
            return jsonify({'error': 'That game is already on this PC.'}), 409
        completed = None
        try:
            invited = relay().invite(code)
            if invited.get('needs_army'):
                seat = invited['seat']
                game_file.check_invite(invited['game'], seat)
                theirs = invited['game']['armies'][1 - seat]
                if data.get('army') is None:
                    return jsonify({'needs_army': True, 'code': code, 'options': view.pending_summary(invited['game']['pending']),
                                    'opponent': {'army': theirs['name'], 'faction': theirs.get('faction'),
                                                 'disposition': theirs.get('disposition'), 'player': invited['names'][1 - seat]}})
                completed, error = complete_invite(invited['game'], seat, data['army'])
                if error:
                    return jsonify({'error': error}), 400
            joined = relay().join(code, player_name(), completed)
            game = game_file.read_game(joined['game'], __version__)
        except relay_client.RelayError as error:
            return jsonify({'error': str(error)}), 503 if error.status is None else 400
        except game_file.GameFileError as error:
            return jsonify({'error': str(error)}), 400
        saved = access.mine(SavedGame.query.filter_by(uid=game['uid']), SavedGame).first() or SavedGame(uid=game['uid'], owner_id=access.stamp())
        saved.name, saved.armies, saved.events, saved.battlefield = game['name'], game['armies'], game['events'], game['battlefield']
        saved.setup, saved.twist, saved.seat = game['setup'], game['twist'], joined['seat']
        saved.app_version = game['app_version'] or __version__
        saved.relay_code, saved.relay_token, saved.relay_count, saved.relay_joined = code, joined['token'], len(game['events']), True
        saved.relay_names = joined.get('names') or [None, None]
        db.session.add(saved)
        db.session.commit()
        forget(saved)
        return game_response(GameSession(saved))

    @app.route('/api/play/games/<int:game_id>/sync', methods=['POST'])
    def api_sync_game(game_id):
        """
        The other player's moves ('moved': how many came). With ?since=<events the page has>, a game with nothing new
        answers {'moved': 0, 'unchanged': true} without replaying it (the page polls every few seconds).
        """
        session = session_for(game_id)
        return answer(session, session.sync(request.args.get('since', type=int)))

    def complete_invite(invited, seat, army_id):
        """The invited game completed with your saved army in `seat`, and the choices that waited for it: (its game
        file, None) or (None, an error)."""
        try:
            saved_army = db.session.get(SavedArmy, int(army_id))
        except (TypeError, ValueError):
            saved_army = None
        if saved_army is None or not access.can_write(saved_army):
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
        session = session_for(game_id)
        if session.waiting_for_army():
            return jsonify({'error': "The other player hasn't joined with their army yet."}), 409
        try:
            return jsonify(OPTIONS[kind](session.state(), unit_id))
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
                   for b in access.listed(SavedBattlefield, SavedBattlefield.query.order_by(SavedBattlefield.id))]
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
        saved = SavedBattlefield(name=data['name'], data=data, owner_id=access.stamp())
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
        saved = access.get(SavedBattlefield, int(key.split(':')[1]), write=True)
        saved.name, saved.data = data['name'], data
        db.session.commit()
        return jsonify(find_battlefield(key))

    @app.route('/api/play/battlefields/<key>', methods=['DELETE'])
    def api_delete_battlefield(key):
        if not key.startswith('saved:') or find_battlefield(key) is None:
            return jsonify({'error': 'No such saved battlefield.'}), 404
        db.session.delete(access.get(SavedBattlefield, int(key.split(':')[1]), write=True))
        db.session.commit()
        return jsonify({'deleted': key})
