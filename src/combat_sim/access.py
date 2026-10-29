"""
Who may do what: the one place the app decides it (docs/hosted-version-scope.md; architecture review, candidate 1).

**Records.** Every saved record kind with an owner is in `POLICY`, with its sharing rule:

- ``shared``: every player reads it, only its owner changes it (armies).
- ``private``: only its owner (scenarios, comparisons, battlefields).
- ``game``: its owner (who started it) and the two players seated in it read it; only the owner changes it
  (deletes it). Which side each plays is Play's rule (play/routes.py `seat_of`), not this module's.
- ``proposal``: its owner and the host read it; only its owner changes it (withdraws it).

A record's owner is ``owner_id``; None means the host's (rows from before the copy had players). The host has no
extra right to other players' records. On a desktop install (no logins) the one user is the host and owns
everything. Callers never ask whether the copy is self-hosted: they ask ``principal()`` who is acting (a
``Desktop``, ``Player`` or ``Visitor``) and what that lets them do, and keep that person's own preferences
through it (settings.py).

Records go through four calls: ``get(Model, id)`` (to read; None for "not found"), ``get(Model, id, write=True)``
(to change: "not allowed" with a reason when it can be read but not changed), ``visible(query, Model)`` /
``listed(Model)`` (what the player can see) and ``stamp()`` (the owner for a new record). Failing reads answer 404,
so nobody learns a record exists; failing writes 403.

**Routes.** Every route is for any logged-in player unless labelled, just under its ``@app.route``:
``@open_to_all`` (login pages), ``@host_only``, ``@proposable`` (a player's request becomes a proposal for the host,
proposals.py) and ``@desktop_only`` (refused on a self-hosted copy). One ``before_request`` gate reads the label of
the route a request reaches. ``check(app)`` refuses to start when a Data-tab write route has no label, or a saved
table with an owner isn't in ``POLICY``.
"""

from urllib.parse import urlsplit

from flask import abort, current_app, g, jsonify, redirect, render_template, request, url_for
from sqlalchemy import or_

from . import settings
from .models import Proposal, SavedArmy, SavedBattlefield, SavedComparison, SavedGame, SavedScenario, User, db

SHARED, PRIVATE, GAME, PROPOSAL = 'shared', 'private', 'game', 'proposal'
POLICY = {
    SavedArmy: (SHARED, "This army is another player's: copy it to make one you can change."),
    SavedScenario: (PRIVATE, None),
    SavedComparison: (PRIVATE, None),
    SavedBattlefield: (PRIVATE, None),
    SavedGame: (GAME, 'Only the player who started this game can delete it.'),
    Proposal: (PROPOSAL, 'Only the player who proposed it can withdraw it.'),
}

OPEN, PLAYER, HOST, PROPOSABLE, DESKTOP = 'open', 'player', 'host', 'proposable', 'desktop'
SAFE_METHODS = ('GET', 'HEAD', 'OPTIONS')


# --- Who is acting: the principal (architecture review, candidate 7) -----------------------------

class Principal:
    """
    Who is acting on this request, and what that lets them do. Callers ask it instead of whether the copy is
    self-hosted. Three adapters: `Desktop` (the person at their own install: its host, owning everything),
    `Player` (a logged-in player of a self-hosted copy) and `Visitor` (not logged in yet).

    - `user`: their account, or None. `is_host`: may change the install (Setup, players, approving proposals).
    - `shared`: the copy has several players (records have owners; whose they are is shown).
    - `local`: at the computer the app runs on (native dialogs, paths on it, the relay's address).
    - `reads` / `writes` (a record), `narrow` (a query, to what they see or with `own` to their own),
      `owner_id` (for a record they make).
    - `preferences()` / `set_preferences(**changes)`: their own settings (settings.PREFERENCES); the install's
      other settings are the host's (settings.py).
    """
    user = None
    is_host = False
    shared = False
    local = False
    owner_id = None

    def reads(self, record):
        return False

    def writes(self, record):
        return False

    def narrow(self, query, model, own=False):
        return query.filter(db.false())

    def preferences(self):
        return {}

    def set_preferences(self, **changes):
        raise PermissionError('Log in first.')

    @property
    def player_name(self):
        """The name they play under online (shown to the other player), or None."""
        return self.preferences().get('player_name')


class Desktop(Principal):
    """A desktop install: one person, its host, who owns every record; their preferences are in settings.json."""
    is_host = True
    local = True

    def reads(self, record):
        return True

    def writes(self, record):
        return True

    def narrow(self, query, model, own=False):
        return query

    def preferences(self):
        saved = settings.load_settings(current_app.config['SETTINGS_JSON'])
        return {key: saved[key] for key in settings.PREFERENCES if key in saved}

    def set_preferences(self, **changes):
        settings.update_settings(current_app.config['SETTINGS_JSON'], **_preferences_only(changes))


class Player(Principal):
    """A logged-in player of a self-hosted copy: their own records and those POLICY shares. Their preferences are
    their own (users.preferences); one they haven't set is the install's (as the copy had it before players)."""
    shared = True

    def __init__(self, user):
        self.user = user
        self.is_host = bool(user.is_host)
        self.owner_id = user.id

    def _owns(self, record):
        return record.owner_id == self.user.id or (record.owner_id is None and self.is_host)

    def reads(self, record):
        kind, _ = _rule(type(record))
        if kind == SHARED or self._owns(record):
            return True
        if kind == GAME:
            return self.user.id in (record.seat_users or [])
        if kind == PROPOSAL:
            return self.is_host
        return False

    def writes(self, record):
        return self._owns(record)

    def narrow(self, query, model, own=False):
        kind, _ = _rule(model)
        if not own and (kind == SHARED or (kind == PROPOSAL and self.is_host)):
            return query
        theirs = model.owner_id == self.user.id
        return query.filter(or_(theirs, model.owner_id.is_(None)) if self.is_host else theirs)

    def preferences(self):
        install = settings.load_settings(current_app.config['SETTINGS_JSON'])
        found = {key: install[key] for key in settings.PREFERENCES if key in install}
        found.update(self.user.preferences or {})
        found['player_name'] = self.user.name  # Online, a player goes by their account's name
        return found

    def set_preferences(self, **changes):
        changes = _preferences_only(changes)
        changes.pop('player_name', None)
        saved = dict(self.user.preferences or {})
        for key, value in changes.items():
            if value is None:
                saved.pop(key, None)
            else:
                saved[key] = value
        self.user.preferences = saved
        db.session.commit()


class Visitor(Principal):
    """Someone on a self-hosted copy who hasn't logged in: only the routes open to all (the gate sees to that)."""
    shared = True


def _preferences_only(changes):
    unknown = set(changes) - set(settings.PREFERENCES)
    if unknown:
        raise ValueError(f'Not a player preference: {", ".join(sorted(unknown))}')
    return changes


def principal():
    """Who is acting on this request (worked out once a request, after the gate has read the login)."""
    found = g.get('principal')
    if found is None or (current_app.config.get('HOSTED') and found.user is not getattr(g, 'user', None)):  # Logged in or out since
        if not current_app.config.get('HOSTED'):
            found = Desktop()
        else:
            user = getattr(g, 'user', None)
            found = Player(user) if user is not None else Visitor()
        g.principal = found
    return found


def viewer():
    """The logged-in player of this request on a self-hosted copy; None on a desktop install or before login."""
    return principal().user


def is_host():
    """Whether the request comes from the host (always, on a desktop install)."""
    return principal().is_host


# --- Records --------------------------------------------------------------------------------

def _rule(model):
    try:
        return POLICY[model]
    except KeyError:
        raise LookupError(f'{model.__name__} has no access rule in access.POLICY') from None


def can_read(record):
    return principal().reads(record)


def can_write(record):
    return principal().writes(record)


def get(model, ident, write=False):
    """The record, for reading or (`write`) changing: 404 if this player can't see it, 403 with the reason if they
    can see it but not change it."""
    record = db.session.get(model, ident)
    if record is None or not can_read(record):
        abort(404)
    if write and not can_write(record):
        abort(403, description=_rule(model)[1] or 'That is another player\'s.')
    return record


def visible(query, model):
    """`query` narrowed to the records this player can see (games by owner here; `listed` adds seats)."""
    return principal().narrow(query, model)


def mine(query, model):
    """`query` narrowed to this player's own records (a shared one included only if theirs), e.g. "my copy of
    this game"."""
    return principal().narrow(query, model, own=True)


def listed(model, query=None):
    """Every record of `model` this player can see, as a list (the games they're seated in included)."""
    query = model.query if query is None else query
    who = principal()
    if _rule(model)[0] == GAME and who.shared:
        return [r for r in query if who.reads(r)]
    return visible(query, model).all()


def stamp():
    """The owner for a record this player makes (None on a desktop install)."""
    return principal().owner_id


def owner_of(record):
    """The player ID that owns `record`, the host's for rows from before the copy had players."""
    if record.owner_id is not None:
        return record.owner_id
    host = User.query.filter_by(is_host=True).first()
    return host.id if host else None


def owner_names():
    """{owner_id (None: the host): name} for showing whose a record is; a removed player is 'Name (removed)'."""
    names = {u.id: f'{u.name} (removed)' if u.removed else u.name for u in User.query}
    host = User.query.filter_by(is_host=True).first()
    names[None] = host.name if host else None
    return names


# --- Routes ----------------------------------------------------------------------------------

def _label(kind):
    def mark(view):
        view.access = kind
        return view
    return mark


open_to_all = _label(OPEN)
host_only = _label(HOST)
proposable = _label(PROPOSABLE)
desktop_only = _label(DESKTOP)


def route_label(app, endpoint):
    if endpoint == 'static':
        return OPEN
    return getattr(app.view_functions.get(endpoint), 'access', PLAYER)


def cross_site():
    """Whether a write came from another site's page (the login cookie alone mustn't be enough)."""
    if request.headers.get('Sec-Fetch-Site') in ('cross-site', 'same-site'):
        return True
    origin = request.headers.get('Origin')
    if origin and origin != 'null':
        return urlsplit(origin).netloc.lower() != request.host.lower()
    return origin == 'null'


def refused(message, status=403):
    if not request.path.startswith('/api/'):
        return render_template('account.html', mode='refused', message=message), status
    return jsonify({'error': message}), status


def register(app):
    """The gate on every request of a self-hosted copy (a desktop install has none), and the start-up checks."""
    check(app)

    @app.errorhandler(403)
    def not_allowed(error):  # get(..., write=True) and abort(403): the reason, as the pages expect it
        return refused(getattr(error, 'description', None) or 'Not allowed.')

    if not app.config.get('HOSTED'):
        return

    @app.before_request
    def gate():
        from .accounts import load_session_user  # logins live in accounts.py
        g.user = None
        g.pop('principal', None)
        writing = request.method not in SAFE_METHODS
        if writing and cross_site():
            return jsonify({'error': 'Refused: that request came from another site.'}), 403
        load_session_user()
        label = route_label(app, request.endpoint)
        if label == OPEN:
            return None
        if g.user is None:
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Log in first.', 'login': True}), 401
            no_host = User.query.count() == 0
            return redirect('/welcome' if no_host else url_for('login_page', next=request.full_path.rstrip('?')))
        if label == DESKTOP and not principal().local:
            return refused('That is turned off on a self-hosted copy (hosted mode).')
        if label == HOST and not g.user.is_host:
            return refused('Only the host can do that.')
        if label == PROPOSABLE and writing and not g.user.is_host:
            from .proposals import propose  # a player's change waits for the host
            return propose()
        return None


def check(app):
    """Refuse to start with a Data-tab write route nobody labelled (it would be refused for players without a
    word, instead of becoming a proposal), or a saved table with an owner that has no rule."""
    unlabelled = sorted(str(rule) for rule in app.url_map.iter_rules()
                        if str(rule).startswith('/api/data/') and set(rule.methods) - set(SAFE_METHODS)
                        and route_label(app, rule.endpoint) not in (PROPOSABLE, HOST))
    if unlabelled:
        raise RuntimeError(f'Data-tab write routes need @proposable or @host_only (access.py): {", ".join(unlabelled)}')
    missing = sorted(m.__name__ for m in db.Model.__subclasses__() if 'owner_id' in m.__table__.columns and m not in POLICY)
    if missing:
        raise RuntimeError(f'Saved tables with an owner need a rule in access.POLICY: {", ".join(missing)}')
    unknown = sorted(str(rule) for rule in app.url_map.iter_rules()
                     if route_label(app, rule.endpoint) == PROPOSABLE and not getattr(app.view_functions[rule.endpoint], 'edit', None))
    if unknown:
        raise RuntimeError(f"@proposable routes need the edit they make (app_data_edits.edit_route): {', '.join(unknown)}")
