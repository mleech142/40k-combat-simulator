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
everything, so callers never ask whether the copy is self-hosted to decide access.

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


# --- Who is asking ------------------------------------------------------------------------

def hosted():
    """Whether this copy is self-hosted (logins, several players) rather than a desktop install."""
    return bool(current_app.config.get('HOSTED'))


def viewer():
    """The logged-in player of this request on a self-hosted copy; None on a desktop install or before login."""
    return getattr(g, 'user', None)


def is_host():
    """Whether the request comes from the host (always, on a desktop install)."""
    if not hosted():
        return True
    user = viewer()
    return bool(user and user.is_host)


# --- Records --------------------------------------------------------------------------------

def _rule(model):
    try:
        return POLICY[model]
    except KeyError:
        raise LookupError(f'{model.__name__} has no access rule in access.POLICY') from None


def _owns(record, user):
    return record.owner_id == user.id or (record.owner_id is None and user.is_host)


def can_read(record):
    if not hosted():
        return True
    user = viewer()
    if user is None:
        return False
    kind, _ = _rule(type(record))
    if kind == SHARED or _owns(record, user):
        return True
    if kind == GAME:
        return user.id in (record.seat_users or [])
    if kind == PROPOSAL:
        return user.is_host
    return False


def can_write(record):
    if not hosted():
        return True
    user = viewer()
    return bool(user) and _owns(record, user)


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
    if not hosted():
        return query
    user = viewer()
    kind, _ = _rule(model)
    if kind == SHARED or (kind == PROPOSAL and user.is_host):
        return query
    mine = model.owner_id == user.id
    return query.filter(or_(mine, model.owner_id.is_(None)) if user.is_host else mine)


def mine(query, model):
    """`query` narrowed to this player's own records (a shared one included only if theirs), e.g. "my copy of
    this game"."""
    if not hosted():
        return query
    user = viewer()
    own = model.owner_id == user.id
    return query.filter(or_(own, model.owner_id.is_(None)) if user.is_host else own)


def listed(model, query=None):
    """Every record of `model` this player can see, as a list (the games they're seated in included)."""
    query = model.query if query is None else query
    if _rule(model)[0] == GAME and hosted():
        return [r for r in query if can_read(r)]
    return visible(query, model).all()


def stamp():
    """The owner for a record this player makes (None on a desktop install)."""
    user = viewer()
    return user.id if hosted() and user else None


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
        if label == DESKTOP:
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
