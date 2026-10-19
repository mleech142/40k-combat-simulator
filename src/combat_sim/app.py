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
from .models import (db, init_db, Faction, Detachment, Unit, RangedWeapon, MeleeWeapon, FactionRule, Stratagem, Keyword, TargetProfile,
                     SavedScenario, SavedComparison, BSDATA_ID_BASE, unit_ranged_weapons, unit_melee_weapons)
from . import __version__
from .paths import DATA_FILE, DATABASE_FILE, BSDATA_FOLDER, BSDATA_CACHE_FILE, prepare_data_dir
from .data_store import (DataFileError, read_data_file, check_data, load_bsdata, merge_data,
                         rebuild_reference_tables, export_data, write_data_file, LINKED_FIELDS)
from . import bsdata_download, folder_dialog
from .settings import settings_path, load_settings, update_settings
from .simulation import (
    simulate, simulate_analytical, simulate_monte_carlo_combined, keyword_name, check_keywords,
    AttackerProfile, DefenderProfile, BattleConditions
)


def create_app(config=None):
    """Application factory."""
    app = Flask(__name__)

    # Configuration. The app data JSON (custom factions, rules, stratagems, references) and the
    # database default to the folder from paths.data_dir(): the repository's data/ from source,
    # a per-user folder in the packaged exe. Settings, the import cache and "Download latest"
    # sit next to the app data JSON. The BSData folder is the one chosen on the Setup page,
    # else BSDATA_DIR, else the bsdata folder next to the app data JSON.
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
    app.config['SETTINGS_JSON'] = str(settings_path(data_json))
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY') or secrets.token_hex(32)
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # New on every start. Pages tag saved page state with it, so state survives
    # switching tabs but is discarded after the app is restarted.
    app.config['BOOT_ID'] = uuid.uuid4().hex

    @app.context_processor
    def inject_page_globals():
        return {'boot_id': app.config['BOOT_ID'], 'app_version': __version__,
                'bsdata_label': bsdata_label(app)}

    if config:
        app.config.update(config)

    # Initialize database, then rebuild the reference tables from BSData and the app data JSON
    init_db(app)
    with app.app_context():
        reload_bsdata(app)

    # Register routes
    register_routes(app)

    return app


def bsdata_folder(app):
    """The BSData folder in use: chosen on the Setup page, else the default."""
    chosen = load_settings(app.config['SETTINGS_JSON']).get('bsdata_dir')
    return chosen or app.config['DEFAULT_BSDATA_DIR']


def reload_bsdata(app, fresh=False):
    """(Re)import the BSData folder and rebuild the reference tables. fresh: ignore the cache."""
    if fresh:
        Path(app.config['BSDATA_CACHE']).unlink(missing_ok=True)
    data = read_data_file(app.config['DATA_JSON'])
    known = [k['name'] for k in data.get('keywords', [])] or None
    app.config['BSDATA_DIR'] = bsdata_folder(app)
    result, status = load_bsdata(app.config['BSDATA_DIR'], app.config['BSDATA_CACHE'], known)
    app.config['BSDATA_RESULT'] = result
    app.config['BSDATA_STATUS'] = status
    load_reference_data(app, data)
    return status


def bsdata_label(app):
    """Data version for the footer: the downloaded commit, else the game system revision."""
    status = app.config.get('BSDATA_STATUS') or {}
    if not status.get('loaded'):
        return None
    download = load_settings(app.config['SETTINGS_JSON']).get('download') or {}
    if download.get('sha') and download.get('folder') == app.config.get('BSDATA_DIR'):
        return f"@ {download['sha'][:7]}, {(download.get('date') or '')[:10]}".rstrip(', ')
    revision = ((status.get('source') or {}).get('game_system') or {}).get('revision')
    return f'game system revision {revision}' if revision is not None else None


def load_reference_data(app, data):
    """Rebuild the reference tables from app data JSON `data` plus the loaded BSData import."""
    merged, unmatched = merge_data(data, app.config.get('BSDATA_RESULT'))
    app.config['DATA_SOURCE'] = data.get('source')
    app.config['UNMATCHED'] = unmatched
    rebuild_reference_tables(merged)


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


# Phases a rule must have to apply to a weapon of each type
RULE_PHASES = {'ranged': ('Shooting', 'Both'), 'melee': ('Fighting', 'Both')}


def selected_rules(selection):
    """Ticked faction rules and stratagems from {'rule_ids': [...], 'stratagem_ids': [...]}."""
    selection = selection or {}
    rule_ids = [int(i) for i in selection.get('rule_ids', []) or []]
    strat_ids = [int(i) for i in selection.get('stratagem_ids', []) or []]
    rules = FactionRule.query.filter(FactionRule.id.in_(rule_ids)).all() if rule_ids else []
    strats = Stratagem.query.filter(Stratagem.id.in_(strat_ids)).all() if strat_ids else []
    return rules + strats


def rule_modifiers(records, side, faction_id, weapon_type):
    """
    Modifier text from ticked rules/stratagems that apply here: the right side
    ('Attacker' or 'Target'), the unit's faction (core stratagems apply to all) and
    the phase of the attacking weapon.
    """
    modifiers = []
    for record in records:
        if (record.applies_to or 'Attacker') != side:
            continue
        if record.faction_id is not None and faction_id is not None and record.faction_id != faction_id:
            continue
        if (record.phase or 'Both') not in RULE_PHASES['melee' if weapon_type == 'melee' else 'ranged']:
            continue
        modifiers += [m.strip() for m in (record.modifier or '').split(',')
                      if m.strip() and m.strip().lower() != 'none']
    return modifiers


BUFF_SIDES = ('Attacker', 'Target')
BUFF_PHASES = ('Shooting', 'Fighting', 'Both')


def load_leader(leader_id):
    """The unit named as leading another in a request, or None."""
    try:
        return db.session.get(Unit, int(leader_id)) if leader_id not in (None, '') else None
    except (TypeError, ValueError):
        return None


def leader_modifiers(leader, side, weapon_type):
    """Modifier text a leader grants the unit it leads, for one side and the attacking weapon's phase."""
    if leader is None:
        return []
    modifiers = []
    for buff in leader.effective_leader_buffs:
        if (buff.get('applies_to') or 'Attacker') != side:
            continue
        if (buff.get('phase') or 'Both') not in RULE_PHASES['melee' if weapon_type == 'melee' else 'ranged']:
            continue
        modifiers += [m.strip() for m in (buff.get('modifier') or '').split(',') if m.strip()]
    return modifiers


def clean_buffs(rows):
    """Leader buff rows from a request: side and phase checked, empty modifiers dropped."""
    cleaned = []
    for row in rows or []:
        modifier = str(row.get('modifier') or '').strip()
        if not modifier:
            continue
        cleaned.append({
            'applies_to': row.get('applies_to') if row.get('applies_to') in BUFF_SIDES else 'Attacker',
            'phase': row.get('phase') if row.get('phase') in BUFF_PHASES else 'Both',
            'modifier': modifier,
        })
    return cleaned


def modifier_has_effect(text, modelled_names):
    """True if any entry of a modifier text is a keyword the calculations use."""
    entries = [m for m in (text or '').split(',') if m.strip() and m.strip().lower() != 'none']
    if not modelled_names:
        return bool(entries)
    return any(keyword_name(m).lower() in modelled_names for m in entries)


def default_used(names):
    """
    Which weapons are used by default: all, except later profiles of a "Weapon - Profile" family
    (alternative profiles of one gun). Same rule as the Simulator and Matchups pages.
    """
    families, used = set(), []
    for name in names:
        family = name.split(' - ')[0].strip().lower() if ' - ' in name else None
        used.append(not family or family not in families)
        if family:
            families.add(family)
    return used


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
        status = app.config.get('BSDATA_STATUS') or {}
        if not status.get('loaded') and not load_settings(app.config['SETTINGS_JSON']).get('setup_skipped'):
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
        "attacker" and "defender" can name a "leader_id": a leader attached to that unit, whose
        buffs apply to its side (the leader's own weapons are sent as weapons like the others).
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

        profiles, targets, rows = [], [], []
        applied = {'attacker': [], 'target': []}
        for w in weapons_data:
            w_type = 'melee' if w.get('type') == 'melee' else 'ranged'
            # Ticked rules and stratagems add modifier text to the weapon (attacker) or the target
            attacker_modifiers = (rule_modifiers(attacker_rules, 'Attacker', w.get('faction_id', attacker_data.get('faction_id')), w_type)
                                  + leader_modifiers(attacker_leader, 'Attacker', w_type))
            target_modifiers = (rule_modifiers(target_rules, 'Target', defender_data.get('faction_id'), w_type)
                                + leader_modifiers(defender_leader, 'Target', w_type))
            for side, side_modifiers in (('attacker', attacker_modifiers), ('target', target_modifiers)):
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
            target = target_profile(target_modifiers)
            profiles.append(profile)
            targets.append(target)

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
        if method == 'monte_carlo':
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
            results=data.get('results')
        )
        db.session.add(scenario)
        db.session.commit()

        return jsonify({'id': scenario.id, 'message': 'Scenario saved'})

    @app.route('/api/scenarios/<int:scenario_id>', methods=['DELETE'])
    def api_delete_scenario(scenario_id):
        """Delete a scenario."""
        scenario = db.get_or_404(SavedScenario, scenario_id)
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
        db.session.commit()
        write_data_file(app.config['DATA_JSON'],
                        export_data(app.config.get('DATA_SOURCE'), app.config.get('UNMATCHED')))

    def read_only(message='BSData records are read-only.'):
        return jsonify({'error': message}), 403

    def next_custom_id(model):
        """IDs for records made in the app stay below the BSData range."""
        top = db.session.query(db.func.max(model.id)).filter(model.id < BSDATA_ID_BASE).scalar()
        return (top or 0) + 1

    def editable_faction(faction_id):
        faction = db.session.get(Faction, int(faction_id)) if faction_id not in (None, '') else None
        return faction is not None and faction.editable

    @app.route('/api/data/status')
    def api_data_status():
        """The BSData folder and import: where, whether it loaded, revisions and report."""
        status = app.config.get('BSDATA_STATUS') or {}
        report = status.get('report') or []
        counts = {}
        for item in report:
            counts[item['kind']] = counts.get(item['kind'], 0) + 1
        return jsonify({
            'folder': status.get('folder'),
            'loaded': bool(status.get('loaded')),
            'error': status.get('error'),
            'from_cache': bool(status.get('from_cache')),
            'source': status.get('source'),
            'report_counts': counts,
            'report': report if request.args.get('report') else None,
            'factions': Faction.query.filter_by(source='bsdata').count(),
            'units': Unit.query.join(Faction).filter(Faction.source == 'bsdata').count(),
            'label': bsdata_label(app),
            'download': load_settings(app.config['SETTINGS_JSON']).get('download'),
            'download_folder': app.config['DOWNLOAD_DIR'],
            'default_folder': app.config['DEFAULT_BSDATA_DIR'],
        })

    # --- Setup: choosing, downloading and re-importing the BSData folder -------------

    @app.route('/setup')
    def setup_page():
        return render_template('setup.html')

    @app.route('/api/bsdata/folder', methods=['POST'])
    def api_bsdata_folder():
        """Use the BSData folder {'path'} (null = back to the default), if it loads."""
        path = (request.get_json() or {}).get('path')
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
        status = reload_bsdata(app)
        if status.get('error'):  # Keep the folder that worked
            update_settings(settings_file, bsdata_dir=previous)
            reload_bsdata(app)
            return jsonify({'error': status['error']}), 400
        return api_data_status()

    @app.route('/api/bsdata/browse', methods=['POST'])
    def api_bsdata_browse():
        """Open the computer's folder dialog; {'path'} or {'path': null} if cancelled or unavailable."""
        return jsonify({'path': folder_dialog.choose_folder(initial=app.config.get('BSDATA_DIR'))})

    @app.route('/api/bsdata/download', methods=['POST'])
    def api_bsdata_download():
        """Download the latest catalogues from GitHub into the download folder and use it."""
        try:
            commit = bsdata_download.latest_commit()
            count = bsdata_download.install_zip(bsdata_download.download_zip(commit['sha']), app.config['DOWNLOAD_DIR'])
        except bsdata_download.DownloadError as err:
            return jsonify({'error': str(err)}), 502
        update_settings(app.config['SETTINGS_JSON'], bsdata_dir=app.config['DOWNLOAD_DIR'], download={
            'sha': commit['sha'], 'date': commit['date'], 'files': count, 'folder': app.config['DOWNLOAD_DIR'],
            'downloaded_at': datetime.now(timezone.utc).isoformat(timespec='seconds')})
        status = reload_bsdata(app, fresh=True)
        if status.get('error'):
            return jsonify({'error': status['error']}), 400
        return api_data_status()

    @app.route('/api/bsdata/reimport', methods=['POST'])
    def api_bsdata_reimport():
        """Import the BSData folder again (after changing its files), ignoring the cache."""
        reload_bsdata(app, fresh=True)
        return api_data_status()

    @app.route('/api/bsdata/skip-setup', methods=['POST'])
    def api_bsdata_skip_setup():
        """Use the app without BSData for now: the Simulator stops redirecting to Setup."""
        update_settings(app.config['SETTINGS_JSON'], setup_skipped=True)
        return jsonify({'message': 'Setup skipped'})

    def int_or_none(value):
        if value in (None, '', '-'):
            return None
        return int(value)

    def text_list(value):
        """A list of strings from a list or a comma-separated string."""
        if isinstance(value, str):
            value = value.split(',')
        return [str(v).strip() for v in (value or []) if str(v).strip()]

    @app.errorhandler(IntegrityError)
    def handle_integrity_error(err):
        db.session.rollback()
        return jsonify({'error': 'That would create a duplicate or break a link between records.'}), 400

    @app.route('/api/data/export')
    def api_data_export():
        """The current app data JSON (BSData records are left out)."""
        return jsonify(export_data(app.config.get('DATA_SOURCE'), app.config.get('UNMATCHED')))

    @app.route('/api/data/import', methods=['POST'])
    def api_data_import():
        """Replace all app data with an uploaded app data JSON (BSData stays as loaded)."""
        data = request.get_json()
        try:
            check_data(data)
        except DataFileError as err:
            return jsonify({'error': str(err)}), 400
        load_reference_data(app, data)
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
            'rule_count': f.faction_rules.count(),
            'stratagem_count': f.stratagems.count(),
        } for f in factions])

    @app.route('/api/data/factions/<int:faction_id>/reference')
    def api_data_faction_reference(faction_id):
        """BSData's text for a faction: army rules and detachments (read-only reference)."""
        faction = db.get_or_404(Faction, faction_id)
        detachments = Detachment.query.filter_by(faction_id=faction.id).order_by(Detachment.name)
        return jsonify({'army_rules': faction.army_rules or [], 'detachments': [d.to_dict() for d in detachments]})

    @app.route('/api/data/factions', methods=['POST'])
    def api_data_create_faction():
        """Create a new (custom) faction."""
        data = request.get_json()
        faction = Faction(id=next_custom_id(Faction), name=data['name'].strip(), source='custom')
        db.session.add(faction)
        save_data()
        return jsonify({'id': faction.id, 'name': faction.name})

    @app.route('/api/data/factions/<int:faction_id>', methods=['PUT'])
    def api_data_update_faction(faction_id):
        """Rename a custom faction."""
        faction = db.get_or_404(Faction, faction_id)
        if not faction.editable:
            return read_only()
        data = request.get_json()
        faction.name = data.get('name', faction.name).strip()
        save_data()
        return jsonify({'id': faction.id, 'name': faction.name})

    @app.route('/api/data/factions/<int:faction_id>', methods=['DELETE'])
    def api_data_delete_faction(faction_id):
        """Delete a custom faction with its units, weapons, rules and stratagems."""
        faction = db.get_or_404(Faction, faction_id)
        if not faction.editable:
            return read_only()
        for unit in faction.units:
            unit.set_loadout('ranged', [])
            unit.set_loadout('melee', [])
            db.session.delete(unit)
        RangedWeapon.query.filter_by(faction_id=faction_id).delete()
        MeleeWeapon.query.filter_by(faction_id=faction_id).delete()
        FactionRule.query.filter_by(faction_id=faction_id).delete()
        Stratagem.query.filter_by(faction_id=faction_id).delete()
        db.session.delete(faction)
        save_data()
        return jsonify({'message': 'Faction deleted'})

    # --- Faction rules ------------------------------------------------------

    def apply_detachment(record, data):
        """detachment_key from the request; its name is looked up (kept if the detachment isn't loaded)."""
        key = (data.get('detachment_key') or '').strip() or None
        known = Detachment.query.filter_by(bsdata_id=key).first() if key else None
        if not key:
            record.detachment_name = None
        elif known:
            record.detachment_name = known.name
        elif key != record.detachment_key:
            record.detachment_name = data.get('detachment_name') or key
        record.detachment_key = key

    def apply_rule_fields(rule, data):
        rule.name = data['name'].strip()
        rule.applies_to = data.get('applies_to') or None
        rule.phase = data.get('phase') or None
        rule.modifier = (data.get('modifier') or '').strip() or None
        apply_detachment(rule, data)

    @app.route('/api/data/rules')
    def api_data_rules():
        """Faction rules, optionally for one faction."""
        query = FactionRule.query
        if request.args.get('faction_id', type=int):
            query = query.filter_by(faction_id=request.args.get('faction_id', type=int))
        return jsonify([r.to_dict() for r in query.order_by(FactionRule.name).all()])

    @app.route('/api/data/rules', methods=['POST'])
    def api_data_create_rule():
        data = request.get_json()
        rule = FactionRule(faction_id=int(data['faction_id']))
        apply_rule_fields(rule, data)
        db.session.add(rule)
        save_data()
        return jsonify(rule.to_dict())

    @app.route('/api/data/rules/<int:rule_id>', methods=['PUT'])
    def api_data_update_rule(rule_id):
        rule = db.get_or_404(FactionRule, rule_id)
        apply_rule_fields(rule, request.get_json())
        save_data()
        return jsonify(rule.to_dict())

    @app.route('/api/data/rules/<int:rule_id>', methods=['DELETE'])
    def api_data_delete_rule(rule_id):
        db.session.delete(db.get_or_404(FactionRule, rule_id))
        save_data()
        return jsonify({'message': 'Rule deleted'})

    # --- Stratagems (core when faction_id is null) --------------------------

    def apply_stratagem_fields(strat, data):
        strat.name = data['name'].strip()
        strat.cp = (str(data.get('cp') or '')).strip() or None
        strat.applies_to = data.get('applies_to') or None
        strat.phase = data.get('phase') or None
        strat.modifier = (data.get('modifier') or '').strip() or None
        strat.notes = (data.get('notes') or '').strip() or None
        apply_detachment(strat, data)

    @app.route('/api/data/stratagems')
    def api_data_stratagems():
        """Stratagems: ?core=1 for core ones, ?faction_id=N for a faction's, otherwise all."""
        query = Stratagem.query
        if request.args.get('core'):
            query = query.filter(Stratagem.faction_id.is_(None))
        elif request.args.get('faction_id', type=int):
            query = query.filter_by(faction_id=request.args.get('faction_id', type=int))
        return jsonify([s.to_dict() for s in query.order_by(Stratagem.name).all()])

    @app.route('/api/data/stratagems', methods=['POST'])
    def api_data_create_stratagem():
        data = request.get_json()
        strat = Stratagem(faction_id=int_or_none(data.get('faction_id')))
        apply_stratagem_fields(strat, data)
        db.session.add(strat)
        save_data()
        return jsonify(strat.to_dict())

    @app.route('/api/data/stratagems/<int:strat_id>', methods=['PUT'])
    def api_data_update_stratagem(strat_id):
        strat = db.get_or_404(Stratagem, strat_id)
        apply_stratagem_fields(strat, request.get_json())
        save_data()
        return jsonify(strat.to_dict())

    @app.route('/api/data/stratagems/<int:strat_id>', methods=['DELETE'])
    def api_data_delete_stratagem(strat_id):
        db.session.delete(db.get_or_404(Stratagem, strat_id))
        save_data()
        return jsonify({'message': 'Stratagem deleted'})

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

    def apply_unit_fields(unit, data):
        unit.faction_id = int(data['faction_id'])
        unit.name = data['name'].strip()
        unit.toughness = int(data['toughness'])
        unit.save = int(data['save'])
        unit.wounds = int(data['wounds'])
        unit.invulnerable_save = int_or_none(data.get('invulnerable_save'))
        unit.feel_no_pain = int_or_none(data.get('feel_no_pain'))
        unit.move = (str(data.get('move') or '')).strip() or None
        unit.oc = int_or_none(data.get('oc'))
        unit.leadership = int_or_none(data.get('leadership'))
        brackets = []
        for row in data.get('points_by_size') or []:
            models, points = int_or_none(row.get('models')), int_or_none(row.get('points'))
            if models and points is not None:
                brackets.append({'models': models, 'points': points})
        if brackets or 'points_by_size' in data:
            unit.points_by_size = brackets
            unit.points_cost = brackets[0]['points'] if brackets else None
            unit.default_models = brackets[0]['models'] if brackets else int_or_none(data.get('default_models'))
        else:
            unit.points_cost = int_or_none(data.get('points_cost'))
            unit.default_models = int_or_none(data.get('default_models'))
        unit.keywords = text_list(data.get('keywords'))
        unit.modifiers = text_list(data.get('modifiers'))
        unit.can_lead = text_list(data.get('can_lead'))
        if 'leader_buffs' in data:
            unit.leader_buffs = clean_buffs(data.get('leader_buffs'))
        unit.abilities = (data.get('abilities') or '').strip() or None

    def apply_unit_loadout(unit, data):
        """Set the loadout from [{'weapon_id', 'qty'}], keeping only the unit's own faction's weapons."""
        for weapon_type, model in (('ranged', RangedWeapon), ('melee', MeleeWeapon)):
            key = f'{weapon_type}_weapons'
            if key not in data:
                continue
            entries = data[key] or []
            ids = [int(e['weapon_id']) for e in entries]
            allowed = {w.id for w in model.query.filter(model.id.in_(ids), model.faction_id == unit.faction_id)} if ids else set()
            unit.set_loadout(weapon_type, [e for e in entries if int(e['weapon_id']) in allowed])

    @app.route('/api/data/units', methods=['POST'])
    def api_data_create_unit():
        """Create a new unit in a custom faction."""
        data = request.get_json()
        if not editable_faction(data.get('faction_id')):
            return read_only('Units can only be added to custom factions.')
        unit = Unit(id=next_custom_id(Unit))
        apply_unit_fields(unit, data)
        db.session.add(unit)
        db.session.flush()
        apply_unit_loadout(unit, data)
        save_data()
        return jsonify({'id': unit.id, 'message': 'Unit created'})

    @app.route('/api/data/units/<int:unit_id>', methods=['PUT'])
    def api_data_update_unit(unit_id):
        """
        Update a custom unit and its loadout. A BSData unit only takes 'added_modifiers' and
        'leader_buffs' (null = back to the ones read from BSData), which apply to that datasheet
        in every faction that has it.
        """
        unit = db.get_or_404(Unit, unit_id)
        data = request.get_json()
        if not unit.editable:
            if set(data) - {'added_modifiers', 'leader_buffs'}:
                return read_only('BSData units are read-only apart from added modifiers and leader buffs.')
            for same in Unit.query.filter_by(bsdata_id=unit.bsdata_id):
                if 'added_modifiers' in data:
                    same.added_modifiers = text_list(data.get('added_modifiers'))
                if 'leader_buffs' in data:
                    same.leader_buffs_override = None if data['leader_buffs'] is None else clean_buffs(data['leader_buffs'])
            save_data()
            return jsonify({'id': unit.id, 'message': 'Unit updated'})
        if not editable_faction(data.get('faction_id', unit.faction_id)):
            return read_only('Units can only be moved to custom factions.')
        apply_unit_fields(unit, data)
        db.session.flush()
        apply_unit_loadout(unit, data)
        save_data()
        return jsonify({'id': unit.id, 'message': 'Unit updated'})

    @app.route('/api/data/units/<int:unit_id>', methods=['DELETE'])
    def api_data_delete_unit(unit_id):
        """Delete a custom unit. Its faction's weapons stay."""
        unit = db.get_or_404(Unit, unit_id)
        if not unit.editable:
            return read_only()
        unit.set_loadout('ranged', [])
        unit.set_loadout('melee', [])
        db.session.delete(unit)
        save_data()
        return jsonify({'message': 'Unit deleted'})

    # --- Weapons ------------------------------------------------------------

    WEAPON_MODELS = {'ranged': RangedWeapon, 'melee': MeleeWeapon}

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

    def bsdata_profile(weapon_type, bsdata_id):
        """A BSData weapon with this profile ID (any faction: the profile is the same)."""
        if not bsdata_id:
            return None
        model = WEAPON_MODELS[weapon_type]
        return model.query.join(Faction).filter(model.bsdata_id == bsdata_id, Faction.source == 'bsdata').first()

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

    def apply_weapon_fields(weapon, weapon_type, data):
        weapon.faction_id = int(data['faction_id'])
        weapon.name = data['name'].strip()
        linked = bsdata_profile(weapon_type, (data.get('linked_bsdata_id') or '').strip() or None)
        weapon.linked_bsdata_id = linked.bsdata_id if linked else None
        if linked:  # Stats come from BSData (and are refreshed from it on every start)
            for field in LINKED_FIELDS[weapon_type]:
                setattr(weapon, field, getattr(linked, field))
            return
        weapon.attacks = str(data['attacks']).strip()
        weapon.strength = int(data['strength'])
        weapon.armor_penetration = abs(int(data['ap'] or 0))
        weapon.damage = str(data['damage']).strip()
        weapon.abilities = text_list(data.get('abilities'))
        skill = int_or_none(data.get('skill'))
        if weapon_type == 'ranged':
            weapon.ballistic_skill = skill
            weapon.range_inches = int_or_none(data.get('range'))
        else:
            weapon.weapon_skill = skill

    @app.route('/api/data/weapons', methods=['POST'])
    def api_data_create_weapon():
        """Create a new weapon for a custom faction."""
        data = request.get_json()
        if not editable_faction(data.get('faction_id')):
            return read_only('Weapons can only be added to custom factions.')
        weapon_type = 'ranged' if data.get('type') == 'ranged' else 'melee'
        weapon = WEAPON_MODELS[weapon_type](id=next_custom_id(WEAPON_MODELS[weapon_type]))
        apply_weapon_fields(weapon, weapon_type, data)
        db.session.add(weapon)
        save_data()
        return jsonify({'id': weapon.id, 'message': 'Weapon created'})

    @app.route('/api/data/weapons/<weapon_type>/<int:weapon_id>')
    def api_data_get_weapon(weapon_type, weapon_id):
        """Get a specific weapon."""
        model = WEAPON_MODELS.get(weapon_type, MeleeWeapon)
        return jsonify(weapon_dict(db.get_or_404(model, weapon_id)))

    @app.route('/api/data/weapons/<weapon_type>/<int:weapon_id>', methods=['PUT'])
    def api_data_update_weapon(weapon_type, weapon_id):
        """Update a weapon. Moving it to another faction drops it from other factions' units."""
        weapon_type = 'ranged' if weapon_type == 'ranged' else 'melee'
        weapon = db.get_or_404(WEAPON_MODELS[weapon_type], weapon_id)
        data = request.get_json()
        if not weapon.faction.editable or not editable_faction(data.get('faction_id', weapon.faction_id)):
            return read_only()
        apply_weapon_fields(weapon, weapon_type, data)
        db.session.flush()

        table = unit_ranged_weapons if weapon_type == 'ranged' else unit_melee_weapons
        other_units = [u.id for u in Unit.query.filter(Unit.faction_id != weapon.faction_id)]
        if other_units:
            db.session.execute(table.delete().where(
                (table.c.weapon_id == weapon.id) & (table.c.unit_id.in_(other_units))))
        save_data()
        return jsonify({'id': weapon.id, 'message': 'Weapon updated'})

    @app.route('/api/data/weapons/<weapon_type>/<int:weapon_id>', methods=['DELETE'])
    def api_data_delete_weapon(weapon_type, weapon_id):
        """Delete a weapon and remove it from every loadout."""
        weapon_type = 'ranged' if weapon_type == 'ranged' else 'melee'
        weapon = db.get_or_404(WEAPON_MODELS[weapon_type], weapon_id)
        if not weapon.faction.editable:
            return read_only()
        table = unit_ranged_weapons if weapon_type == 'ranged' else unit_melee_weapons
        db.session.execute(table.delete().where(table.c.weapon_id == weapon.id))
        db.session.delete(weapon)
        save_data()
        return jsonify({'message': 'Weapon deleted'})

    @app.route('/api/scenarios/<int:scenario_id>', methods=['PUT'])
    def api_update_scenario(scenario_id):
        """Update a scenario."""
        scenario = db.get_or_404(SavedScenario, scenario_id)
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
        scenarios = SavedScenario.query.order_by(SavedScenario.created_at.desc()).all()
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
    # Matchups (Matrix Comparison) Routes
    # =========================================================================

    @app.route('/matchups')
    def matchups_page():
        """Matrix comparison page for army analysis."""
        factions = Faction.query.order_by(Faction.name).all()
        factions_list = [{'id': f.id, 'name': f.name} for f in factions]
        return render_template('matchups.html', factions=factions_list)

    # Units without a points cost count as this many points per unit (placeholder until costs are entered)
    DEFAULT_UNIT_POINTS = 1

    def attacker_profile(weapon_type, weapon_id, models=1, extra_abilities=()):
        """Build an AttackerProfile for a library weapon (plus rule modifiers), or None if it no longer exists."""
        if weapon_type == 'ranged':
            weapon = db.session.get(RangedWeapon, weapon_id)
            skill = weapon.ballistic_skill if weapon else None
        else:
            weapon = db.session.get(MeleeWeapon, weapon_id)
            skill = weapon.weapon_skill if weapon else None
        if not weapon:
            return None, None
        return weapon, AttackerProfile(
            attacks=weapon.attacks,
            skill=skill,
            strength=weapon.strength,
            ap=weapon.armor_penetration,
            damage=weapon.damage,
            abilities=list(weapon.abilities or []) + list(extra_abilities),
            models=max(1, int(models or 1)),
            weapon_type=weapon_type,
        )

    def defender_profile(unit, model_count, extra_keywords=()):
        return DefenderProfile(
            toughness=unit.toughness,
            save=unit.save,
            wounds=unit.wounds,
            invulnerable_save=unit.invulnerable_save,
            feel_no_pain=unit.feel_no_pain,
            keywords=unit.target_keywords + list(extra_keywords),
            model_count=model_count
        )

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
        adds its points (its weapons come in the weapons list).
        """
        data = request.get_json()
        conditions = conditions_from_request(data.get('conditions', {}))

        # Army-wide rules and stratagems ticked for each side
        attacker_rules = selected_rules(data.get('attacker_rules'))
        defender_rules = selected_rules(data.get('defender_rules'))

        # Load attacker units and their included weapons
        attackers = []
        for atk in data.get('attackers', []):
            atk_unit = db.session.get(Unit, atk['unit_id']) if atk.get('unit_id') else None
            atk_leader = load_leader(atk.get('leader_id'))
            weapons = []
            for w in atk.get('weapons', []):
                w_type = 'melee' if w.get('weapon_type') == 'melee' else 'ranged'
                weapon_modifiers = (rule_modifiers(attacker_rules, 'Attacker', atk_unit.faction_id if atk_unit else None, w_type)
                                    + leader_modifiers(atk_leader, 'Attacker', w_type))
                weapon, profile = attacker_profile(w_type, w.get('weapon_id'), w.get('models', 1), weapon_modifiers)
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

            # Kept even if its weapons were deleted, so rows stay aligned with the request
            attackers.append({
                'label': atk.get('label') or (atk_unit.name if atk_unit else 'Unknown'),
                'points': points or DEFAULT_UNIT_POINTS,
                'weapons': weapons,
                'unit': atk_unit,
                'size': unit_size or 1,
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
                })

        # Each defender as seen by ranged and by melee attacks (target rules can be phase-specific)
        for d in defenders:
            d['profiles'] = {
                w_type: defender_profile(d['unit'], d['model_count'],
                                         rule_modifiers(defender_rules, 'Target', d['unit'].faction_id, w_type)
                                         + leader_modifiers(d['leader'], 'Target', w_type)
                                         + d['extra_modifiers'])
                for w_type in ('ranged', 'melee')
            }

        def weapon_part(result):
            return (result.average_damage, result.models_killed, result.overkill)

        # Average damage and kills of every weapon against every defender
        weapon_damage = [
            [
                [weapon_part(simulate_analytical(w['profile'], d['profiles'][w['type']], conditions))
                 for d in defenders]
                for w in atk['weapons']
            ]
            for atk in attackers
        ]

        matrix = []
        weapon_matrix = []
        for atk_idx, atk in enumerate(attackers):
            matrix.append([
                combine_cells([wd[def_idx] for wd in weapon_damage[atk_idx]], d['unit'], d['model_count'], atk['points'])
                for def_idx, d in enumerate(defenders)
            ])
            weapon_matrix.append([
                [combine_cells([weapon_damage[atk_idx][w_idx][def_idx]], d['unit'], d['model_count'], atk['points'])
                 for def_idx, d in enumerate(defenders)]
                for w_idx in range(len(atk['weapons']))
            ])

        total_points = sum(a['points'] for a in attackers)
        total_cells = [
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
            'defenders': [{'label': d['label'], 'points': d['points'], 'weight': d['weight']} for d in defenders],
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
                add_percentages(matrix[atk_idx][def_idx], d['model_count'], atk['points'])
                for weapon_cells in weapon_matrix[atk_idx]:
                    add_percentages(weapon_cells[def_idx], d['model_count'], atk['points'])
        for def_idx, d in enumerate(defenders):
            add_percentages(total_cells[def_idx], d['model_count'], total_points)
            total_cells[def_idx]['wiped'] = total_cells[def_idx]['kills_uncapped'] >= d['model_count']

        results['analysis'] = army_analysis(attackers, defenders, matrix, total_cells, total_points,
                                            conditions, data.get('weapon_filter', 'both'))
        return jsonify(results)

    def army_analysis(attackers, defenders, matrix, total_cells, total_points, conditions, weapon_filter):
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
                killed = min(cell['kills_uncapped'], d['model_count'])
                gaps.append({'label': d['label'], 'models': d['model_count'],
                             'survivors': round(d['model_count'] - killed, 2), 'pct_destroyed': cell['pct_destroyed']})
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
            target = defender_profile(role, role.models)
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
            'reverse': reverse_durability(attackers, defenders, conditions, weapon_filter),
        }

    def reverse_durability(attackers, defenders, conditions, weapon_filter):
        """
        The opponent's real units attacking mine (Excel's Reverse Durability): their loadouts with the
        default ticks, quantities scaled to their model counts, same conditions, no opponent rules.
        """
        if not defenders or any(not isinstance(d['unit'], Unit) for d in defenders):
            return None  # Real units mode only

        types = {'ranged': ['ranged'], 'melee': ['melee']}.get(weapon_filter, ['ranged', 'melee'])
        enemies = []
        for d in defenders:
            unit, size = d['unit'], d['model_count']
            scale = size / unit.default_models if unit.default_models else 1
            profiles = []
            for weapon_type in types:
                loadout = unit.loadout(weapon_type)
                for (weapon, qty), used in zip(loadout, default_used([w.name for w, _ in loadout])):
                    if used:
                        _, profile = attacker_profile(weapon_type, weapon.id, max(1, round(qty * scale)))
                        if profile:
                            profiles.append(profile)
            enemies.append({'label': d['label'], 'profiles': profiles, 'points': d['points']})

        rows = []
        for atk in attackers:
            unit = atk['unit']
            if unit is None:
                continue
            models = atk['size'] or unit.default_models or 1
            target = defender_profile(unit, models)
            cells = []
            for enemy in enemies:
                kills = sum(simulate_analytical(profile, target, conditions).models_killed for profile in enemy['profiles'])
                cells.append(round(min(1.0, kills / models) * 100, 1))
            rows.append({'label': atk['label'], 'models': models, 'cells': cells, 'total': round(min(100.0, sum(cells)), 1)})

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

        # Each weapon with its own unit's attacker rules, against the target as its phase sees it
        profiles, targets = [], []
        for w in data.get('weapons', []):
            w_type = 'melee' if w.get('weapon_type') == 'melee' else 'ranged'
            atk_unit = db.session.get(Unit, w['unit_id']) if w.get('unit_id') else None
            weapon_modifiers = (rule_modifiers(attacker_rules, 'Attacker', atk_unit.faction_id if atk_unit else None, w_type)
                                + leader_modifiers(load_leader(w.get('leader_id')), 'Attacker', w_type))
            _, profile = attacker_profile(w_type, w.get('weapon_id'), w.get('models', 1), weapon_modifiers)
            if profile:
                profiles.append(profile)
                targets.append(defender_profile(unit, model_count,
                                                rule_modifiers(defender_rules, 'Target', unit.faction_id, w_type)
                                                + leader_modifiers(defender_leader, 'Target', w_type)
                                                + extra_modifiers))
        if not profiles:
            return jsonify({'error': 'No weapons found'}), 400

        result = simulate_monte_carlo_combined(
            profiles, defender_profile(unit, model_count, extra_modifiers),
            conditions_from_request(data.get('conditions', {})), iterations,
            weapon_defenders=targets,
        )

        return jsonify({
            'summary': {
                'average_damage': result.average_damage,
                'models_killed': result.models_killed,
                'overkill': result.overkill,
                'kill_probability': result.kill_probability,
                'wipe_probability': result.wipe_probability,
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

    @app.route('/api/comparisons', methods=['GET'])
    def api_list_comparisons():
        """List saved matrix comparisons."""
        comparisons = SavedComparison.query.order_by(SavedComparison.created_at.desc()).all()
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
            results=data.get('results')
        )
        db.session.add(comparison)
        db.session.commit()

        return jsonify({'id': comparison.id, 'message': 'Comparison saved'})

    @app.route('/api/comparisons/<int:comparison_id>')
    def api_get_comparison(comparison_id):
        """Get a specific comparison with full config."""
        c = db.get_or_404(SavedComparison, comparison_id)
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
        comparison = db.get_or_404(SavedComparison, comparison_id)
        db.session.delete(comparison)
        db.session.commit()
        return jsonify({'message': 'Comparison deleted'})
