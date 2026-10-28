"""
Proposed edits on a self-hosted copy (hosted-version-scope.md section 8 d; architecture review, candidate 5). A
player's change to the shared app data (a Data-tab route labelled `edit_route`, app_data_edits.py) isn't made: its
edit is checked by making it and undoing it (`app_data_edits.preview`), so a change that can't be made is refused
at once, then saved as a Proposal with the record as it was, and answered 202. The host reviews each on the
Proposals page: what it changes (each column, now and proposed) and whether the record changed since it was
proposed (the host decides: agreed with the owner). Approving applies the edit (`app_data_edits.apply`), the same
call a direct Data-tab change makes. Players see their own proposals and can withdraw them while they wait.
"""

from datetime import datetime

from flask import current_app, jsonify, render_template, request

from . import access
from . import app_data_edits as edits
from .access import host_only
from .models import Proposal, db

MAX_PENDING = 50  # a player's proposals waiting at once
MAX_BODY = 200_000  # bytes


def propose():
    """Save the current request (a player's Data-tab change) as a proposal: the 202 answer, or why not."""
    if (request.content_length or 0) > MAX_BODY:
        return jsonify({'error': 'That change is too big to send.'}), 400
    user = access.viewer()
    if Proposal.query.filter_by(owner_id=user.id, status='pending').count() >= MAX_PENDING:
        return jsonify({'error': f'You have {MAX_PENDING} proposals waiting: let the host catch up first.'}), 429
    body = request.get_json(silent=True)
    edit = edits.from_route(current_app.view_functions[request.endpoint], request.view_args, body)
    try:
        preview = edits.preview(edit)
    except edits.EditError as error:
        return jsonify({'error': str(error)}), error.status
    proposal = Proposal(owner_id=user.id, method=edits.METHODS[edit.action], path=request.path, body=body,
                        edit=edit.to_dict(), before=preview.before, summary=preview.summary)
    db.session.add(proposal)
    db.session.commit()
    return jsonify({'proposed': True, 'message': 'Sent to the host to approve.', 'proposal': proposal_dict(proposal)}), 202


def edit_of(proposal):
    """A proposal's edit; one saved before edits were stored is rebuilt from its route (or None)."""
    if proposal.edit:
        return edits.Edit.from_dict(proposal.edit)
    app = current_app._get_current_object()
    try:
        endpoint, view_args = app.url_map.bind('localhost').match(proposal.path, method=proposal.method)
    except Exception:
        return None
    view = app.view_functions.get(endpoint)
    return edits.from_route(view, view_args, proposal.body) if getattr(view, 'edit', None) else None


def proposal_dict(proposal, names=None, detail=False):
    names = names if names is not None else access.owner_names()
    viewer = access.viewer()
    out = {'id': proposal.id, 'by': names.get(proposal.owner_id), 'mine': bool(viewer and viewer.id == proposal.owner_id),
           'summary': proposal.summary, 'method': proposal.method, 'status': proposal.status, 'note': proposal.note,
           'created_at': proposal.created_at.isoformat() if proposal.created_at else None,
           'decided_at': proposal.decided_at.isoformat() if proposal.decided_at else None}
    if detail:
        edit = edit_of(proposal)
        out.update(changes=[], changed_since=[], gone=False, problem=None)
        if edit is None:
            out['problem'] = 'This kind of change is no longer possible.'
            return out
        try:
            preview = edits.preview(edit)
        except edits.EditError as error:
            out['gone'] = error.status == 404
            out['problem'] = None if out['gone'] else str(error)
            return out
        out['changes'] = preview.changes
        if proposal.before is not None and preview.before is not None:
            out['changed_since'] = [{'field': k, 'then': proposal.before.get(k), 'now': v}
                                    for k, v in preview.before.items() if proposal.before.get(k) != v]
    return out


def apply(proposal):
    """Make the proposal's edit as the host: (ok, the reason it couldn't be made or None)."""
    edit = edit_of(proposal)
    if edit is None:
        return False, 'That kind of change is no longer possible.'
    try:
        edits.apply(current_app._get_current_object(), edit)
    except edits.EditError as error:
        return False, str(error)
    except Exception as error:  # nothing is changed (apply rolls back)
        db.session.rollback()
        return False, f'It could not be applied: {type(error).__name__} {error}'.strip()
    return True, None


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
