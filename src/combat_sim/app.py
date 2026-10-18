"""
Flask application for 40K Combat Simulator.
"""

import os
from flask import Flask, render_template, request, jsonify
from .models import db, init_db, Faction, Unit, RangedWeapon, MeleeWeapon, SavedScenario, SavedComparison
from .simulation import (
    simulate, simulate_analytical, AttackerProfile, DefenderProfile, BattleConditions
)


def create_app(config=None):
    """Application factory."""
    app = Flask(__name__)

    # Configuration
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-key-change-in-production')
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
        'DATABASE_URL',
        f'sqlite:///{os.path.join(os.path.dirname(__file__), "..", "..", "data", "combat_sim.db")}'
    )
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    if config:
        app.config.update(config)

    # Initialize database
    init_db(app)

    # Register routes
    register_routes(app)

    return app


def register_routes(app):
    """Register all application routes."""

    @app.route('/')
    def index():
        """Main simulation page."""
        factions = Faction.query.order_by(Faction.name).all()
        factions_list = [{'id': f.id, 'name': f.name} for f in factions]
        return render_template('index.html', factions=factions_list)

    @app.route('/api/units')
    def api_units():
        """Get units, optionally filtered by faction."""
        faction_id = request.args.get('faction_id', type=int)
        search = request.args.get('search', '').strip()

        query = Unit.query

        if faction_id:
            query = query.filter(Unit.faction_id == faction_id)

        if search:
            query = query.filter(Unit.name.ilike(f'%{search}%'))

        units = query.order_by(Unit.name).limit(50).all()
        return jsonify([u.to_dict() for u in units])

    @app.route('/api/units/<int:unit_id>')
    def api_unit(unit_id):
        """Get a specific unit with its weapons."""
        unit = Unit.query.get_or_404(unit_id)
        data = unit.to_dict()
        data['ranged_weapons'] = [w.to_dict() for w in unit.ranged_weapons]
        data['melee_weapons'] = [w.to_dict() for w in unit.melee_weapons]
        return jsonify(data)

    @app.route('/api/weapons')
    def api_weapons():
        """Get weapons, optionally filtered by unit or type."""
        unit_id = request.args.get('unit_id', type=int)
        weapon_type = request.args.get('type', 'all')
        search = request.args.get('search', '').strip()

        weapons = []

        if weapon_type in ('all', 'ranged'):
            query = RangedWeapon.query
            if unit_id:
                query = query.filter(RangedWeapon.unit_id == unit_id)
            if search:
                query = query.filter(RangedWeapon.name.ilike(f'%{search}%'))
            weapons.extend([w.to_dict() for w in query.limit(25).all()])

        if weapon_type in ('all', 'melee'):
            query = MeleeWeapon.query
            if unit_id:
                query = query.filter(MeleeWeapon.unit_id == unit_id)
            if search:
                query = query.filter(MeleeWeapon.name.ilike(f'%{search}%'))
            weapons.extend([w.to_dict() for w in query.limit(25).all()])

        return jsonify(weapons)

    @app.route('/api/simulate', methods=['POST'])
    def api_simulate():
        """Run a combat simulation."""
        data = request.get_json()

        # Build attacker profile
        attacker = AttackerProfile(
            attacks=str(data['attacker']['attacks']),
            skill=int(data['attacker']['skill']),
            strength=int(data['attacker']['strength']),
            ap=int(data['attacker']['ap']),
            damage=str(data['attacker']['damage']),
            abilities=data['attacker'].get('abilities', [])
        )

        # Build defender profile
        defender = DefenderProfile(
            toughness=int(data['defender']['toughness']),
            save=int(data['defender']['save']),
            wounds=int(data['defender']['wounds']),
            invulnerable_save=data['defender'].get('invulnerable_save'),
            feel_no_pain=data['defender'].get('feel_no_pain'),
            keywords=data['defender'].get('keywords', []),
            model_count=int(data['defender'].get('model_count', 1))
        )

        # Build conditions
        conditions = BattleConditions(
            half_range=data['conditions'].get('half_range', False),
            charged=data['conditions'].get('charged', False),
            stationary=data['conditions'].get('stationary', False),
            cover=data['conditions'].get('cover', False),
            reroll_hits=data['conditions'].get('reroll_hits', 'none'),
            reroll_wounds=data['conditions'].get('reroll_wounds', 'none')
        )

        # Run simulation
        method = data.get('method', 'analytical')
        iterations = int(data.get('iterations', 1000))

        result = simulate(attacker, defender, conditions, method, iterations)

        return jsonify({
            'summary': {
                'average_damage': result.average_damage,
                'models_killed': result.models_killed,
                'overkill': result.overkill,
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

    @app.route('/api/scenarios', methods=['GET'])
    def api_list_scenarios():
        """List saved scenarios."""
        scenarios = SavedScenario.query.order_by(SavedScenario.created_at.desc()).all()
        return jsonify([{
            'id': s.id,
            'name': s.name,
            'created_at': s.created_at.isoformat(),
            'results': s.results,
        } for s in scenarios])

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
        scenario = SavedScenario.query.get_or_404(scenario_id)
        db.session.delete(scenario)
        db.session.commit()
        return jsonify({'message': 'Scenario deleted'})

    @app.route('/api/backup', methods=['GET'])
    def api_backup():
        """Export database as JSON backup."""
        backup = {
            'factions': [],
            'units': [],
            'ranged_weapons': [],
            'melee_weapons': [],
            'scenarios': [],
        }

        for faction in Faction.query.all():
            backup['factions'].append({
                'id': faction.id,
                'name': faction.name,
            })

        for unit in Unit.query.all():
            backup['units'].append({
                'id': unit.id,
                'faction_id': unit.faction_id,
                'name': unit.name,
                'toughness': unit.toughness,
                'save': unit.save,
                'wounds': unit.wounds,
                'invulnerable_save': unit.invulnerable_save,
                'feel_no_pain': unit.feel_no_pain,
                'keywords': unit.keywords,
            })

        for weapon in RangedWeapon.query.all():
            backup['ranged_weapons'].append({
                'id': weapon.id,
                'unit_id': weapon.unit_id,
                'name': weapon.name,
                'attacks': weapon.attacks,
                'ballistic_skill': weapon.ballistic_skill,
                'strength': weapon.strength,
                'armor_penetration': weapon.armor_penetration,
                'damage': weapon.damage,
                'range_inches': weapon.range_inches,
                'abilities': weapon.abilities,
            })

        for weapon in MeleeWeapon.query.all():
            backup['melee_weapons'].append({
                'id': weapon.id,
                'unit_id': weapon.unit_id,
                'name': weapon.name,
                'attacks': weapon.attacks,
                'weapon_skill': weapon.weapon_skill,
                'strength': weapon.strength,
                'armor_penetration': weapon.armor_penetration,
                'damage': weapon.damage,
                'abilities': weapon.abilities,
            })

        for scenario in SavedScenario.query.all():
            backup['scenarios'].append({
                'id': scenario.id,
                'name': scenario.name,
                'attacker_config': scenario.attacker_config,
                'defender_config': scenario.defender_config,
                'battle_conditions': scenario.battle_conditions,
                'results': scenario.results,
            })

        return jsonify(backup)

    @app.route('/api/restore', methods=['POST'])
    def api_restore():
        """Restore database from JSON backup."""
        data = request.get_json()

        # Clear existing data
        SavedScenario.query.delete()
        MeleeWeapon.query.delete()
        RangedWeapon.query.delete()
        Unit.query.delete()
        Faction.query.delete()

        # Restore factions
        for f in data.get('factions', []):
            faction = Faction(id=f['id'], name=f['name'])
            db.session.add(faction)

        db.session.flush()

        # Restore units
        for u in data.get('units', []):
            unit = Unit(
                id=u['id'],
                faction_id=u['faction_id'],
                name=u['name'],
                toughness=u['toughness'],
                save=u['save'],
                wounds=u['wounds'],
                invulnerable_save=u.get('invulnerable_save'),
                feel_no_pain=u.get('feel_no_pain'),
                keywords=u.get('keywords', []),
            )
            db.session.add(unit)

        db.session.flush()

        # Restore weapons
        for w in data.get('ranged_weapons', []):
            weapon = RangedWeapon(
                id=w['id'],
                unit_id=w['unit_id'],
                name=w['name'],
                attacks=w['attacks'],
                ballistic_skill=w['ballistic_skill'],
                strength=w['strength'],
                armor_penetration=w['armor_penetration'],
                damage=w['damage'],
                range_inches=w.get('range_inches', 24),
                abilities=w.get('abilities', []),
            )
            db.session.add(weapon)

        for w in data.get('melee_weapons', []):
            weapon = MeleeWeapon(
                id=w['id'],
                unit_id=w['unit_id'],
                name=w['name'],
                attacks=w['attacks'],
                weapon_skill=w['weapon_skill'],
                strength=w['strength'],
                armor_penetration=w['armor_penetration'],
                damage=w['damage'],
                abilities=w.get('abilities', []),
            )
            db.session.add(weapon)

        # Restore scenarios
        for s in data.get('scenarios', []):
            scenario = SavedScenario(
                id=s['id'],
                name=s['name'],
                attacker_config=s['attacker_config'],
                defender_config=s['defender_config'],
                battle_conditions=s['battle_conditions'],
                results=s.get('results'),
            )
            db.session.add(scenario)

        db.session.commit()

        return jsonify({'message': 'Database restored successfully'})

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
    # =========================================================================

    @app.route('/api/data/factions')
    def api_data_factions():
        """Get all factions with unit counts."""
        factions = Faction.query.order_by(Faction.name).all()
        return jsonify([{
            'id': f.id,
            'name': f.name,
            'unit_count': f.units.count()
        } for f in factions])

    @app.route('/api/data/factions', methods=['POST'])
    def api_data_create_faction():
        """Create a new faction."""
        data = request.get_json()
        faction = Faction(name=data['name'])
        db.session.add(faction)
        db.session.commit()
        return jsonify({'id': faction.id, 'name': faction.name})

    @app.route('/api/data/factions/<int:faction_id>', methods=['PUT'])
    def api_data_update_faction(faction_id):
        """Update a faction."""
        faction = Faction.query.get_or_404(faction_id)
        data = request.get_json()
        faction.name = data.get('name', faction.name)
        db.session.commit()
        return jsonify({'id': faction.id, 'name': faction.name})

    @app.route('/api/data/factions/<int:faction_id>', methods=['DELETE'])
    def api_data_delete_faction(faction_id):
        """Delete a faction and all its units."""
        faction = Faction.query.get_or_404(faction_id)
        # Delete all units (cascades to weapons)
        for unit in faction.units:
            RangedWeapon.query.filter_by(unit_id=unit.id).delete()
            MeleeWeapon.query.filter_by(unit_id=unit.id).delete()
        Unit.query.filter_by(faction_id=faction_id).delete()
        db.session.delete(faction)
        db.session.commit()
        return jsonify({'message': 'Faction deleted'})

    @app.route('/api/data/units')
    def api_data_units():
        """Get all units with faction info."""
        faction_id = request.args.get('faction_id', type=int)
        query = Unit.query.join(Faction)

        if faction_id:
            query = query.filter(Unit.faction_id == faction_id)

        units = query.order_by(Faction.name, Unit.name).all()
        return jsonify([{
            'id': u.id,
            'name': u.name,
            'faction': u.faction.name,
            'faction_id': u.faction_id,
            'toughness': u.toughness,
            'save': u.save,
            'wounds': u.wounds,
            'invulnerable_save': u.invulnerable_save,
            'feel_no_pain': u.feel_no_pain,
            'keywords': u.keywords or []
        } for u in units])

    @app.route('/api/data/units', methods=['POST'])
    def api_data_create_unit():
        """Create a new unit."""
        data = request.get_json()
        unit = Unit(
            faction_id=data['faction_id'],
            name=data['name'],
            toughness=data['toughness'],
            save=data['save'],
            wounds=data['wounds'],
            invulnerable_save=data.get('invulnerable_save'),
            feel_no_pain=data.get('feel_no_pain'),
            keywords=data.get('keywords', [])
        )
        db.session.add(unit)
        db.session.commit()
        return jsonify({'id': unit.id, 'message': 'Unit created'})

    @app.route('/api/data/units/<int:unit_id>', methods=['PUT'])
    def api_data_update_unit(unit_id):
        """Update a unit."""
        unit = Unit.query.get_or_404(unit_id)
        data = request.get_json()

        unit.faction_id = data.get('faction_id', unit.faction_id)
        unit.name = data.get('name', unit.name)
        unit.toughness = data.get('toughness', unit.toughness)
        unit.save = data.get('save', unit.save)
        unit.wounds = data.get('wounds', unit.wounds)
        unit.invulnerable_save = data.get('invulnerable_save')
        unit.feel_no_pain = data.get('feel_no_pain')
        unit.keywords = data.get('keywords', unit.keywords)

        db.session.commit()
        return jsonify({'id': unit.id, 'message': 'Unit updated'})

    @app.route('/api/data/units/<int:unit_id>', methods=['DELETE'])
    def api_data_delete_unit(unit_id):
        """Delete a unit and all its weapons."""
        unit = Unit.query.get_or_404(unit_id)
        RangedWeapon.query.filter_by(unit_id=unit_id).delete()
        MeleeWeapon.query.filter_by(unit_id=unit_id).delete()
        db.session.delete(unit)
        db.session.commit()
        return jsonify({'message': 'Unit deleted'})

    @app.route('/api/data/weapons')
    def api_data_weapons():
        """Get all weapons."""
        weapon_type = request.args.get('type', 'all')
        weapons = []

        if weapon_type in ('all', 'ranged'):
            for w in RangedWeapon.query.join(Unit).order_by(Unit.name, RangedWeapon.name).all():
                weapons.append({
                    'id': w.id,
                    'type': 'ranged',
                    'name': w.name,
                    'unit_id': w.unit_id,
                    'unit_name': w.unit.name,
                    'attacks': w.attacks,
                    'skill': w.ballistic_skill,
                    'strength': w.strength,
                    'ap': w.armor_penetration,
                    'damage': w.damage,
                    'abilities': w.abilities or []
                })

        if weapon_type in ('all', 'melee'):
            for w in MeleeWeapon.query.join(Unit).order_by(Unit.name, MeleeWeapon.name).all():
                weapons.append({
                    'id': w.id,
                    'type': 'melee',
                    'name': w.name,
                    'unit_id': w.unit_id,
                    'unit_name': w.unit.name,
                    'attacks': w.attacks,
                    'skill': w.weapon_skill,
                    'strength': w.strength,
                    'ap': w.armor_penetration,
                    'damage': w.damage,
                    'abilities': w.abilities or []
                })

        return jsonify(weapons)

    @app.route('/api/data/weapons', methods=['POST'])
    def api_data_create_weapon():
        """Create a new weapon."""
        data = request.get_json()

        if data['type'] == 'ranged':
            weapon = RangedWeapon(
                unit_id=data['unit_id'],
                name=data['name'],
                attacks=data['attacks'],
                ballistic_skill=data['skill'],
                strength=data['strength'],
                armor_penetration=data['ap'],
                damage=data['damage'],
                abilities=data.get('abilities', [])
            )
        else:
            weapon = MeleeWeapon(
                unit_id=data['unit_id'],
                name=data['name'],
                attacks=data['attacks'],
                weapon_skill=data['skill'],
                strength=data['strength'],
                armor_penetration=data['ap'],
                damage=data['damage'],
                abilities=data.get('abilities', [])
            )

        db.session.add(weapon)
        db.session.commit()
        return jsonify({'id': weapon.id, 'message': 'Weapon created'})

    @app.route('/api/data/weapons/<weapon_type>/<int:weapon_id>')
    def api_data_get_weapon(weapon_type, weapon_id):
        """Get a specific weapon."""
        if weapon_type == 'ranged':
            w = RangedWeapon.query.get_or_404(weapon_id)
            return jsonify({
                'id': w.id,
                'type': 'ranged',
                'name': w.name,
                'unit_id': w.unit_id,
                'attacks': w.attacks,
                'skill': w.ballistic_skill,
                'strength': w.strength,
                'ap': w.armor_penetration,
                'damage': w.damage,
                'abilities': w.abilities or []
            })
        else:
            w = MeleeWeapon.query.get_or_404(weapon_id)
            return jsonify({
                'id': w.id,
                'type': 'melee',
                'name': w.name,
                'unit_id': w.unit_id,
                'attacks': w.attacks,
                'skill': w.weapon_skill,
                'strength': w.strength,
                'ap': w.armor_penetration,
                'damage': w.damage,
                'abilities': w.abilities or []
            })

    @app.route('/api/data/weapons/<weapon_type>/<int:weapon_id>', methods=['PUT'])
    def api_data_update_weapon(weapon_type, weapon_id):
        """Update a weapon."""
        data = request.get_json()

        if weapon_type == 'ranged':
            weapon = RangedWeapon.query.get_or_404(weapon_id)
            weapon.ballistic_skill = data.get('skill', weapon.ballistic_skill)
        else:
            weapon = MeleeWeapon.query.get_or_404(weapon_id)
            weapon.weapon_skill = data.get('skill', weapon.weapon_skill)

        weapon.unit_id = data.get('unit_id', weapon.unit_id)
        weapon.name = data.get('name', weapon.name)
        weapon.attacks = data.get('attacks', weapon.attacks)
        weapon.strength = data.get('strength', weapon.strength)
        weapon.armor_penetration = data.get('ap', weapon.armor_penetration)
        weapon.damage = data.get('damage', weapon.damage)
        weapon.abilities = data.get('abilities', weapon.abilities)

        db.session.commit()
        return jsonify({'id': weapon.id, 'message': 'Weapon updated'})

    @app.route('/api/data/weapons/<weapon_type>/<int:weapon_id>', methods=['DELETE'])
    def api_data_delete_weapon(weapon_type, weapon_id):
        """Delete a weapon."""
        if weapon_type == 'ranged':
            weapon = RangedWeapon.query.get_or_404(weapon_id)
        else:
            weapon = MeleeWeapon.query.get_or_404(weapon_id)

        db.session.delete(weapon)
        db.session.commit()
        return jsonify({'message': 'Weapon deleted'})

    @app.route('/api/scenarios/<int:scenario_id>', methods=['PUT'])
    def api_update_scenario(scenario_id):
        """Update a scenario."""
        scenario = SavedScenario.query.get_or_404(scenario_id)
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

    @app.route('/api/matrix/simulate', methods=['POST'])
    def api_matrix_simulate():
        """
        Run N×M matrix simulation.

        Request body:
        {
            "attackers": [
                {"weapon_id": 1, "weapon_type": "ranged"},
                {"weapon_id": 2, "weapon_type": "melee"}
            ],
            "defenders": [
                {"unit_id": 1, "model_count": 5},
                {"unit_id": 2, "model_count": 1}
            ],
            "conditions": {
                "half_range": false,
                "charged": false,
                "stationary": false,
                "cover": false,
                "reroll_hits": "none",
                "reroll_wounds": "none"
            },
            "metrics": ["avg_damage", "models_killed", "damage_per_point", "kill_probability"]
        }
        """
        data = request.get_json()

        attackers_config = data.get('attackers', [])
        defenders_config = data.get('defenders', [])
        conditions_data = data.get('conditions', {})
        requested_metrics = data.get('metrics', ['avg_damage', 'models_killed'])

        # Build conditions
        conditions = BattleConditions(
            half_range=conditions_data.get('half_range', False),
            charged=conditions_data.get('charged', False),
            stationary=conditions_data.get('stationary', False),
            cover=conditions_data.get('cover', False),
            reroll_hits=conditions_data.get('reroll_hits', 'none'),
            reroll_wounds=conditions_data.get('reroll_wounds', 'none')
        )

        # Load attackers (weapons)
        attackers = []
        for atk in attackers_config:
            weapon_type = atk.get('weapon_type', 'ranged')
            weapon_id = atk.get('weapon_id')

            if weapon_type == 'ranged':
                weapon = RangedWeapon.query.get(weapon_id)
                if weapon:
                    attackers.append({
                        'weapon': weapon,
                        'type': 'ranged',
                        'label': f"{weapon.unit.name} - {weapon.name}",
                        'points': (weapon.unit.points_cost or 0) + (weapon.points_cost or 0)
                    })
            else:
                weapon = MeleeWeapon.query.get(weapon_id)
                if weapon:
                    attackers.append({
                        'weapon': weapon,
                        'type': 'melee',
                        'label': f"{weapon.unit.name} - {weapon.name}",
                        'points': (weapon.unit.points_cost or 0) + (weapon.points_cost or 0)
                    })

        # Load defenders (units)
        defenders = []
        for def_config in defenders_config:
            unit_id = def_config.get('unit_id')
            model_count = def_config.get('model_count', 1)
            unit = Unit.query.get(unit_id)
            if unit:
                defenders.append({
                    'unit': unit,
                    'model_count': model_count,
                    'label': f"{unit.name} (x{model_count})",
                    'points': (unit.points_cost or 0) * model_count
                })

        # Run simulations for each attacker × defender pair
        results = {
            'attackers': [{'label': a['label'], 'points': a['points']} for a in attackers],
            'defenders': [{'label': d['label'], 'points': d['points']} for d in defenders],
            'matrix': [],  # [attacker_index][defender_index] = metrics
            'summary': {
                'best_attackers': [],  # Best attacker for each defender
                'biggest_threats': []  # Most dangerous attacker for each defender
            }
        }

        for atk_idx, atk in enumerate(attackers):
            row = []
            weapon = atk['weapon']

            # Build attacker profile
            if atk['type'] == 'ranged':
                attacker_profile = AttackerProfile(
                    attacks=weapon.attacks,
                    skill=weapon.ballistic_skill,
                    strength=weapon.strength,
                    ap=weapon.armor_penetration,
                    damage=weapon.damage,
                    abilities=weapon.abilities or []
                )
            else:
                attacker_profile = AttackerProfile(
                    attacks=weapon.attacks,
                    skill=weapon.weapon_skill,
                    strength=weapon.strength,
                    ap=weapon.armor_penetration,
                    damage=weapon.damage,
                    abilities=weapon.abilities or []
                )

            for def_idx, def_data in enumerate(defenders):
                unit = def_data['unit']
                model_count = def_data['model_count']

                # Build defender profile
                defender_profile = DefenderProfile(
                    toughness=unit.toughness,
                    save=unit.save,
                    wounds=unit.wounds,
                    invulnerable_save=unit.invulnerable_save,
                    feel_no_pain=unit.feel_no_pain,
                    keywords=unit.keywords or [],
                    model_count=model_count
                )

                # Run analytical simulation
                result = simulate_analytical(attacker_profile, defender_profile, conditions)

                # Calculate metrics
                cell_metrics = {
                    'avg_damage': round(result.average_damage, 2),
                    'models_killed': round(result.models_killed, 2),
                    'overkill': round(result.overkill, 2)
                }

                # Damage per point (attacker points)
                if atk['points'] > 0:
                    cell_metrics['damage_per_point'] = round(result.average_damage / atk['points'], 3)
                else:
                    cell_metrics['damage_per_point'] = result.average_damage

                # Kill probability (chance to kill at least 1 model)
                # For analytical, approximate from expected wounds
                total_wounds_in_squad = unit.wounds * model_count
                if total_wounds_in_squad > 0 and result.average_damage > 0:
                    # Rough approximation: P(kill >= 1) based on expected damage
                    kill_prob = min(1.0, result.average_damage / unit.wounds)
                    cell_metrics['kill_probability'] = round(kill_prob * 100, 1)
                else:
                    cell_metrics['kill_probability'] = 0

                row.append(cell_metrics)

            results['matrix'].append(row)

        # Calculate summary: best attacker for each defender (by damage per point)
        for def_idx, def_data in enumerate(defenders):
            best_atk_idx = 0
            best_efficiency = -1

            for atk_idx in range(len(attackers)):
                efficiency = results['matrix'][atk_idx][def_idx].get('damage_per_point', 0)
                if efficiency > best_efficiency:
                    best_efficiency = efficiency
                    best_atk_idx = atk_idx

            results['summary']['best_attackers'].append({
                'defender': def_data['label'],
                'attacker': attackers[best_atk_idx]['label'] if attackers else None,
                'attacker_idx': best_atk_idx,
                'efficiency': best_efficiency
            })

        # Biggest threats: highest avg damage for each defender
        for def_idx, def_data in enumerate(defenders):
            max_damage_idx = 0
            max_damage = -1

            for atk_idx in range(len(attackers)):
                damage = results['matrix'][atk_idx][def_idx].get('avg_damage', 0)
                if damage > max_damage:
                    max_damage = damage
                    max_damage_idx = atk_idx

            results['summary']['biggest_threats'].append({
                'defender': def_data['label'],
                'attacker': attackers[max_damage_idx]['label'] if attackers else None,
                'attacker_idx': max_damage_idx,
                'damage': max_damage
            })

        return jsonify(results)

    @app.route('/api/matrix/cell', methods=['POST'])
    def api_matrix_cell_detail():
        """
        Get detailed Monte Carlo results for a specific cell.
        Used when user clicks a cell in the matrix for detailed distribution.
        """
        data = request.get_json()

        weapon_id = data.get('weapon_id')
        weapon_type = data.get('weapon_type', 'ranged')
        unit_id = data.get('unit_id')
        model_count = data.get('model_count', 1)
        conditions_data = data.get('conditions', {})
        iterations = data.get('iterations', 1000)

        # Load weapon
        if weapon_type == 'ranged':
            weapon = RangedWeapon.query.get_or_404(weapon_id)
            attacker = AttackerProfile(
                attacks=weapon.attacks,
                skill=weapon.ballistic_skill,
                strength=weapon.strength,
                ap=weapon.armor_penetration,
                damage=weapon.damage,
                abilities=weapon.abilities or []
            )
        else:
            weapon = MeleeWeapon.query.get_or_404(weapon_id)
            attacker = AttackerProfile(
                attacks=weapon.attacks,
                skill=weapon.weapon_skill,
                strength=weapon.strength,
                ap=weapon.armor_penetration,
                damage=weapon.damage,
                abilities=weapon.abilities or []
            )

        # Load unit
        unit = Unit.query.get_or_404(unit_id)
        defender = DefenderProfile(
            toughness=unit.toughness,
            save=unit.save,
            wounds=unit.wounds,
            invulnerable_save=unit.invulnerable_save,
            feel_no_pain=unit.feel_no_pain,
            keywords=unit.keywords or [],
            model_count=model_count
        )

        # Build conditions
        conditions = BattleConditions(
            half_range=conditions_data.get('half_range', False),
            charged=conditions_data.get('charged', False),
            stationary=conditions_data.get('stationary', False),
            cover=conditions_data.get('cover', False),
            reroll_hits=conditions_data.get('reroll_hits', 'none'),
            reroll_wounds=conditions_data.get('reroll_wounds', 'none')
        )

        # Run Monte Carlo simulation
        result = simulate(attacker, defender, conditions, method='monte_carlo', iterations=iterations)

        return jsonify({
            'attacker': f"{weapon.unit.name} - {weapon.name}",
            'defender': f"{unit.name} (x{model_count})",
            'summary': {
                'average_damage': result.average_damage,
                'models_killed': result.models_killed,
                'overkill': result.overkill,
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
        c = SavedComparison.query.get_or_404(comparison_id)
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
        comparison = SavedComparison.query.get_or_404(comparison_id)
        db.session.delete(comparison)
        db.session.commit()
        return jsonify({'message': 'Comparison deleted'})
