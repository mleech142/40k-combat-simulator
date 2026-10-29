"""
Flask application for 40K Combat Simulator.
"""

import os
import secrets
import uuid
from pathlib import Path
from datetime import datetime, timezone
from flask import Flask, render_template, request, jsonify, redirect
from sqlalchemy.exc import IntegrityError
from .models import (db, init_db, Proposal, Faction, Detachment, Enhancement, Unit, RangedWeapon, MeleeWeapon, FactionRule, Stratagem, Keyword, TargetProfile,
                     SavedScenario, SavedComparison, SavedArmy, unit_ranged_weapons, unit_melee_weapons)
from . import __version__
from .paths import DATA_FILE, DATABASE_FILE, BSDATA_FOLDER, BSDATA_CACHE_FILE, WAHAPEDIA_FOLDER, is_hosted, prepare_data_dir
from .data_store import DataFileError, check_data
from . import armies, army_share, bsdata_download, contagion, folder_dialog, match_view, wahapedia_download
from .play.routes import register_play_routes
from .proposals import register_proposal_routes
from . import access, jobs, reference
from . import app_data_edits as edits
from .access import desktop_only, host_only
from .app_data_edits import WEAPON_MODELS, edit_route, text_list
from .engagement import (afflictions, attack_modifiers, enhancement_points, leader_target, load_enhancements, load_leader,
                         selected_rules, sources_for, stat_changes, target_modifiers, unit_target, weapon_attack)
from .accounts import register_account_routes, secret_key
from .wargear import default_used
from .settings import settings_path, load_settings, update_settings
from .simulation import (
    simulate, simulate_analytical, simulate_monte_carlo_combined, simulate_attached_analytical,
    simulate_monte_carlo_attached, keyword_name, check_keywords,
    AttackerProfile, DefenderProfile, BattleConditions
)


def create_app(config=None):
    """Application factory."""
    app = Flask(__name__)

    # Configuration. The app data JSON (custom factions, rules, stratagems, references) and the
    # database default to the folder from paths.data_dir(): the repository's data/ from source,
    # a per-user folder in the packaged exe. Settings, the import cache and "Download latest"
    # sit next to the app data JSON. The BSData folder is the one chosen on the Setup page,
    # else BSDATA_DIR, else the bsdata folder next to the app data JSON. Wahapedia's files
    # (rules and stratagems) are in WAHAPEDIA_DIR, else the wahapedia folder next to it.
    data_json = os.environ.get('DATA_JSON')
    database_url = os.environ.get('DATABASE_URL')
    if not (data_json and database_url):
        folder = prepare_data_dir()
        data_json = data_json or str(folder / DATA_FILE)
        database_url = database_url or 'sqlite:///' + (folder / DATABASE_FILE).as_posix()
    app.config['DATA_JSON'] = data_json
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    app.config['DOWNLOAD_DIR'] = str(Path(data_json).parent / BSDATA_FOLDER)
    app.config['DEFAULT_BSDATA_DIR'] = os.environ.get('BSDATA_DIR') or app.config['DOWNLOAD_DIR']
    app.config['BSDATA_CACHE'] = str(Path(data_json).parent / BSDATA_CACHE_FILE)
    app.config['WAHAPEDIA_DIR'] = os.environ.get('WAHAPEDIA_DIR') or str(Path(data_json).parent / WAHAPEDIA_FOLDER)
    app.config['SETTINGS_JSON'] = str(settings_path(data_json))
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY') or secrets.token_hex(32)
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    # Hosted mode (docs/self-hosting.md): other people's browsers reach this copy, so what only makes sense on
    # your own computer is refused (the folder dialog, BSData folders by path, the relay's address)
    app.config['HOSTED'] = is_hosted()
    if app.config['HOSTED'] and not os.environ.get('SECRET_KEY'):
        app.config['SECRET_KEY'] = secret_key(Path(data_json).parent)  # logins survive restarts

    # New on every start. Pages tag saved page state with it, so state survives
    # switching tabs but is discarded after the app is restarted.
    app.config['BOOT_ID'] = uuid.uuid4().hex

    @app.context_processor
    def inject_page_globals():
        who = access.principal()  # Pages ask who is acting, never whether the copy is self-hosted
        return {'boot_id': app.config['BOOT_ID'], 'app_version': __version__,
                'bsdata_label': reference.label(app),
                'who': who,
                'user': who.user,
                'can_setup': who.is_host,
                'pending_proposals': (Proposal.query.filter_by(status='pending').count()
                                      if who.shared and who.is_host else 0),
                'show_play': bool(who.preferences().get('show_play')),
                'wahapedia_label': reference.wahapedia_label(app)}

    if config:
        app.config.update(config)

    # Initialize database, then rebuild the reference tables from BSData, Wahapedia and the app data JSON
    init_db(app)
    with app.app_context():
        reference.load(app, bsdata=True, wahapedia_too=True)

    # Register routes
    register_routes(app)
    register_play_routes(app)
    register_account_routes(app)
    register_proposal_routes(app)
    access.register(app)  # last: it checks every route is labelled

    return app


def conditions_from_request(conditions_data):
    return BattleConditions(
        half_range=bool(conditions_data.get('half_range', False)),
        charged=bool(conditions_data.get('charged', False)),
        stationary=bool(conditions_data.get('stationary', False)),
        cover=bool(conditions_data.get('cover', False)),
        damaged=bool(conditions_data.get('damaged', False)),
        reroll_hits=conditions_data.get('reroll_hits', 'none'),
        reroll_wounds=conditions_data.get('reroll_wounds', 'none')
    )


def modifier_has_effect(text, modelled_names):
    """True if any entry of a modifier text is a keyword the calculations use."""
    entries = [m for m in (text or '').split(',') if m.strip() and m.strip().lower() != 'none']
    if not modelled_names:
        return bool(entries)
    return any(keyword_name(m).lower() in modelled_names for m in entries)


def weighted_average(values, weights):
    pairs = [(v, w) for v, w in zip(values, weights) if v is not None and w]
    total = sum(w for _, w in pairs)
    return sum(v * w for v, w in pairs) / total if total else 0.0


COVERAGE_TOUGHNESS = [3, 4, 5, 6, 8, 9, 10, 12]
MAX_UNIT_SIZE = 99  # Largest model count the pages' number boxes allow
COVERAGE_SAVES = [2, 3, 4, 5, 6]


def register_routes(app):
    """Register all application routes."""

    @app.route('/')
    def index():
        """Main simulation page (the Setup page until BSData loads or the user skips it)."""
        status = reference.current(app).bsdata_status
        if (not status.get('loaded') and not load_settings(app.config['SETTINGS_JSON']).get('setup_skipped')
                and access.is_host()):
            return redirect('/setup')
        factions = Faction.query.order_by(Faction.name).all()
        factions_list = [{'id': f.id, 'name': f.name} for f in factions]
        return render_template('index.html', factions=factions_list)

    @app.route('/api/units')
    def api_units():
        """Get units, optionally filtered by faction."""
        faction_id = request.args.get('faction_id', type=int)
        search = request.args.get('search', '').strip()

        query = legends_filter(Unit.query)

        if faction_id:
            query = query.filter(Unit.faction_id == faction_id)

        if search:
            query = query.filter(Unit.name.ilike(f'%{search}%'))

        query = query.order_by(Unit.name)
        units = query.all() if faction_id else query.limit(50).all()  # Search results are capped
        return jsonify([u.to_dict() for u in units])

    def legends_filter(query):
        """Legends units only with ?legends=1."""
        if request.args.get('legends') in ('1', 'true'):
            return query
        return query.filter(db.or_(Unit.legends.is_(None), Unit.legends.is_(False)))

    @app.route('/api/units/<int:unit_id>')
    def api_unit(unit_id):
        """Get a specific unit with its weapons."""
        unit = db.get_or_404(Unit, unit_id)
        data = unit.to_dict()
        # qty = how many models in the unit carry the weapon; default_use = ticked by default
        for weapon_type in ('ranged', 'melee'):
            loadout = unit.loadout(weapon_type)
            data[f'{weapon_type}_weapons'] = [
                dict(w.to_dict(), qty=qty, default_use=used)
                for (w, qty), used in zip(loadout, default_used([w.name for w, _ in loadout]))
            ]
        # Leaders that can join this unit (with what they grant and their points)
        data['leaders'] = [dict(id=u.id, name=u.name, leader_buffs=u.effective_leader_buffs,
                                points=u.points_for(u.default_models or 1)) for u in unit.leaders()]
        # Wargear options not in the default loadout (BSData units)
        for weapon_type, model in (('ranged', RangedWeapon), ('melee', MeleeWeapon)):
            ids = getattr(unit, f'{weapon_type}_options') or []
            weapons = {w.id: w for w in model.query.filter(model.id.in_(ids))} if ids else {}
            data[f'{weapon_type}_options'] = [weapons[i].to_dict() for i in ids if i in weapons]
        # Points for every unit size the pages offer (index = model count), from Unit.points_for
        data['points_by_model_count'] = [None] + [unit.points_for(n) for n in range(1, MAX_UNIT_SIZE + 1)]
        return jsonify(data)

    @app.route('/api/rules/available')
    def api_rules_available():
        """
        Rules and stratagems that can be ticked for one side.

        ?side=Attacker|Target, one or more ?faction_id=N and any ?detachment=<BSData ID>.
        Returns the factions' rules and stratagems for that side (those for the whole faction,
        plus those for a chosen detachment) and the core stratagems, each with whether its
        modifier text changes the calculations.
        """
        side = request.args.get('side', 'Attacker')
        faction_ids = [i for i in request.args.getlist('faction_id', type=int) if i]
        chosen = set(request.args.getlist('detachment'))

        def offered(record):
            if record.superseded or (record.source == 'wahapedia' and not record.phase):
                return False  # Replaced by Wahapedia's, or used outside the Shooting and Fight phases
            return (record.applies_to or 'Attacker') == side and (
                not record.detachment_key or record.detachment_key in chosen)
        modelled = {k.name.lower() for k in Keyword.query.filter_by(modelled=True)}
        names = {f.id: f.name for f in Faction.query}

        def entry(record, kind):
            return {
                'key': f'{"rule" if kind == "rule" else "stratagem"}:{record.id}',
                'id': record.id,
                'kind': kind,
                'faction_id': record.faction_id,
                'faction': names.get(record.faction_id),
                'name': record.name,
                'cp': getattr(record, 'cp', None),
                'phase': record.phase or 'Both',
                'modifier': record.modifier,
                'has_effect': modifier_has_effect(record.modifier, modelled),
                'detachment': record.detachment_name,
                'source': record.source,
                'conditions': [] if record.edited else (record.parse or {}).get('conditions') or [],
            }

        entries = []
        if faction_ids:
            rules = FactionRule.query.filter(FactionRule.faction_id.in_(faction_ids)).order_by(FactionRule.name)
            entries += [entry(r, 'rule') for r in rules if offered(r)]
            strats = Stratagem.query.filter(Stratagem.faction_id.in_(faction_ids)).order_by(Stratagem.name)
            entries += [entry(st, 'stratagem') for st in strats if offered(st)]
        core = Stratagem.query.filter(Stratagem.faction_id.is_(None)).order_by(Stratagem.name)
        entries += [entry(st, 'core') for st in core if offered(st)]
        return jsonify(entries)

    @app.route('/api/detachments')
    def api_detachments():
        """BSData detachments of one or more ?faction_id=N, with their rule text."""
        faction_ids = [i for i in request.args.getlist('faction_id', type=int) if i]
        if not faction_ids:
            return jsonify([])
        rows = Detachment.query.filter(Detachment.faction_id.in_(faction_ids)).order_by(Detachment.name)
        return jsonify([d.to_dict() for d in rows])

    @app.route('/api/enhancements')
    def api_enhancements():
        """BSData enhancements of one or more ?faction_id=N, with their points, text and modelled effects."""
        faction_ids = [i for i in request.args.getlist('faction_id', type=int) if i]
        if not faction_ids:
            return jsonify([])
        rows = Enhancement.query.filter(Enhancement.faction_id.in_(faction_ids)).order_by(Enhancement.name)
        return jsonify([e.to_dict() for e in rows])

    def rules_from_keys(keys):
        """{'rule_ids', 'stratagem_ids'} from saved tick keys like 'rule:3' / 'stratagem:7'."""
        selection = {'rule_ids': [], 'stratagem_ids': []}
        for key in keys or []:
            kind, _, ident = str(key).partition(':')
            if ident.isdigit():
                (selection['rule_ids'] if kind == 'rule' else selection['stratagem_ids']).append(int(ident))
        return selection

    @app.route('/api/simulate', methods=['POST'])
    def api_simulate():
        """
        Simulate one or more weapons of one attacking unit against one target.

        Request:
        {
            "attacker": {"faction_id": 1, "weapons": [
                {"name": "Boltgun", "type": "ranged", "attacks": "2", "skill": 3, "strength": 4, "ap": 1,
                 "damage": "1", "abilities": ["Lethal Hits"], "models": 3}, ...]},
            "defender": {"toughness": 4, "save": 3, "wounds": 2, "model_count": 5, "keywords": [...], "faction_id": 2},
            "conditions": {...}, "method": "analytical" | "monte_carlo", "iterations": 1000,
            "attacker_rules": {"rule_ids": [], "stratagem_ids": []}, "target_rules": {...}
        }
        Each side can say it is "afflicted" (Death Guard's Nurgle's Gift, contagion.py: "none" for no Plague,
        or a Plague's key): an Afflicted defender has -1 Toughness, its Plague's effect applies, and attacks
        with bonuses against Afflicted units get them. "attacker" can name its "unit_id" (its datasheet's
        own abilities then count).

        "attacker" and "defender" can name a "leader_id": a leader attached to that unit, whose
        buffs apply to its side (the leader's own weapons are sent as weapons like the others). A
        defender with a leader is an attached unit: attacks go to the bodyguard models, then to the
        leader with its own profile, and the results give bodyguards_killed and leader_killed.
        A single weapon's fields directly in "attacker" (older saved scenarios) also work.

        Per-weapon rows are averages, as in Excel; the totals use the chosen method,
        with Monte Carlo rolling all the weapons together in each trial.
        """
        data = request.get_json()
        attacker_data = data['attacker']
        defender_data = data['defender']
        weapons_data = attacker_data.get('weapons') or [attacker_data]

        attacker_rules = selected_rules(data.get('attacker_rules') or rules_from_keys(attacker_data.get('rules')))
        target_rules = selected_rules(data.get('target_rules') or rules_from_keys(defender_data.get('rules')))
        conditions = conditions_from_request(data.get('conditions', {}))
        method = data.get('method', 'analytical')
        iterations = min(int(data.get('iterations', 1000)), 100000)

        # One-off target modifiers for this defender (never saved to the datasheet)
        extra_modifiers = text_list(defender_data.get('extra_modifiers'))
        attacker_leader = load_leader(attacker_data.get('leader_id'))
        defender_leader = load_leader(defender_data.get('leader_id'))
        attacker_enhancements = load_enhancements(attacker_data.get('enhancements'))
        defender_enhancements = load_enhancements(defender_data.get('enhancements'))
        attacker_unit = load_leader(attacker_data.get('unit_id'))
        attacker_affliction = contagion.affliction(attacker_data.get('afflicted'))
        target_affliction = contagion.affliction(defender_data.get('afflicted'))
        attacker_side = sources_for(attacker_unit, attacker_leader, attacker_enhancements, attacker_rules, attacker_affliction)
        target_side = sources_for(None, defender_leader, defender_enhancements, target_rules, target_affliction)

        def target_profile(extra_keywords=()):
            return DefenderProfile(
                toughness=int(defender_data['toughness']),
                save=int(defender_data['save']),
                wounds=int(defender_data['wounds']),
                invulnerable_save=defender_data.get('invulnerable_save'),
                feel_no_pain=defender_data.get('feel_no_pain'),
                keywords=list(defender_data.get('keywords', [])) + extra_modifiers + list(extra_keywords),
                model_count=int(defender_data.get('model_count', 1))
            )

        profiles, targets, leader_targets, rows = [], [], [], []
        applied = {'attacker': [], 'target': []}
        for w in weapons_data:
            w_type = 'melee' if w.get('type') == 'melee' else 'ranged'
            # Ticked rules and stratagems add modifier text to the weapon (attacker) or the target
            attacker_modifiers = attack_modifiers(attacker_side, target_affliction, w_type, w.get('leader'),
                                                  w.get('faction_id', attacker_data.get('faction_id')))
            weapon_target_modifiers = target_modifiers(target_side, attacker_affliction, w_type, defender_data.get('faction_id'))
            for side, side_modifiers in (('attacker', attacker_modifiers), ('target', weapon_target_modifiers)):
                applied[side] += [m for m in side_modifiers if m not in applied[side]]

            skill = w.get('skill')
            profile = AttackerProfile(
                attacks=str(w['attacks']),
                skill=int(skill) if skill not in (None, '', '-') else None,
                strength=int(w['strength']),
                ap=int(w['ap']),
                damage=str(w['damage']),
                abilities=list(w.get('abilities', [])) + attacker_modifiers,
                weapon_type=w_type,
                models=max(1, int(w.get('models') or 1)),
            )
            target = target_profile(weapon_target_modifiers)
            profiles.append(profile)
            targets.append(target)
            if defender_leader is not None:
                leader_targets.append(leader_target(defender_leader, extra_modifiers + weapon_target_modifiers))

            r = simulate_analytical(profile, target, conditions)
            rows.append({
                'name': w.get('name') or w.get('weapon_name') or 'Weapon',
                'type': w_type,
                'models': profile.models,
                'attacks': r.attacks_made,
                'hits': r.hits,
                'auto_wounds': r.auto_wounds,
                'wounds': r.wounds,
                'mortal_wounds': r.mortal_wounds,
                'unsaved': r.unsaved,
                'damage_per_attack': r.damage_per_attack,
                'total_damage': r.average_damage,
                'models_killed': r.models_killed,
                'overkill': r.overkill,
            })

        if not profiles:
            return jsonify({'error': 'No weapons to simulate'}), 400

        base_target = target_profile()
        attached = None
        if defender_leader is not None:
            leader_base = leader_target(defender_leader, extra_modifiers)
            attached = {'leader': defender_leader.name, 'leader_models': leader_base.model_count,
                        'leader_wounds': leader_base.wounds * leader_base.model_count,
                        'bodyguard_models': base_target.model_count}
            combined, per_weapon = simulate_attached_analytical(profiles, base_target, leader_base, conditions,
                                                                bodyguard_targets=targets, leader_targets=leader_targets)
            for row, r in zip(rows, per_weapon):  # Each weapon in order against what is left of the unit
                row.update(attacks=r.attacks_made, hits=r.hits, auto_wounds=r.auto_wounds, wounds=r.wounds,
                           mortal_wounds=r.mortal_wounds, unsaved=r.unsaved, total_damage=r.average_damage,
                           models_killed=r.models_killed, overkill=r.overkill,
                           bodyguards_killed=r.bodyguards_killed, leader_killed=r.leader_killed,
                           leader_damage=r.leader_damage)

        if method == 'monte_carlo' and attached:
            result = simulate_monte_carlo_attached(profiles, base_target, leader_base, conditions, iterations,
                                                   bodyguard_targets=targets, leader_targets=leader_targets)
            summary = {
                'average_damage': result.average_damage,
                'models_killed': result.models_killed,
                'overkill': result.overkill,
                'kill_probability': result.kill_probability,
                'wipe_probability': result.wipe_probability,
                'bodyguards_killed': result.bodyguards_killed,
                'leader_killed': result.leader_killed,
                'leader_damage': result.leader_damage,
                'leader_kill_probability': result.leader_kill_probability,
            }
            breakdown = {
                'attacks': result.attacks_made,
                'hits': result.hits,
                'wounds': result.wounds,
                'failed_saves': result.failed_saves,
                'damage': result.damage_dealt,
            }
            distribution, iterations_run = result.damage_distribution, result.iterations
        elif method == 'monte_carlo':
            result = simulate_monte_carlo_combined(profiles, base_target, conditions, iterations, weapon_defenders=targets)
            summary = {
                'average_damage': result.average_damage,
                'models_killed': result.models_killed,
                'overkill': result.overkill,
                'kill_probability': result.kill_probability,
                'wipe_probability': result.wipe_probability,
            }
            breakdown = {
                'attacks': result.attacks_made,
                'hits': result.hits,
                'wounds': result.wounds,
                'failed_saves': result.failed_saves,
                'damage': result.damage_dealt,
            }
            distribution, iterations_run = result.damage_distribution, result.iterations
        else:
            total = sum(row['total_damage'] for row in rows)
            summary = {
                'average_damage': round(total, 2),
                # As in Excel: the weapons' kills add up, capped at the unit size
                'models_killed': round(min(sum(row['models_killed'] for row in rows), base_target.model_count), 2),
                'overkill': round(sum(row['overkill'] for row in rows), 2),
            }
            if attached:
                summary.update(models_killed=combined.models_killed, bodyguards_killed=combined.bodyguards_killed,
                               leader_killed=combined.leader_killed, leader_damage=combined.leader_damage)
            breakdown = {
                'attacks': round(sum(row['attacks'] for row in rows), 2),
                'hits': round(sum(row['hits'] for row in rows), 2),
                'wounds': round(sum(row['wounds'] for row in rows), 2),
                'failed_saves': round(sum(row['unsaved'] + row['mortal_wounds'] for row in rows), 2),
                'damage': round(total, 2),
            }
            distribution, iterations_run = None, 0

        return jsonify({
            'weapons': rows,
            'summary': summary,
            'breakdown': breakdown,
            'distribution': distribution,
            'method': method,
            'iterations': iterations_run,
            'applied': applied,
            'attached': attached,
        })

    @app.route('/api/scenarios', methods=['POST'])
    def api_save_scenario():
        """Save a new scenario."""
        data = request.get_json()

        scenario = SavedScenario(
            name=data['name'],
            attacker_config=data['attacker'],
            defender_config=data['defender'],
            battle_conditions=data['conditions'],
            results=data.get('results'),
            owner_id=access.stamp(),
        )
        db.session.add(scenario)
        db.session.commit()

        return jsonify({'id': scenario.id, 'message': 'Scenario saved'})

    @app.route('/api/scenarios/<int:scenario_id>', methods=['DELETE'])
    def api_delete_scenario(scenario_id):
        """Delete a scenario."""
        scenario = access.get(SavedScenario, scenario_id, write=True)
        db.session.delete(scenario)
        db.session.commit()
        return jsonify({'message': 'Scenario deleted'})

    # =========================================================================
    # Page Routes
    # =========================================================================

    @app.route('/data')
    def data_page():
        """Data management page."""
        return render_template('data.html')

    @app.route('/scenarios')
    def scenarios_page():
        """Saved scenarios page."""
        return render_template('scenarios.html')

    # =========================================================================
    # Data Management API
    #
    # Every change is committed to the database and then written straight to the app
    # data JSON (see data_store.py). BSData factions' units and weapons are read-only;
    # their rules and stratagems, and modifiers added to their units, are app data.
    # =========================================================================

    def save_data():
        reference.save_app_data(app)  # never while the reference tables are being rebuilt

    def run_edit(edit):
        """A Data-tab change (app_data_edits.py) as a web answer."""
        try:
            return jsonify(edits.apply(app, edit))
        except edits.EditError as error:
            return jsonify({'error': str(error)}), error.status

    @app.route('/api/data/status')
    def api_data_status():
        """The BSData folder and import: where, whether it loaded, revisions and report."""
        status = reference.current(app).bsdata_status
        report = status.get('report') or []
        counts = {}
        for item in report:
            counts[item['kind']] = counts.get(item['kind'], 0) + 1
        return jsonify(without_paths({
            'folder': status.get('folder'),
            'loaded': bool(status.get('loaded')),
            'error': status.get('error'),
            'from_cache': bool(status.get('from_cache')),
            'source': status.get('source'),
            'report_counts': counts,
            'report': report if request.args.get('report') else None,
            'factions': Faction.query.filter_by(source='bsdata').count(),
            'units': Unit.query.join(Faction).filter(Faction.source == 'bsdata').count(),
            'label': reference.label(app),
            'download': load_settings(app.config['SETTINGS_JSON']).get('download'),
            'download_folder': app.config['DOWNLOAD_DIR'],
            'hosted': not access.principal().local,  # No folder dialog or paths
            'default_folder': app.config['DEFAULT_BSDATA_DIR'],
            'wahapedia': wahapedia_status(bool(request.args.get('report'))),
        }))

    PATH_KEYS = ('folder', 'download_folder', 'default_folder')

    def without_paths(status):
        """A self-hosted copy shows where its files are on the server to the host alone."""
        if access.is_host():
            return status
        def strip(d):
            return {k: (strip(v) if isinstance(v, dict) else v) for k, v in d.items() if k not in PATH_KEYS}
        return strip(status)

    def wahapedia_status(with_report=False):
        """The Wahapedia import: whether it loaded, when Wahapedia last updated it, counts and report."""
        status = reference.current(app).wahapedia_status
        report = status.get('report') or []
        counts = {}
        for item in report:
            counts[item['kind']] = counts.get(item['kind'], 0) + 1
        # Each Wahapedia record once (it is copied into every BSData faction that has it)
        parsed, unique = {}, {}
        for kind, model in (('stratagems', Stratagem), ('rules', FactionRule)):
            records = dict(db.session.query(model.wahapedia_id, model.parse).filter(model.source == 'wahapedia'))
            unique[kind] = len(records)
            for parse in records.values():
                key = (parse or {}).get('status') or 'not_modelled'
                parsed[key] = parsed.get(key, 0) + 1
        return {
            'folder': status.get('folder'),
            'loaded': bool(status.get('loaded')),
            'error': status.get('error'),
            'last_update': (status.get('source') or {}).get('last_update'),
            'stratagems': unique['stratagems'],
            'rules': unique['rules'],
            'parsed': parsed,
            'report_counts': counts,
            'report': report if with_report else None,
            'download': load_settings(app.config['SETTINGS_JSON']).get('wahapedia_download'),
        }

    # --- Long Setup work runs as a job (jobs.py): the answer is the data status when it finished at once, else
    # 202 with the job for the page to poll (/api/jobs/<id>)

    def job_answer(job):
        if not job.finished:
            return jsonify({'job': job.to_dict()}), 202
        if job.state == jobs.FAILED:
            return jsonify({'error': job.error, 'job': job.to_dict()}), job.status or 500
        return api_data_status()

    @app.route('/api/jobs/<int:job_id>')
    @host_only
    def api_job(job_id):
        """A Setup job's progress: {'id', 'kind', 'state' (running, done, failed), 'step', 'error', 'seconds'}."""
        job = jobs.of(app).get(job_id)
        if job is None:
            return jsonify({'error': 'That job is no longer known (the app may have restarted).'}), 404
        return jsonify(job.to_dict())

    def wahapedia_job(job):
        job.step = 'Downloading from Wahapedia'
        try:
            info = wahapedia_download.download(app.config['WAHAPEDIA_DIR'])
        except wahapedia_download.DownloadError as err:
            raise jobs.JobError(str(err), 502) from err
        update_settings(app.config['SETTINGS_JSON'], wahapedia_download=dict(
            info, downloaded_at=datetime.now(timezone.utc).isoformat(timespec='seconds')))
        job.step = 'Reading the rules and stratagems'
        status = reference.load(app, wahapedia_too=True).wahapedia_status
        if status.get('error'):
            raise jobs.JobError(status['error'], 400)

    @app.route('/api/wahapedia/download', methods=['POST'])
    @host_only
    def api_wahapedia_download():
        """Download the latest rules and stratagems from Wahapedia and use them (a job)."""
        return job_answer(jobs.of(app).submit('wahapedia', wahapedia_job))

    # --- Setup: choosing, downloading and re-importing the BSData folder -------------

    def hosted_refusal(what):
        return jsonify({'error': f'{what} is turned off on a self-hosted copy (hosted mode).'}), 403

    @app.route('/setup')
    @host_only
    def setup_page():
        return render_template('setup.html')

    @app.route('/api/bsdata/folder', methods=['POST'])
    @host_only
    def api_bsdata_folder():
        """Use the BSData folder {'path'} (null = back to the default), if it loads."""
        path = (request.get_json() or {}).get('path')
        if path and not access.principal().local:
            return hosted_refusal('Choosing a BSData folder by path')
        settings_file = app.config['SETTINGS_JSON']
        previous = load_settings(settings_file).get('bsdata_dir')
        if path:
            folder = Path(str(path).strip()).expanduser()
            if not folder.is_dir():
                return jsonify({'error': f'{folder} is not a folder.'}), 400
            if not any(folder.glob('*.json')):
                return jsonify({'error': f'{folder} holds no .json files. Choose the folder with the BSData '
                                         'catalogues (e.g. "Warhammer 40,000.json").'}), 400
            update_settings(settings_file, bsdata_dir=str(folder.resolve()))
        else:
            update_settings(settings_file, bsdata_dir=None)
        status = reference.load(app, bsdata=True).bsdata_status
        if status.get('error'):  # Keep the folder that worked
            update_settings(settings_file, bsdata_dir=previous)
            reference.load(app, bsdata=True)
            return jsonify({'error': status['error']}), 400
        return api_data_status()

    @app.route('/api/bsdata/browse', methods=['POST'])
    @desktop_only
    def api_bsdata_browse():
        """Open the computer's folder dialog; {'path'} or {'path': null} if cancelled or unavailable."""
        return jsonify({'path': folder_dialog.choose_folder(initial=reference.current(app).bsdata_dir)})

    def bsdata_download_job(job):
        job.step = 'Finding the latest catalogues on GitHub'
        try:
            commit = bsdata_download.latest_commit()
            job.step = 'Downloading the catalogues'
            count = bsdata_download.install_zip(bsdata_download.download_zip(commit['sha']), app.config['DOWNLOAD_DIR'])
        except bsdata_download.DownloadError as err:
            raise jobs.JobError(str(err), 502) from err
        update_settings(app.config['SETTINGS_JSON'], bsdata_dir=app.config['DOWNLOAD_DIR'], download={
            'sha': commit['sha'], 'date': commit['date'], 'files': count, 'folder': app.config['DOWNLOAD_DIR'],
            'downloaded_at': datetime.now(timezone.utc).isoformat(timespec='seconds')})
        job.step = 'Importing the catalogues'
        status = reference.load(app, bsdata='fresh').bsdata_status
        if status.get('error'):
            raise jobs.JobError(status['error'], 400)

    def bsdata_reimport_job(job):
        job.step = 'Importing the catalogues'
        reference.load(app, bsdata='fresh')

    @app.route('/api/bsdata/download', methods=['POST'])
    @host_only
    def api_bsdata_download():
        """Download the latest catalogues from GitHub into the download folder and use it (a job)."""
        return job_answer(jobs.of(app).submit('bsdata', bsdata_download_job))

    @app.route('/api/bsdata/reimport', methods=['POST'])
    @host_only
    def api_bsdata_reimport():
        """Import the BSData folder again (after changing its files), ignoring the cache (a job)."""
        return job_answer(jobs.of(app).submit('bsdata', bsdata_reimport_job))

    @app.route('/api/bsdata/skip-setup', methods=['POST'])
    @host_only
    def api_bsdata_skip_setup():
        """Use the app without BSData for now: the Simulator stops redirecting to Setup."""
        update_settings(app.config['SETTINGS_JSON'], setup_skipped=True)
        return jsonify({'message': 'Setup skipped'})

    def bad_data_request(err):
        """A Data-tab write with a field missing or of the wrong kind: 400, not a crash."""
        if not request.path.startswith('/api/data/'):
            raise err
        db.session.rollback()
        detail = f"'{err.args[0]}' is missing" if isinstance(err, KeyError) and err.args else str(err)
        return jsonify({'error': f'That change has a field missing or wrong: {detail}.'}), 400

    for kind in (KeyError, ValueError, TypeError):
        app.register_error_handler(kind, bad_data_request)

    @app.errorhandler(IntegrityError)
    def handle_integrity_error(err):
        db.session.rollback()
        return jsonify({'error': 'That would create a duplicate or break a link between records.'}), 400

    @app.route('/api/data/export')
    def api_data_export():
        """The current app data JSON (BSData records are left out)."""
        return jsonify(reference.export(app))

    @app.route('/api/data/import', methods=['POST'])
    @host_only
    def api_data_import():
        """Replace all app data with an uploaded app data JSON (BSData stays as loaded)."""
        data = request.get_json()
        try:
            check_data(data)
        except DataFileError as err:
            return jsonify({'error': str(err)}), 400
        reference.load(app, data=data)
        save_data()
        return jsonify({'message': 'Data imported'})

    @app.route('/api/data/target-profiles')
    def api_data_target_profiles():
        """Generic opponent profiles and role benchmarks (read-only; from the workbook)."""
        profiles = TargetProfile.query.order_by(TargetProfile.kind, TargetProfile.id).all()
        return jsonify([t.to_dict() for t in profiles])

    @app.route('/api/keywords/check', methods=['POST'])
    def api_keywords_check():
        """As-you-type keyword hints: {'text', 'mode'} -> {'unknown': [...], 'capitals': [[entry, name]]}."""
        data = request.get_json() or {}
        names = [k.name for k in Keyword.query]
        if not names:
            return jsonify({'unknown': [], 'capitals': []})  # no keyword reference loaded
        return jsonify(check_keywords(data.get('text', ''), names, data.get('mode', '')))

    @app.route('/api/data/keywords')
    def api_data_keywords():
        """The keywords reference (read-only)."""
        return jsonify([k.to_dict() for k in Keyword.query.order_by(Keyword.id).all()])

    # --- Factions -----------------------------------------------------------

    @app.route('/api/data/factions')
    def api_data_factions():
        """Get all factions with counts."""
        factions = Faction.query.order_by(Faction.name).all()
        unit_counts = dict(db.session.query(Unit.faction_id, db.func.count(Unit.id)).group_by(Unit.faction_id))
        return jsonify([{
            'id': f.id,
            'name': f.name,
            'source': f.source,
            'editable': f.editable,
            'unit_count': unit_counts.get(f.id, 0),
            'rule_count': f.faction_rules.filter_by(superseded=False).count(),
            'stratagem_count': f.stratagems.filter_by(superseded=False).count(),
        } for f in factions])

    @app.route('/api/data/factions/<int:faction_id>/reference')
    def api_data_faction_reference(faction_id):
        """BSData's text for a faction: army rules and detachments (read-only reference)."""
        faction = db.get_or_404(Faction, faction_id)
        detachments = Detachment.query.filter_by(faction_id=faction.id).order_by(Detachment.name)
        return jsonify({'army_rules': faction.army_rules or [], 'detachments': [d.to_dict() for d in detachments]})

    @app.route('/api/data/factions', methods=['POST'])
    @edit_route('faction', 'add')
    def api_data_create_faction():
        """Create a new (custom) faction."""
        return run_edit(edits.make('faction', 'add', fields=request.get_json(silent=True)))

    @app.route('/api/data/factions/<int:faction_id>', methods=['PUT'])
    @edit_route('faction', 'change')
    def api_data_update_faction(faction_id):
        """Rename a custom faction."""
        return run_edit(edits.make('faction', 'change', faction_id, fields=request.get_json(silent=True)))

    @app.route('/api/data/factions/<int:faction_id>', methods=['DELETE'])
    @edit_route('faction', 'delete')
    def api_data_delete_faction(faction_id):
        """Delete a custom faction with its units, weapons, rules and stratagems."""
        return run_edit(edits.make('faction', 'delete', faction_id, fields=request.get_json(silent=True)))

    # --- Faction rules ------------------------------------------------------

    @app.route('/api/data/rules')
    def api_data_rules():
        """Faction rules, optionally for one faction."""
        query = FactionRule.query.filter_by(superseded=False)
        if request.args.get('faction_id', type=int):
            query = query.filter_by(faction_id=request.args.get('faction_id', type=int))
        return jsonify([r.to_dict() for r in query.order_by(FactionRule.name).all()])

    @app.route('/api/data/rules', methods=['POST'])
    @edit_route('rule', 'add')
    def api_data_create_rule():
        """Add a faction rule."""
        return run_edit(edits.make('rule', 'add', fields=request.get_json(silent=True)))

    @app.route('/api/data/rules/<int:rule_id>', methods=['PUT'])
    @edit_route('rule', 'change')
    def api_data_update_rule(rule_id):
        """Change a rule (a Wahapedia one: how the maths reads it)."""
        return run_edit(edits.make('rule', 'change', rule_id, fields=request.get_json(silent=True)))

    @app.route('/api/data/rules/<int:rule_id>', methods=['DELETE'])
    @edit_route('rule', 'delete')
    def api_data_delete_rule(rule_id):
        """Delete a rule (not a Wahapedia one)."""
        return run_edit(edits.make('rule', 'delete', rule_id, fields=request.get_json(silent=True)))

    # --- Stratagems (core when faction_id is null) --------------------------

    @app.route('/api/data/stratagems')
    def api_data_stratagems():
        """Stratagems: ?core=1 for core ones, ?faction_id=N for a faction's, otherwise all."""
        query = Stratagem.query.filter_by(superseded=False)
        if request.args.get('core'):
            query = query.filter(Stratagem.faction_id.is_(None))
        elif request.args.get('faction_id', type=int):
            query = query.filter_by(faction_id=request.args.get('faction_id', type=int))
        return jsonify([s.to_dict() for s in query.order_by(Stratagem.name).all()])

    @app.route('/api/data/stratagems', methods=['POST'])
    @edit_route('stratagem', 'add')
    def api_data_create_stratagem():
        """Add a stratagem (core when faction_id is null)."""
        return run_edit(edits.make('stratagem', 'add', fields=request.get_json(silent=True)))

    @app.route('/api/data/stratagems/<int:strat_id>', methods=['PUT'])
    @edit_route('stratagem', 'change')
    def api_data_update_stratagem(strat_id):
        """Change a stratagem (a Wahapedia one: how the maths reads it)."""
        return run_edit(edits.make('stratagem', 'change', strat_id, fields=request.get_json(silent=True)))

    @app.route('/api/data/stratagems/<int:strat_id>', methods=['DELETE'])
    @edit_route('stratagem', 'delete')
    def api_data_delete_stratagem(strat_id):
        """Delete a stratagem (not a Wahapedia one)."""
        return run_edit(edits.make('stratagem', 'delete', strat_id, fields=request.get_json(silent=True)))

    # --- Units --------------------------------------------------------------

    @app.route('/api/data/units')
    def api_data_units():
        """Get all units with faction info and loadout counts."""
        faction_id = request.args.get('faction_id', type=int)
        query = legends_filter(Unit.query.join(Faction))

        if faction_id:
            query = query.filter(Unit.faction_id == faction_id)

        units = query.order_by(Faction.name, Unit.name).all()
        return jsonify([dict(
            u.to_dict(),
            ranged_weapon_count=len(u.ranged_weapons),
            melee_weapon_count=len(u.melee_weapons),
        ) for u in units])

    @app.route('/api/data/units', methods=['POST'])
    @edit_route('unit', 'add')
    def api_data_create_unit():
        """Create a new unit in a custom faction."""
        return run_edit(edits.make('unit', 'add', fields=request.get_json(silent=True)))

    @app.route('/api/data/units/<int:unit_id>', methods=['PUT'])
    @edit_route('unit', 'change')
    def api_data_update_unit(unit_id):
        """Update a custom unit and its loadout; a BSData unit only takes 'added_modifiers' and 'leader_buffs'."""
        return run_edit(edits.make('unit', 'change', unit_id, fields=request.get_json(silent=True)))

    @app.route('/api/data/units/<int:unit_id>', methods=['DELETE'])
    @edit_route('unit', 'delete')
    def api_data_delete_unit(unit_id):
        """Delete a custom unit. Its faction's weapons stay."""
        return run_edit(edits.make('unit', 'delete', unit_id, fields=request.get_json(silent=True)))

    # --- Weapons ------------------------------------------------------------

    def weapon_dict(weapon, unit_names=None):
        return dict(
            weapon.to_dict(),
            faction=weapon.faction.name if weapon.faction else None,
            unit_names=sorted(u.name for u in weapon.units) if unit_names is None else sorted(unit_names),
        )

    @app.route('/api/data/weapons')
    def api_data_weapons():
        """Weapons, filtered by ?type=ranged|melee|all and optional ?faction_id=."""
        weapon_type = request.args.get('type', 'all')
        faction_id = request.args.get('faction_id', type=int)
        weapons = []
        for type_name, model in WEAPON_MODELS.items():
            if weapon_type not in ('all', type_name):
                continue
            query = model.query
            if faction_id:
                query = query.filter(model.faction_id == faction_id)
            # Units carrying each weapon, in one query (a faction can have hundreds of weapons)
            table = unit_ranged_weapons if type_name == 'ranged' else unit_melee_weapons
            users = {}
            rows = db.session.query(table.c.weapon_id, Unit.name).join(Unit, Unit.id == table.c.unit_id)
            if faction_id:
                rows = rows.filter(Unit.faction_id == faction_id)
            for weapon_id, unit_name in rows:
                users.setdefault(weapon_id, set()).add(unit_name)
            weapons.extend(weapon_dict(w, users.get(w.id, ())) for w in query.order_by(model.name).all())
        weapons.sort(key=lambda w: ((w['faction'] or '').lower(), w['name'].lower()))
        return jsonify(weapons)

    @app.route('/api/data/weapons/bsdata')
    def api_data_bsdata_weapons():
        """BSData weapons to copy into a custom faction: ?q=name&type=ranged|melee (one per profile, 40 at most)."""
        search = request.args.get('q', '').strip()
        types = [request.args.get('type')] if request.args.get('type') in WEAPON_MODELS else list(WEAPON_MODELS)
        found, seen = [], set()
        for type_name in types:
            model = WEAPON_MODELS[type_name]
            query = model.query.join(Faction).filter(Faction.source == 'bsdata')
            if search:
                query = query.filter(model.name.ilike(f'%{search}%'))
            for w in query.order_by(model.name, model.id).limit(400):
                if (type_name, w.bsdata_id) in seen:
                    continue
                seen.add((type_name, w.bsdata_id))
                found.append(dict(w.to_dict(), faction=w.faction.name))
        found.sort(key=lambda w: w['name'].lower())
        return jsonify(found[:40])

    @app.route('/api/data/weapons', methods=['POST'])
    @edit_route('weapon', 'add')
    def api_data_create_weapon():
        """Create a new weapon for a custom faction."""
        return run_edit(edits.make('weapon', 'add', fields=request.get_json(silent=True)))

    @app.route('/api/data/weapons/<weapon_type>/<int:weapon_id>')
    def api_data_get_weapon(weapon_type, weapon_id):
        """Get a specific weapon."""
        model = WEAPON_MODELS.get(weapon_type, MeleeWeapon)
        return jsonify(weapon_dict(db.get_or_404(model, weapon_id)))

    @app.route('/api/data/weapons/<weapon_type>/<int:weapon_id>', methods=['PUT'])
    @edit_route('weapon', 'change')
    def api_data_update_weapon(weapon_type, weapon_id):
        """Update a weapon. Moving it to another faction drops it from other factions' units."""
        return run_edit(edits.make('weapon', 'change', weapon_id, fields=request.get_json(silent=True), weapon_type=weapon_type))

    @app.route('/api/data/weapons/<weapon_type>/<int:weapon_id>', methods=['DELETE'])
    @edit_route('weapon', 'delete')
    def api_data_delete_weapon(weapon_type, weapon_id):
        """Delete a weapon and remove it from every loadout."""
        return run_edit(edits.make('weapon', 'delete', weapon_id, fields=request.get_json(silent=True), weapon_type=weapon_type))

    @app.route('/api/scenarios/<int:scenario_id>', methods=['PUT'])
    def api_update_scenario(scenario_id):
        """Update a scenario."""
        scenario = access.get(SavedScenario, scenario_id, write=True)
        data = request.get_json()

        if 'name' in data:
            scenario.name = data['name']
        if 'results' in data:
            scenario.results = data['results']
        if 'attacker' in data:
            scenario.attacker_config = data['attacker']
        if 'defender' in data:
            scenario.defender_config = data['defender']
        if 'conditions' in data:
            scenario.battle_conditions = data['conditions']

        db.session.commit()
        return jsonify({'id': scenario.id, 'message': 'Scenario updated'})

    @app.route('/api/scenarios')
    def api_get_scenarios():
        """Get all scenarios with full config."""
        scenarios = access.listed(SavedScenario, SavedScenario.query.order_by(SavedScenario.created_at.desc()))
        return jsonify([{
            'id': s.id,
            'name': s.name,
            'created_at': s.created_at.isoformat(),
            'attacker_config': s.attacker_config,
            'defender_config': s.defender_config,
            'battle_conditions': s.battle_conditions,
            'results': s.results,
        } for s in scenarios])

    # =========================================================================
    # Analytics (matrix comparison and Army Analysis) routes; the page was called Matchups
    # =========================================================================

    @app.route('/matchups')
    def matchups_page():
        """The page's old address."""
        return redirect('/analytics', code=301)

    @app.route('/analytics')
    def analytics_page():
        """Matrix comparison page for army analysis."""
        factions = Faction.query.order_by(Faction.name).all()
        factions_list = [{'id': f.id, 'name': f.name} for f in factions]
        return render_template('analytics.html', factions=factions_list, afflictions=contagion.choices())

    # Units without a points cost count as this many points per unit (placeholder until costs are entered)
    DEFAULT_UNIT_POINTS = 1

    def load_defender(config):
        """A real unit ({'unit_id'}) or a generic target profile ({'profile_id'})."""
        if config.get('profile_id'):
            return db.session.get(TargetProfile, int(config['profile_id']))
        if config.get('unit_id'):
            return db.session.get(Unit, int(config['unit_id']))
        return None

    def combine_cells(parts, unit, model_count, points):
        """
        Matrix metrics for weapons (or units) attacking together. `parts` are
        (damage, models killed, damage lost to the cap). As in Excel, kills add up and are
        capped at the unit size; `kills_uncapped` keeps the plain sum (for "% destroyed").
        """
        damage = sum(part[0] for part in parts)
        kills = sum(part[1] for part in parts)
        return {
            'avg_damage': round(damage, 2),
            'models_killed': round(min(kills, model_count), 2),
            'kills_uncapped': round(kills, 2),
            'overkill': round(sum(part[2] for part in parts), 2),
            'damage_per_point': round(damage / points, 3) if points else round(damage, 3),
            # Rough estimate; the cell detail panel gives the simulated figure
            'kill_probability': round(min(1.0, damage / unit.wounds) * 100, 1) if unit.wounds else 0,
        }

    @app.route('/api/matrix/simulate', methods=['POST'])
    def api_matrix_simulate():
        """
        Run the attacker-unit x defender matrix.

        Request body:
        {
            "attackers": [
                {"label": "Intercessors", "unit_id": 3, "weapons": [
                    {"weapon_id": 1, "weapon_type": "ranged", "models": 9, "label": "Bolt rifle"},
                    {"weapon_id": 2, "weapon_type": "ranged", "models": 1, "label": "Plasma gun"}
                ]}
            ],
            "defenders": [
                {"unit_id": 1, "model_count": 5, "label": "Hellblasters (x5)"}
            ],
            "conditions": {"half_range": false, "charged": false, ...}
        }

        Each attacker unit's row adds up its weapons' average damage. A per-weapon
        breakdown and an "All attackers" total row are returned alongside. An attacker or
        defender can name a "leader_id": its buffs apply to that unit, and an attacker's leader
        adds its points (its weapons come in the weapons list). "afflicted": {"attackers", "defenders"}
        says either side is Afflicted (contagion.py; as in /api/simulate).
        """
        data = request.get_json()
        conditions = conditions_from_request(data.get('conditions', {}))
        attacker_affliction, defender_affliction = afflictions(data)

        # Army-wide rules and stratagems ticked for each side
        attacker_rules = selected_rules(data.get('attacker_rules'))
        defender_rules = selected_rules(data.get('defender_rules'))

        # Load attacker units and their included weapons
        attackers = []
        for atk in data.get('attackers', []):
            atk_unit = db.session.get(Unit, atk['unit_id']) if atk.get('unit_id') else None
            atk_leader = load_leader(atk.get('leader_id'))
            atk_enhancements = load_enhancements(atk.get('enhancements'))
            atk_side = sources_for(atk_unit, atk_leader, atk_enhancements, attacker_rules, attacker_affliction)
            weapons = []
            for w in atk.get('weapons', []):
                w_type = 'melee' if w.get('weapon_type') == 'melee' else 'ranged'
                weapon_modifiers = attack_modifiers(atk_side, defender_affliction, w_type, w.get('leader'),
                                                    atk_unit.faction_id if atk_unit else None)
                weapon, profile = weapon_attack(w_type, w.get('weapon_id'), w.get('models', 1), weapon_modifiers)
                if profile:
                    models = profile.models
                    weapons.append({
                        'profile': profile,
                        'label': w.get('label') or weapon.name,
                        'type': w_type,
                        'models': models,
                    })
            # Points for the chosen unit size (datasheet size if not given)
            unit_size = int(atk.get('unit_size') or (atk_unit.default_models if atk_unit else 0) or 0)
            points = atk_unit.points_for(unit_size) if atk_unit else None
            if atk_leader is not None and points is not None:
                points += atk_leader.points_for(atk_leader.default_models or 1) or 0
            if points is not None:
                points += enhancement_points(atk_enhancements)

            # Kept even if its weapons were deleted, so rows stay aligned with the request
            attackers.append({
                'label': atk.get('label') or (atk_unit.name if atk_unit else 'Unknown'),
                'points': points or DEFAULT_UNIT_POINTS,
                'weapons': weapons,
                'unit': atk_unit,
                'leader': atk_leader,
                'enhancements': atk_enhancements,
                'size': unit_size or 1,
                'stat_changes': stat_changes(atk.get('stat_changes')),
                'leader_stat_changes': stat_changes(atk.get('leader_stat_changes')),
            })

        # Load defenders
        defenders = []
        for def_config in data.get('defenders', []):
            unit = load_defender(def_config)
            if unit:
                default_models = unit.models if isinstance(unit, TargetProfile) else 1
                model_count = max(1, int(def_config.get('model_count') or default_models))
                defenders.append({
                    'unit': unit,
                    'model_count': model_count,
                    'label': def_config.get('label') or f"{unit.name} (x{model_count})",
                    'points': unit.points_for(model_count),
                    'weight': unit.weight if isinstance(unit, TargetProfile) else None,
                    # One-off target modifiers for this defender only (real units; never saved to the datasheet)
                    'extra_modifiers': text_list(def_config.get('extra_modifiers')) if isinstance(unit, Unit) else [],
                    'leader': load_leader(def_config.get('leader_id')) if isinstance(unit, Unit) else None,
                    'enhancements': load_enhancements(def_config.get('enhancements')) if isinstance(unit, Unit) else [],
                    # Its chosen wargear's characteristic changes (an army's; engagement.stat_changes)
                    'stat_changes': stat_changes(def_config.get('stat_changes')) if isinstance(unit, Unit) else None,
                    'leader_stat_changes': stat_changes(def_config.get('leader_stat_changes')) if isinstance(unit, Unit) else None,
                })

        # Each defender as seen by ranged and by melee attacks (target rules can be phase-specific);
        # with a leader it is an attached unit: its bodyguard models first, then the leader
        for d in defenders:
            d_side = sources_for(None, d['leader'], d['enhancements'], defender_rules, defender_affliction, d['extra_modifiers'])
            d_modifiers = {w_type: target_modifiers(d_side, attacker_affliction, w_type, d['unit'].faction_id)
                           for w_type in ('ranged', 'melee')}
            d['profiles'] = {w_type: unit_target(d['unit'], d['model_count'], mods, d['stat_changes'])
                             for w_type, mods in d_modifiers.items()}
            d['leader_profiles'] = {w_type: leader_target(d['leader'], mods, d['leader_stat_changes'])
                                    for w_type, mods in d_modifiers.items()} \
                if d['leader'] is not None else None
            d['total_models'] = d['model_count'] + (d['leader'].default_models or 1 if d['leader'] is not None else 0)

        def weapon_part(result):
            return (result.average_damage, result.models_killed, result.overkill)

        def cell(weapons, d, points):
            """Matrix metrics for weapons attacking a defender together (in order, for an attached unit)."""
            if d['leader_profiles'] is None:
                return combine_cells([weapon_part(simulate_analytical(w['profile'], d['profiles'][w['type']], conditions))
                                      for w in weapons], d['unit'], d['model_count'], points)
            result, _ = simulate_attached_analytical(
                [w['profile'] for w in weapons], d['profiles']['ranged'], d['leader_profiles']['ranged'], conditions,
                bodyguard_targets=[d['profiles'][w['type']] for w in weapons],
                leader_targets=[d['leader_profiles'][w['type']] for w in weapons])
            metrics = combine_cells([weapon_part(result)], d['unit'], d['total_models'], points)
            metrics.update(bodyguards_killed=result.bodyguards_killed, leader_killed=result.leader_killed,
                           leader_damage=result.leader_damage, leader_wounds=d['leader'].wounds * (d['leader'].default_models or 1))
            return metrics

        matrix = []
        weapon_matrix = []
        for atk in attackers:
            matrix.append([cell(atk['weapons'], d, atk['points']) for d in defenders])
            weapon_matrix.append([[cell([w], d, atk['points']) for d in defenders] for w in atk['weapons']])

        total_points = sum(a['points'] for a in attackers)
        all_weapons = [w for atk in attackers for w in atk['weapons']]
        total_cells = [
            cell(all_weapons, d, total_points) if d['leader_profiles'] is not None else
            combine_cells([(row[def_idx]['avg_damage'], row[def_idx]['models_killed'], row[def_idx]['overkill'])
                           for row in matrix],
                          d['unit'], d['model_count'], total_points)
            for def_idx, d in enumerate(defenders)
        ]

        results = {
            'attackers': [{
                'label': a['label'],
                'points': a['points'],
                'weapons': [{'label': w['label'], 'type': w['type'], 'models': w['models']} for w in a['weapons']],
            } for a in attackers],
            'defenders': [{'label': d['label'], 'points': d['points'], 'weight': d['weight'],
                           'leader': d['leader'].name if d['leader'] is not None else None} for d in defenders],
            'matrix': matrix,  # [attacker unit][defender]
            'weapon_matrix': weapon_matrix,  # [attacker unit][weapon][defender]
            'total': {'label': 'All attackers', 'points': total_points, 'cells': total_cells},
            'summary': {
                'best_attackers': [],  # Most efficient attacker unit for each defender
                'biggest_threats': []  # Most damaging attacker unit for each defender
            }
        }

        # Summary ranks attacker units only (not the total row)
        for def_idx, def_data in enumerate(defenders):
            if not attackers:
                break
            best = max(range(len(attackers)), key=lambda i: matrix[i][def_idx]['damage_per_point'])
            threat = max(range(len(attackers)), key=lambda i: matrix[i][def_idx]['avg_damage'])
            results['summary']['best_attackers'].append({
                'defender': def_data['label'],
                'attacker': attackers[best]['label'],
                'attacker_idx': best,
                'efficiency': matrix[best][def_idx]['damage_per_point'],
            })
            results['summary']['biggest_threats'].append({
                'defender': def_data['label'],
                'attacker': attackers[threat]['label'],
                'attacker_idx': threat,
                'damage': matrix[threat][def_idx]['avg_damage'],
            })

        # % of each target destroyed and points efficiency (Excel's kill grid and heat map)
        def add_percentages(cell, models, points):
            pct = min(1.0, cell['kills_uncapped'] / max(1, models)) * 100
            cell['pct_destroyed'] = round(pct, 1)
            cell['pct_per_100'] = round(pct / points * 100, 2) if points else None

        for atk_idx, atk in enumerate(attackers):
            for def_idx, d in enumerate(defenders):
                add_percentages(matrix[atk_idx][def_idx], d['total_models'], atk['points'])
                for weapon_cells in weapon_matrix[atk_idx]:
                    add_percentages(weapon_cells[def_idx], d['total_models'], atk['points'])
        for def_idx, d in enumerate(defenders):
            add_percentages(total_cells[def_idx], d['total_models'], total_points)
            total_cells[def_idx]['wiped'] = total_cells[def_idx]['kills_uncapped'] >= d['total_models'] - 1e-9

        results['analysis'] = army_analysis(attackers, defenders, matrix, total_cells, total_points,
                                            conditions, data.get('weapon_filter', 'both'),
                                            (attacker_affliction, defender_affliction), attacker_rules)
        return jsonify(results)

    def army_analysis(attackers, defenders, matrix, total_cells, total_points, conditions, weapon_filter,
                      afflicted=(None, None), my_rules=()):
        """Excel's Results tab: summary, gaps, role scores, T/Sv coverage and reverse durability."""
        weights = [d['weight'] if d['weight'] is not None else 1 for d in defenders]
        army_pcts = [cell['pct_destroyed'] for cell in total_cells]
        avg_pct = weighted_average(army_pcts, weights)

        # Best unit: highest weighted average % destroyed per 100 points
        best = None
        for atk_idx, atk in enumerate(attackers):
            unit_pct = weighted_average([matrix[atk_idx][i]['pct_destroyed'] for i in range(len(defenders))], weights)
            per_100 = unit_pct / atk['points'] * 100 if atk['points'] else 0
            if best is None or per_100 > best['pct_per_100']:
                best = {'label': atk['label'], 'pct_per_100': round(per_100, 2), 'avg_pct_destroyed': round(unit_pct, 1)}

        summary = {
            'units': len(attackers),
            'points': total_points,
            'targets': len(defenders),
            'wiped': sum(1 for cell in total_cells if cell.get('wiped')),
            'avg_pct_destroyed': round(avg_pct, 1),
            'pct_per_100': round(avg_pct / total_points * 100, 2) if total_points else None,
            'best_unit': best,
            'weighted': any(d['weight'] is not None for d in defenders),
        }

        # Gaps: targets the whole army can't wipe in one turn, most survivors first
        gaps = []
        for d, cell in zip(defenders, total_cells):
            if not cell.get('wiped'):
                killed = min(cell['kills_uncapped'], d['total_models'])
                gaps.append({'label': d['label'], 'models': d['total_models'],
                             'survivors': round(d['total_models'] - killed, 2), 'pct_destroyed': cell['pct_destroyed']})
        gaps.sort(key=lambda g: (-g['survivors'], g['pct_destroyed']))

        def army_kills(target):
            """Sum of each unit's kills (each capped at the target's size), uncapped across units."""
            total = 0.0
            for atk in attackers:
                unit_kills = sum(simulate_analytical(w['profile'], target, conditions).models_killed for w in atk['weapons'])
                total += min(unit_kills, target.model_count)
            return total

        # Role scores: % of each benchmark unit the army destroys (100% = wiped)
        roles = []
        for role in TargetProfile.query.filter_by(kind='role').order_by(TargetProfile.id):
            target = unit_target(role, role.models)
            kills = army_kills(target)
            roles.append({'name': role.name, 'models': role.models, 'kills': round(kills, 2),
                          'score': round(kills / max(1, role.models) * 100, 1)})

        # T/Sv coverage: expected army damage, no invulnerable/FNP/keywords, damage not capped by wounds
        coverage = []
        for toughness in COVERAGE_TOUGHNESS:
            row = []
            for save in COVERAGE_SAVES:
                target = DefenderProfile(toughness=toughness, save=save, wounds=99, model_count=1)
                row.append(round(sum(simulate_analytical(w['profile'], target, conditions).average_damage
                                     for atk in attackers for w in atk['weapons']), 2))
            coverage.append(row)

        return {
            'summary': summary,
            'gaps': gaps,
            'roles': roles,
            'coverage': {'toughness': COVERAGE_TOUGHNESS, 'saves': COVERAGE_SAVES, 'damage': coverage},
            'reverse': reverse_durability(attackers, defenders, conditions, weapon_filter, afflicted, my_rules),
        }

    def reverse_durability(attackers, defenders, conditions, weapon_filter, afflicted=(None, None), my_rules=()):
        """
        The opponent's real units attacking mine (Excel's Reverse Durability): their loadouts with the
        default ticks, quantities scaled to their model counts, same conditions, no opponent rules.
        My units defend as they would in the Simulator: with my rules', leader's and enhancements' Target
        modifiers (`my_rules`: the rules ticked for my side). `afflicted`: (my units' affliction, theirs),
        as the matrix request says (contagion.py).
        """
        mine, theirs = afflicted
        if not defenders or any(not isinstance(d['unit'], Unit) for d in defenders):
            return None  # Real units mode only

        types = {'ranged': ['ranged'], 'melee': ['melee']}.get(weapon_filter, ['ranged', 'melee'])
        enemies = []
        for d in defenders:
            unit, size = d['unit'], d['model_count']
            enemy_side = sources_for(unit, affliction=theirs)  # their datasheet's own abilities count, no rules
            scale = size / unit.default_models if unit.default_models else 1
            profiles = []
            for weapon_type in types:
                loadout = unit.loadout(weapon_type)
                for (weapon, qty), used in zip(loadout, default_used([w.name for w, _ in loadout])):
                    if used:
                        _, profile = weapon_attack(weapon_type, weapon.id, max(1, round(qty * scale)),
                                                   attack_modifiers(enemy_side, mine, weapon_type, faction_id=unit.faction_id))
                        if profile:
                            profiles.append(profile)
            enemies.append({'label': d['label'], 'profiles': profiles, 'points': d['points']})

        rows = []
        for atk in attackers:
            unit = atk['unit']
            if unit is None:
                continue
            models = atk['size'] or unit.default_models or 1
            my_side = sources_for(None, atk['leader'], atk.get('enhancements') or [], my_rules, mine)
            extra = {w_type: target_modifiers(my_side, theirs, w_type, unit.faction_id) for w_type in ('ranged', 'melee')}
            targets = {w_type: unit_target(unit, models, mods, atk['stat_changes']) for w_type, mods in extra.items()}
            leaders = {w_type: leader_target(atk['leader'], mods, atk['leader_stat_changes']) for w_type, mods in extra.items()} \
                if atk['leader'] is not None else None
            total_models = models + (leaders['ranged'].model_count if leaders else 0)
            cells = []
            for enemy in enemies:
                types = ['melee' if p.is_melee else 'ranged' for p in enemy['profiles']]
                if leaders is not None:  # My unit with its leader: bodyguard models first, then the leader
                    kills = simulate_attached_analytical(
                        enemy['profiles'], targets['ranged'], leaders['ranged'], conditions,
                        bodyguard_targets=[targets[t] for t in types],
                        leader_targets=[leaders[t] for t in types])[0].models_killed if enemy['profiles'] else 0
                else:
                    kills = sum(simulate_analytical(profile, targets[t], conditions).models_killed
                                for profile, t in zip(enemy['profiles'], types))
                cells.append(round(min(1.0, kills / total_models) * 100, 1))
            rows.append({'label': atk['label'], 'models': total_models, 'cells': cells,
                         'total': round(min(100.0, sum(cells)), 1)})

        threat = [round(sum(row['cells'][j] for row in rows), 1) for j in range(len(enemies))]
        biggest = max(range(len(enemies)), key=lambda j: threat[j]) if enemies and rows else None
        if biggest is not None and threat[biggest] <= 0:
            biggest = None  # nobody threatens anything
        points = [e['points'] for e in enemies if e['points']]
        return {
            'enemies': [e['label'] for e in enemies],
            'rows': rows,
            'summary': {
                'points': sum(points) if points else None,
                'wiped': sum(1 for row in rows if row['total'] >= 100),
                'units': len(rows),
                'avg_pct_destroyed': round(sum(row['total'] for row in rows) / len(rows), 1) if rows else 0,
                'biggest_threat': enemies[biggest]['label'] if biggest is not None else None,
            },
        }

    @app.route('/api/matrix/cell', methods=['POST'])
    def api_matrix_cell_detail():
        """
        Monte Carlo detail for one matrix cell.

        Request body:
        {
            "weapons": [{"weapon_id": 1, "weapon_type": "ranged", "models": 9}, ...],
            "unit_id": 4, "model_count": 5, "leader_id": 9,
            "conditions": {...}, "iterations": 1000
        }
        Each weapon can carry the "leader_id" of its unit's leader; the top-level one leads the defender.

        All weapons are rolled together in each iteration, so a unit row or the
        "All attackers" row gets a true combined damage distribution.
        """
        data = request.get_json()

        unit = load_defender(data)
        if unit is None:
            return jsonify({'error': 'Defender not found'}), 404
        model_count = max(1, int(data.get('model_count') or (unit.models if isinstance(unit, TargetProfile) else 1)))
        iterations = min(int(data.get('iterations') or 1000), 10000)
        attacker_rules = selected_rules(data.get('attacker_rules'))
        defender_rules = selected_rules(data.get('defender_rules'))
        extra_modifiers = text_list(data.get('extra_modifiers')) if isinstance(unit, Unit) else []
        defender_leader = load_leader(data.get('leader_id')) if isinstance(unit, Unit) else None
        defender_enhancements = load_enhancements(data.get('enhancements')) if isinstance(unit, Unit) else []
        attacker_affliction, defender_affliction = afflictions(data)
        target_side = sources_for(None, defender_leader, defender_enhancements, defender_rules, defender_affliction, extra_modifiers)
        changes = stat_changes(data.get('stat_changes')) if isinstance(unit, Unit) else None
        leader_changes = stat_changes(data.get('leader_stat_changes')) if isinstance(unit, Unit) else None

        # Each weapon with its own unit's attacker rules, against the target as its phase sees it
        profiles, targets, leader_targets = [], [], []
        for w in data.get('weapons', []):
            w_type = 'melee' if w.get('weapon_type') == 'melee' else 'ranged'
            atk_unit = db.session.get(Unit, w['unit_id']) if w.get('unit_id') else None
            atk_leader = load_leader(w.get('leader_id'))
            atk_side = sources_for(atk_unit, atk_leader, load_enhancements(w.get('enhancements')), attacker_rules,
                                   attacker_affliction)
            weapon_modifiers = attack_modifiers(atk_side, defender_affliction, w_type, w.get('leader'),
                                                atk_unit.faction_id if atk_unit else None)
            _, profile = weapon_attack(w_type, w.get('weapon_id'), w.get('models', 1), weapon_modifiers)
            if profile:
                profiles.append(profile)
                mods = target_modifiers(target_side, attacker_affliction, w_type, unit.faction_id)
                targets.append(unit_target(unit, model_count, mods, changes))
                if defender_leader is not None:
                    leader_targets.append(leader_target(defender_leader, mods, leader_changes))
        if not profiles:
            return jsonify({'error': 'No weapons found'}), 400

        conditions = conditions_from_request(data.get('conditions', {}))
        if defender_leader is not None:  # Attached unit: bodyguard models first, then the leader
            result = simulate_monte_carlo_attached(
                profiles, unit_target(unit, model_count, extra_modifiers, changes),
                leader_target(defender_leader, extra_modifiers, leader_changes), conditions, iterations,
                bodyguard_targets=targets, leader_targets=leader_targets)
        else:
            result = simulate_monte_carlo_combined(
                profiles, unit_target(unit, model_count, extra_modifiers, changes), conditions, iterations,
                weapon_defenders=targets,
            )

        return jsonify({
            'summary': {
                'average_damage': result.average_damage,
                'models_killed': result.models_killed,
                'overkill': result.overkill,
                'kill_probability': result.kill_probability,
                'wipe_probability': result.wipe_probability,
                'bodyguards_killed': result.bodyguards_killed,
                'leader_killed': result.leader_killed,
                'leader_damage': result.leader_damage,
                'leader_kill_probability': result.leader_kill_probability,
            },
            'breakdown': {
                'attacks': result.attacks_made,
                'hits': result.hits,
                'wounds': result.wounds,
                'failed_saves': result.failed_saves,
                'damage': result.damage_dealt,
            },
            'distribution': result.damage_distribution,
            'method': result.method,
            'iterations': result.iterations,
        })

    # --- Army lists (armies.py): built on the Armies page, used in Analytics ----------------

    @app.route('/armies')
    def armies_page():
        factions = Faction.query.order_by(Faction.name).all()
        return render_template('armies.html', factions=[{'id': f.id, 'name': f.name} for f in factions],
                               battle_sizes=armies.BATTLE_SIZES)

    def army_from_request():
        try:
            return armies.clean_army(request.get_json(silent=True)), None
        except armies.ArmyError as err:
            return None, (jsonify({'error': str(err)}), 400)

    def saved_army_response(saved):
        """A saved army with its prices and problems (its units priced too)."""
        army = saved.to_dict()
        whose = access.owner_names().get(saved.owner_id) if access.principal().shared else None
        return jsonify(dict(army, **armies.price_army(army), mine=access.can_write(saved), owner=whose))


    @app.route('/api/armies')
    def api_list_armies():
        """Saved armies, newest first, with their faction and points. On a self-hosted copy, every player's: 'mine'
        says which are yours, 'owner' whose the others are (read-only; a copy is yours)."""
        names = {f.id: f.name for f in Faction.query}
        players = access.owner_names() if access.principal().shared else None
        out = []
        for saved in SavedArmy.query.order_by(SavedArmy.updated_at.desc(), SavedArmy.id.desc()):
            priced = armies.price_army(saved.to_dict())
            out.append({'id': saved.id, 'name': saved.name, 'faction': names.get(saved.faction_id),
                        'detachment': priced['detachment'], 'total': priced['total'],
                        'points_limit': saved.points_limit, 'unit_count': len(saved.units or []),
                        'problem_count': len(priced['problems']), 'mine': access.can_write(saved),
                        'owner': players.get(saved.owner_id) if players is not None else None})
        out.sort(key=lambda a: not a['mine'])  # Yours first
        return jsonify(out)

    @app.route('/api/armies/price', methods=['POST'])
    def api_price_army():
        """Price and check an army as it is being built (nothing is saved)."""
        army, error = army_from_request()
        if error:
            return error
        return jsonify(armies.price_army(army))

    @app.route('/api/armies/wargear', methods=['POST'])
    def api_army_wargear():
        """
        One unit's wargear for the Armies page's popup: {unit_id, faction_id, models, wargear} ->
        {models, points, problems, wargear (the full choices), loadout, form, summary, sizes}.
        """
        data = request.get_json(silent=True) or {}
        unit = db.session.get(Unit, int(data.get('unit_id') or 0))
        if unit is None or unit.wargear is None:
            return jsonify({'error': 'This unit has no wargear to choose.'}), 404
        try:
            choices = armies.clean_wargear(data.get('wargear'))
            models = int(data.get('models') or 0) or None
        except (armies.ArmyError, TypeError, ValueError) as err:
            return jsonify({'error': str(err)}), 400
        faction = db.session.get(Faction, int(data.get('faction_id') or unit.faction_id))
        result = armies.unit_wargear(unit, choices, models, faction.bsdata_id if faction else None)
        return jsonify(dict(result, wargear=result.pop('choices'), sizes=armies.sizes_of(unit), name=unit.name))

    @app.route('/api/armies/text', methods=['POST'])
    def api_army_text():
        """An army as plain text, to share."""
        army, error = army_from_request()
        if error:
            return error
        return jsonify({'text': armies.army_text(army, armies.price_army(army))})

    @app.route('/api/armies/share', methods=['POST'])
    def api_army_share_code():
        """An army as a share code another install can import (army_share.py); lists with custom units can't be shared."""
        army, error = army_from_request()
        if error:
            return error
        try:
            return jsonify({'code': army_share.encode(army)})
        except armies.ArmyError as err:
            return jsonify({'error': str(err)}), 400

    @app.route('/api/armies/import', methods=['POST'])
    def api_army_import_code():
        """{code} -> {army (in this install's IDs, not saved), notes (what wasn't found in this install's data)}."""
        try:
            return jsonify(army_share.decode((request.get_json(silent=True) or {}).get('code')))
        except armies.ArmyError as err:
            return jsonify({'error': str(err)}), 400

    @app.route('/api/armies', methods=['POST'])
    def api_create_army():
        army, error = army_from_request()
        if error:
            return error
        saved = SavedArmy(**army, owner_id=access.stamp())
        db.session.add(saved)
        db.session.commit()
        return saved_army_response(saved)

    @app.route('/api/armies/<int:army_id>')
    def api_get_army(army_id):
        return saved_army_response(access.get(SavedArmy, army_id))

    @app.route('/armies/<int:army_id>/match')
    def army_match_page(army_id):
        saved = access.get(SavedArmy, army_id)
        return render_template('match.html', army_id=saved.id, army_name=saved.name)

    @app.route('/api/armies/<int:army_id>/match')
    def api_army_match(army_id):
        """A saved army's Match view: its units' datasheets, rules, enhancements and stratagems (match_view.py)."""
        return jsonify(match_view.match_view(access.get(SavedArmy, army_id).to_dict()))

    @app.route('/api/armies/<int:army_id>', methods=['PUT'])
    def api_update_army(army_id):
        saved = access.get(SavedArmy, army_id, write=True)
        army, error = army_from_request()
        if error:
            return error
        for key, value in army.items():
            setattr(saved, key, value)
        db.session.commit()
        return saved_army_response(saved)

    @app.route('/api/armies/<int:army_id>/copy', methods=['POST'])
    def api_copy_army(army_id):
        saved = access.get(SavedArmy, army_id)
        copy = SavedArmy(name=f'{saved.name} (copy)'[:200], faction_id=saved.faction_id,
                         detachment_keys=saved.to_dict()['detachment_keys'], points_limit=saved.points_limit,
                         warlord=saved.warlord, disposition=saved.disposition,
                         units=list(saved.units or []), owner_id=access.stamp())
        db.session.add(copy)
        db.session.commit()
        return saved_army_response(copy)

    @app.route('/api/armies/<int:army_id>', methods=['DELETE'])
    def api_delete_army(army_id):
        saved = access.get(SavedArmy, army_id, write=True)
        db.session.delete(saved)
        db.session.commit()
        return jsonify({'message': 'Army deleted'})

    @app.route('/api/comparisons', methods=['GET'])
    def api_list_comparisons():
        """List saved matrix comparisons."""
        comparisons = access.listed(SavedComparison, SavedComparison.query.order_by(SavedComparison.created_at.desc()))
        return jsonify([{
            'id': c.id,
            'name': c.name,
            'created_at': c.created_at.isoformat(),
            'attacker_count': len(c.attackers) if c.attackers else 0,
            'defender_count': len(c.defenders) if c.defenders else 0,
        } for c in comparisons])

    @app.route('/api/comparisons', methods=['POST'])
    def api_save_comparison():
        """Save a new matrix comparison."""
        data = request.get_json()

        comparison = SavedComparison(
            name=data['name'],
            attackers=data['attackers'],
            defenders=data['defenders'],
            battle_conditions=data['conditions'],
            results=data.get('results'),
            owner_id=access.stamp(),
        )
        db.session.add(comparison)
        db.session.commit()

        return jsonify({'id': comparison.id, 'message': 'Comparison saved'})

    @app.route('/api/comparisons/<int:comparison_id>')
    def api_get_comparison(comparison_id):
        """Get a specific comparison with full config."""
        c = access.get(SavedComparison, comparison_id)
        return jsonify({
            'id': c.id,
            'name': c.name,
            'created_at': c.created_at.isoformat(),
            'attackers': c.attackers,
            'defenders': c.defenders,
            'battle_conditions': c.battle_conditions,
            'results': c.results,
        })

    @app.route('/api/comparisons/<int:comparison_id>', methods=['DELETE'])
    def api_delete_comparison(comparison_id):
        """Delete a comparison."""
        comparison = access.get(SavedComparison, comparison_id, write=True)
        db.session.delete(comparison)
        db.session.commit()
        return jsonify({'message': 'Comparison deleted'})
