"""
Proposed edits on a self-hosted copy (hosted-version-scope.md section 8 d). A player's write to the shared app data
(any POST / PUT / DELETE under /api/data/ but the import) isn't made: it is saved as a Proposal, with a snapshot of
the record as it was, and answered 202. The host reviews each on the Proposals page: what it changes, and whether
the record has changed since it was proposed (the host decides: agreed with the owner). Approving sends it to the
same route a direct edit uses, as the host, so it is checked, applied and saved (`save_data`) the same way.
Players see their own proposals and can withdraw them while they wait.
"""

import json
import re
from datetime import datetime

from flask import g, jsonify, render_template, request
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import HTTPException

from . import access
from .access import host_only
from .models import Faction, FactionRule, MeleeWeapon, Proposal, RangedWeapon, Stratagem, Unit, User, db

MAX_PENDING = 50  # a player's proposals waiting at once
MAX_BODY = 200_000  # bytes
PATH_RE = re.compile(r'^/api/data/(factions|rules|stratagems|units|weapons)(?:/(ranged|melee))?(?:/(\d+))?$')
MODELS = {'factions': Faction, 'rules': FactionRule, 'stratagems': Stratagem, 'units': Unit,
          'ranged': RangedWeapon, 'melee': MeleeWeapon}
KIND_NAMES = {'factions': 'faction', 'rules': 'rule', 'stratagems': 'stratagem', 'units': 'unit',
              'ranged': 'ranged weapon', 'melee': 'melee weapon', 'weapons': 'weapon'}
VERBS = {'POST': 'Add', 'PUT': 'Edit', 'DELETE': 'Delete'}


def plain_value(value):
    """A value as JSON can hold it (anything else as text)."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


def record_for(path):
    """The record a path names (PUT / DELETE), or None (a new one, or gone)."""
    match = PATH_RE.match(path)
    if not match or not match.group(3):
        return None
    kind, weapon_type, ident = match.groups()
    model = MODELS.get(weapon_type if kind == 'weapons' else kind)
    return db.session.get(model, int(ident)) if model else None


def snapshot(record):
    """A record's columns, to compare later."""
    if record is None:
        return None
    return {column.name: plain_value(getattr(record, column.name)) for column in record.__table__.columns}


def describe(method, path, body, before):
    kind, weapon_type, _ = PATH_RE.match(path).groups()
    if kind == 'weapons' and not weapon_type:
        weapon_type = (body or {}).get('type') if (body or {}).get('type') in ('ranged', 'melee') else None
    noun = KIND_NAMES.get(weapon_type or kind, kind)
    name = (body or {}).get('name') or (before or {}).get('name')
    faction_id = (body or {}).get('faction_id') or (before or {}).get('faction_id')
    faction = db.session.get(Faction, int(faction_id)) if str(faction_id or '').isdigit() and kind != 'factions' else None
    text = f"{VERBS[method]} {noun}" + (f" '{str(name)[:120]}'" if name else '') + (f' ({faction.name})' if faction else '')
    return text[:300]


def propose():
    """Save the current request (a player's Data-tab write) as a proposal: the 202 answer, or an error."""
    if (request.content_length or 0) > MAX_BODY:
        return jsonify({'error': 'That change is too big to send.'}), 400
    path, method = request.path, request.method  # a @proposable route (access.py); check_routes says it can be read
    user = access.viewer()
    if Proposal.query.filter_by(owner_id=user.id, status='pending').count() >= MAX_PENDING:
        return jsonify({'error': f'You have {MAX_PENDING} proposals waiting: let the host catch up first.'}), 429
    body = request.get_json(silent=True)
    record = record_for(path)
    if method != 'POST' and record is None:
        return jsonify({'error': 'That record no longer exists.'}), 404
    before = snapshot(record)
    proposal = Proposal(owner_id=user.id, method=method, path=path, body=body, before=before,
                        summary=describe(method, path, body, before))
    db.session.add(proposal)
    db.session.commit()
    return jsonify({'proposed': True, 'message': 'Sent to the host to approve.', 'proposal': proposal_dict(proposal)}), 202


# The Data tab sends some fields under other names than the columns they set (app.py's apply_*_fields)
FIELD_COLUMNS = {'ap': 'armor_penetration', 'range': 'range_inches'}
ROUTING_FIELDS = {'type'}  # say which kind of record, not part of it
MISSING = object()


def column_for(field, base):
    if field == 'skill':
        return 'ballistic_skill' if 'ballistic_skill' in base else 'weapon_skill'
    return FIELD_COLUMNS.get(field, field)


def comparable(field, value):
    """A value as the Data tab and the database would both write it: '5' and 5 alike, '' as None, lists of text."""
    if isinstance(value, (list, tuple)):
        return [comparable(field, v) for v in value]
    if isinstance(value, str):
        value = value.strip()
        if value == '':
            return None
        try:
            value = int(value)
        except ValueError:
            return value
    if field == 'ap' and isinstance(value, int):
        return abs(value)  # stored without its minus sign
    return value


def changes(proposal, current):
    """What the proposal changes ({field, now, proposed}; 'now' is None for a field with no column of its own) and
    what changed since it was made ({field, then, now})."""
    body = proposal.body if isinstance(proposal.body, dict) else {}
    base = current or proposal.before or {}
    wanted = []
    for field, value in body.items():
        if field in ROUTING_FIELDS:
            continue
        now = base.get(column_for(field, base), MISSING)
        if now is not MISSING and comparable(field, now) == comparable(field, value):
            continue
        wanted.append({'field': field, 'now': None if now is MISSING else now, 'proposed': value})
    since = []
    if proposal.before is not None and current is not None:
        since = [{'field': k, 'then': proposal.before.get(k), 'now': v}
                 for k, v in current.items() if proposal.before.get(k) != v]
    return wanted, since


def proposal_dict(proposal, names=None, detail=False):
    names = names if names is not None else access.owner_names()
    out = {'id': proposal.id, 'by': names.get(proposal.owner_id), 'mine': bool(access.viewer() and access.viewer().id == proposal.owner_id),
           'summary': proposal.summary, 'method': proposal.method, 'status': proposal.status, 'note': proposal.note,
           'created_at': proposal.created_at.isoformat() if proposal.created_at else None,
           'decided_at': proposal.decided_at.isoformat() if proposal.decided_at else None}
    if detail:
        current = snapshot(record_for(proposal.path))
        wanted, since = changes(proposal, current)
        out.update(changes=wanted, changed_since=since, gone=proposal.method != 'POST' and current is None)
    return out


def apply(proposal):
    """Send the proposal to its route as the host: (ok, the error or None)."""
    from flask import current_app
    app = current_app._get_current_object()
    try:
        endpoint, view_args = app.url_map.bind('localhost').match(proposal.path, method=proposal.method)
    except HTTPException:
        return False, 'That kind of change is no longer possible.'
    host = access.viewer()
    with app.test_request_context(proposal.path, method=proposal.method,
                                  json=proposal.body if proposal.body is not None else None):
        g.user = host
        try:
            response = app.make_response(app.view_functions[endpoint](**view_args))
        except HTTPException as error:
            db.session.rollback()
            return False, error.description or 'It could not be applied.'
        except IntegrityError:
            db.session.rollback()
            return False, 'That would create a duplicate or break a link between records.'
        except Exception as error:  # A field missing or of the wrong kind: the proposal fails, nothing is changed
            db.session.rollback()
            return False, f'It could not be applied: {type(error).__name__} {error}'.strip()
    if response.status_code >= 400:
        db.session.rollback()
        data = response.get_json(silent=True) or {}
        return False, data.get('error') or f'It could not be applied ({response.status_code}).'
    return True, None


def sample_path(rule):
    """A URL a route rule could answer, its variables filled in ('/api/data/weapons/ranged/1')."""
    import re as _re
    return _re.sub(r'<(?:(\w+)(?:\([^)]*\))?:)?(\w+)>', lambda m: '1' if m.group(1) in ('int', 'float') else 'ranged', rule)


def unreadable_routes(app):
    """@proposable routes whose URLs this module can't describe (PATH_RE): access.check refuses to start."""
    return sorted(str(rule) for rule in app.url_map.iter_rules()
                  if access.route_label(app, rule.endpoint) == access.PROPOSABLE and not PATH_RE.match(sample_path(str(rule))))


def register_proposal_routes(app):
    if not app.config.get('HOSTED'):
        return

    @app.route('/proposals')
    def proposals_page():
        return render_template('proposals.html')

    @app.route('/api/proposals')
    def api_proposals():
        """The host: every proposal, waiting ones first. A player: their own (access.py)."""
        found = sorted(access.visible(Proposal.query, Proposal).order_by(Proposal.id.desc()).limit(200),
                       key=lambda p: p.status != 'pending')
        names = access.owner_names()
        return jsonify([proposal_dict(p, names, detail=p.status == 'pending') for p in found])

    def decide(proposal_id, withdraw=False):
        proposal = access.get(Proposal, proposal_id, write=withdraw)
        if proposal.status != 'pending':
            return None, (jsonify({'error': 'That proposal has already been answered.'}), 409)
        return proposal, None

    @app.route('/api/proposals/<int:proposal_id>/approve', methods=['POST'])
    @host_only
    def api_approve_proposal(proposal_id):
        proposal, error = decide(proposal_id)
        if error:
            return error
        ok, message = apply(proposal)
        proposal = db.session.get(Proposal, proposal_id)
        proposal.status, proposal.note = ('approved', None) if ok else ('failed', message)
        proposal.decided_at = datetime.utcnow()
        db.session.commit()
        answer = proposal_dict(proposal)
        return (jsonify(answer), 200) if ok else (jsonify(dict(answer, error=message)), 400)

    @app.route('/api/proposals/<int:proposal_id>/reject', methods=['POST'])
    @host_only
    def api_reject_proposal(proposal_id):
        proposal, error = decide(proposal_id)
        if error:
            return error
        note = ' '.join(str((request.get_json(silent=True) or {}).get('note') or '').split())[:500] or None
        proposal.status, proposal.note, proposal.decided_at = 'rejected', note, datetime.utcnow()
        db.session.commit()
        return jsonify(proposal_dict(proposal))

    @app.route('/api/proposals/<int:proposal_id>', methods=['DELETE'])
    def api_withdraw_proposal(proposal_id):
        """A player takes back their own proposal while it waits."""
        proposal, error = decide(proposal_id, withdraw=True)
        if error:
            return error
        proposal.status, proposal.decided_at = 'withdrawn', datetime.utcnow()
        db.session.commit()
        return jsonify(proposal_dict(proposal))
