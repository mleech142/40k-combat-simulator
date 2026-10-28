"""
Named players on a self-hosted copy (hosted mode; docs/self-hosting.md, hosted-version-scope.md section 8 b).

- The first visit to a new copy creates the **host** (/welcome). The host adds players by name and email and sends
  each a one-time **join link** (/join/<token>, 7 days), where they choose their own password. A new link is how a
  forgotten password is reset.
- Logins are a session cookie holding the player's ID and `session_key`; changing the key (new password, removal)
  logs out every browser they use. The cookie is SameSite=Lax, and writes from other sites are refused.
- Host only: Setup and its downloads, the Play menu setting, the Players page, and app data writes (players'
  proposals come with step d).

Outside hosted mode none of this runs: there is no login.
"""

import hashlib
import os
import re
import secrets
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from flask import g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .models import User, db

SECRET_KEY_FILE = 'secret_key'
JOIN_DAYS = 7
MIN_PASSWORD = 8
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-]{0,29}$")
EMAIL_RE = re.compile(r"^[^@\s<>\"'`]+@[^@\s<>\"'`]+\.[^@\s<>\"'`]+$")
LOGIN_TRIES = 5  # wrong passwords for one name before it waits
LOGIN_WAIT = 60  # seconds

# Reachable without logging in
OPEN_PATHS = ('/login', '/welcome', '/join/', '/api/auth/login', '/api/auth/welcome', '/api/auth/join', '/static/')
# The host's alone (prefixes; app data writes are checked by method below)
HOST_PATHS = ('/setup', '/api/bsdata/', '/api/wahapedia/', '/api/play/setting', '/api/play/relay', '/players',
              '/api/players')
SAFE_METHODS = ('GET', 'HEAD', 'OPTIONS')

_failed = {}  # name_key -> (count, time of the last failure)


def secret_key(folder):
    """The copy's session secret, kept in its data folder so logins survive restarts."""
    path = Path(folder) / SECRET_KEY_FILE
    try:
        key = path.read_text(encoding='utf-8').strip()
        if len(key) >= 32:
            return key
    except OSError:
        pass
    key = secrets.token_hex(32)
    path.write_text(key, encoding='utf-8')
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def hash_token(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def clean_name(value):
    name = ' '.join(str(value or '').split())
    if not NAME_RE.match(name):
        raise ValueError('A name is 1 to 30 letters, digits, spaces, dots, dashes or underscores, starting with a letter or digit.')
    return name


def clean_email(value):
    email = str(value or '').strip()
    if len(email) > 254 or not EMAIL_RE.match(email):
        raise ValueError('That doesn\'t look like an email address.')
    return email


def check_password(value):
    password = str(value or '')
    if len(password) < MIN_PASSWORD:
        raise ValueError(f'A password needs at least {MIN_PASSWORD} characters.')
    if len(password) > 200:
        raise ValueError('That password is too long.')
    return password


def new_join_link(user):
    """Give `user` a fresh join link (the old one stops working) and return its path."""
    token = secrets.token_urlsafe(24)
    user.join_token_hash = hash_token(token)
    user.join_expires = datetime.utcnow() + timedelta(days=JOIN_DAYS)
    return f'/join/{token}'


def user_for_token(token):
    if not token:
        return None
    user = User.query.filter_by(join_token_hash=hash_token(token)).first()
    if user is None or not user.join_expires or user.join_expires < datetime.utcnow():
        return None
    return user


def log_in(user):
    if not user.session_key:
        user.session_key = secrets.token_hex(16)
    session.clear()
    session.permanent = True
    session['uid'] = user.id
    session['key'] = user.session_key


def current_user():
    """The logged-in player of this request (hosted mode), or None."""
    return getattr(g, 'user', None)


def is_host():
    user = current_user()
    return bool(user and user.is_host)


def cross_site():
    """Whether a write came from another site's page (the cookie alone mustn't be enough)."""
    if request.headers.get('Sec-Fetch-Site') in ('cross-site', 'same-site'):
        return True
    origin = request.headers.get('Origin')
    if origin and origin != 'null':
        return urlsplit(origin).netloc.lower() != request.host.lower()
    return origin == 'null'


def wants_page():
    return not request.path.startswith('/api/')


def register_account_routes(app):
    if not app.config.get('HOSTED'):
        return

    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_NAME'] = 'combat_sim_session'
    app.config['SESSION_COOKIE_SECURE'] = os.environ.get('COMBAT_SIM_SECURE_COOKIES', '').strip() in ('1', 'true', 'yes')
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

    @app.before_request
    def gate():
        g.user = None
        if request.method not in SAFE_METHODS and cross_site():
            return jsonify({'error': 'Refused: that request came from another site.'}), 403
        uid = session.get('uid')
        if uid is not None:
            user = db.session.get(User, uid)
            if user and user.password_hash and user.session_key and session.get('key') == user.session_key:
                g.user = user
            else:
                session.clear()
        path = request.path
        if path.startswith(OPEN_PATHS):
            return None
        if g.user is None:
            no_host = User.query.count() == 0
            if wants_page():
                return redirect('/welcome' if no_host else url_for('login_page', next=request.full_path.rstrip('?')))
            return jsonify({'error': 'Log in first.', 'login': True}), 401
        host_only = path.startswith(HOST_PATHS) or (path.startswith('/api/data/') and request.method not in SAFE_METHODS)
        if host_only and not g.user.is_host:
            message = ('Only the host can change the shared data for now.' if path.startswith('/api/data/')
                       else 'Only the host can do that.')
            if wants_page():
                return render_template('account.html', mode='refused', message=message), 403
            return jsonify({'error': message}), 403
        return None

    def form():
        return request.get_json(silent=True) or {}

    def error(message, status=400):
        return jsonify({'error': message}), status

    # --- Logging in ---------------------------------------------------------------

    @app.route('/welcome')
    def welcome_page():
        if User.query.count():
            return redirect('/login')
        return render_template('account.html', mode='welcome')

    @app.route('/api/auth/welcome', methods=['POST'])
    def api_auth_welcome():
        """Create the host: only while the copy has no players at all."""
        if User.query.count():
            return error('This copy already has a host.', 403)
        data = form()
        try:
            name, email = clean_name(data.get('name')), clean_email(data.get('email'))
            password = check_password(data.get('password'))
        except ValueError as err:
            return error(str(err))
        user = User(name=name, name_key=name.lower(), email=email, is_host=True,
                    password_hash=generate_password_hash(password))
        db.session.add(user)
        db.session.flush()  # its ID, for the login
        log_in(user)
        db.session.commit()
        return jsonify(user.to_dict())

    @app.route('/login')
    def login_page():
        if not User.query.count():
            return redirect('/welcome')
        return render_template('account.html', mode='login')

    @app.route('/api/auth/login', methods=['POST'])
    def api_auth_login():
        data = form()
        key = ' '.join(str(data.get('name') or '').split()).lower()
        count, last = _failed.get(key, (0, 0))
        if count >= LOGIN_TRIES and time.time() - last < LOGIN_WAIT:
            return error(f'Too many wrong passwords: wait a minute and try again.', 429)
        user = User.query.filter_by(name_key=key).first() if key else None
        if not (user and user.password_hash and check_password_hash(user.password_hash, str(data.get('password') or ''))):
            _failed[key] = (count + 1 if time.time() - last < LOGIN_WAIT * 10 else 1, time.time())
            return error('Wrong name or password.', 401)
        _failed.pop(key, None)
        log_in(user)
        db.session.commit()
        return jsonify(user.to_dict())

    @app.route('/api/auth/logout', methods=['POST'])
    def api_auth_logout():
        session.clear()
        return jsonify({'message': 'Logged out'})

    @app.route('/join/<token>')
    def join_page(token):
        user = user_for_token(token)
        return render_template('account.html', mode='join' if user else 'bad-link', player=user)

    @app.route('/api/auth/join', methods=['POST'])
    def api_auth_join():
        """Choose a password with a join link (a first one, or a new one the host sent)."""
        data = form()
        user = user_for_token(str(data.get('token') or ''))
        if user is None:
            return error('That link has expired or was already used: ask the host for a new one.', 403)
        try:
            password = check_password(data.get('password'))
        except ValueError as err:
            return error(str(err))
        user.password_hash = generate_password_hash(password)
        user.join_token_hash = user.join_expires = None
        user.session_key = secrets.token_hex(16)  # logs out anyone using the old password
        db.session.commit()
        log_in(user)
        return jsonify(user.to_dict())

    # --- Your account -------------------------------------------------------------

    @app.route('/account')
    def account_page():
        return render_template('account.html', mode='account')

    @app.route('/api/auth/me')
    def api_auth_me():
        return jsonify(current_user().to_dict())

    @app.route('/api/auth/account', methods=['POST'])
    def api_auth_account():
        """Change your email, or your password (with the current one)."""
        user, data = current_user(), form()
        try:
            if 'email' in data:
                user.email = clean_email(data.get('email'))
            if data.get('new_password') is not None:
                if not check_password_hash(user.password_hash, str(data.get('password') or '')):
                    return error('Your current password is wrong.', 403)
                user.password_hash = generate_password_hash(check_password(data.get('new_password')))
                user.session_key = secrets.token_hex(16)
                log_in(user)
        except ValueError as err:
            db.session.rollback()
            return error(str(err))
        db.session.commit()
        return jsonify(user.to_dict())

    # --- The host's Players page ---------------------------------------------------

    @app.route('/players')
    def players_page():
        return render_template('account.html', mode='players')

    @app.route('/api/players')
    def api_players():
        return jsonify([u.to_dict() for u in User.query.order_by(User.name_key)])

    @app.route('/api/players', methods=['POST'])
    def api_add_player():
        """Add a player {name, email}; the answer has their join link to send them."""
        data = form()
        try:
            name, email = clean_name(data.get('name')), clean_email(data.get('email'))
        except ValueError as err:
            return error(str(err))
        if User.query.filter_by(name_key=name.lower()).first():
            return error(f'There is already a player called {name}.')
        user = User(name=name, name_key=name.lower(), email=email)
        link = new_join_link(user)
        db.session.add(user)
        db.session.commit()
        return jsonify(dict(user.to_dict(), link=link))

    def player_or_404(player_id):
        user = db.session.get(User, player_id)
        if user is None:
            return None, error('No such player.', 404)
        return user, None

    @app.route('/api/players/<int:player_id>/link', methods=['POST'])
    def api_player_link(player_id):
        """A new join link for a player who lost theirs or forgot their password (their password works until it's used)."""
        user, failed = player_or_404(player_id)
        if failed:
            return failed
        link = new_join_link(user)
        db.session.commit()
        return jsonify(dict(user.to_dict(), link=link))

    @app.route('/api/players/<int:player_id>', methods=['PUT'])
    def api_update_player(player_id):
        user, failed = player_or_404(player_id)
        if failed:
            return failed
        try:
            user.email = clean_email(form().get('email'))
        except ValueError as err:
            return error(str(err))
        db.session.commit()
        return jsonify(user.to_dict())

    @app.route('/api/players/<int:player_id>', methods=['DELETE'])
    def api_remove_player(player_id):
        user, failed = player_or_404(player_id)
        if failed:
            return failed
        if user.is_host:
            return error('The host can\'t be removed.', 403)
        db.session.delete(user)
        db.session.commit()
        return jsonify({'message': f'{user.name} removed'})
