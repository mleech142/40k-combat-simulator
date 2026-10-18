#!/usr/bin/env python
"""
Excel Migration Script - Updated for actual 40KCombatSimulator.xlsx structure

Parses the Excel file and populates the SQLite database.

Excel Structure (per faction sheet):
- Unit Stats: Columns B-H (Name, T, W, Sv, InSv, FNP, Modifiers)
- Ranged Weapons: Columns I-S (Weapon, Type, ..., A, BS, S, AP, D, Modifier)
- Melee Weapons: Columns V-AB (Weapon, A, WS, S, AP, D, Modifier)
- Faction Rules: Columns AE-AH (Rule, Applies To, Phase, Modifier)
- Stratagems: Columns AJ-AO (Stratagem, CP, Applies To, Phase, Modifier, Notes)

Headers in Row 3, Data starts Row 4.
"""

import os
import sys
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

import pandas as pd
from openpyxl import load_workbook

from combat_sim.app import create_app
from combat_sim.models import db, Faction, Unit, RangedWeapon, MeleeWeapon, FactionRule, Stratagem


FACTION_SHEETS = [
    'DeathGuard',
    'Space Marines',
    'Drukhari',
    'Daemons',
    "T'Au",
    'Imperial Guard',
    'Sisters of Battle',
    'Thousand Sons',
]

# Column mappings (0-indexed)
UNIT_COLS = {
    'name': 1,      # B
    't': 2,         # C
    'w': 3,         # D
    'sv': 4,        # E
    'inv': 5,       # F
    'fnp': 6,       # G
    'modifiers': 7, # H
}

RANGED_COLS = {
    'name': 8,      # I
    'type': 9,      # J
    'name2': 12,    # M (duplicate name)
    'a': 13,        # N
    'bs': 14,       # O
    's': 15,        # P
    'ap': 16,       # Q
    'd': 17,        # R
    'abilities': 18, # S
}

MELEE_COLS = {
    'name': 21,     # V
    'a': 22,        # W
    'ws': 23,       # X
    's': 24,        # Y
    'ap': 25,       # Z
    'd': 26,        # AA
    'abilities': 27, # AB
}

RULE_COLS = {
    'name': 30,      # AE
    'applies_to': 31, # AF
    'phase': 32,     # AG
    'modifier': 33,  # AH
}

STRATAGEM_COLS = {
    'name': 35,      # AJ
    'cp': 36,        # AK
    'applies_to': 37, # AL
    'phase': 38,     # AM
    'modifier': 39,  # AN
    'notes': 40,     # AO
}


def clean_value(val):
    """Clean a cell value."""
    if pd.isna(val) or val is None:
        return None
    val = str(val).strip()
    if val in ('', '-', 'None', 'nan'):
        return None
    return val


def parse_stat(val, default=None):
    """Parse a numeric stat, handling 3+, -, etc."""
    val = clean_value(val)
    if val is None:
        return default

    # Remove + suffix
    val = val.rstrip('+')

    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default


def parse_dice(val):
    """Parse attacks/damage which can be numbers or dice expressions."""
    val = clean_value(val)
    if val is None:
        return '1'
    return val.upper()


def parse_abilities(val):
    """Parse abilities string into list."""
    val = clean_value(val)
    if val is None:
        return []

    # Split on comma, clean each
    abilities = [a.strip() for a in val.split(',')]
    return [a for a in abilities if a]


def migrate_faction(wb, sheet_name, app):
    """Migrate a single faction sheet."""
    print(f"\n  Processing: {sheet_name}")

    try:
        ws = wb[sheet_name]
    except KeyError:
        print(f"    Sheet not found: {sheet_name}")
        return

    with app.app_context():
        # Create or get faction
        faction = Faction.query.filter_by(name=sheet_name).first()
        if not faction:
            faction = Faction(name=sheet_name)
            db.session.add(faction)
            db.session.flush()

        # Clear existing data for this faction
        Unit.query.filter_by(faction_id=faction.id).delete()
        FactionRule.query.filter_by(faction_id=faction.id).delete()
        Stratagem.query.filter_by(faction_id=faction.id).delete()
        db.session.flush()

        units_added = 0
        ranged_added = 0
        melee_added = 0
        rules_added = 0

        # Read all data starting from row 4 (index 3)
        # Create a mapping of unit names to unit objects for weapon assignment
        unit_map = {}

        for row_idx in range(4, ws.max_row + 1):
            row = [ws.cell(row=row_idx, column=col+1).value for col in range(42)]

            # Parse unit
            unit_name = clean_value(row[UNIT_COLS['name']])
            if unit_name and unit_name.lower() not in ('name', 'unit'):
                t = parse_stat(row[UNIT_COLS['t']])
                w = parse_stat(row[UNIT_COLS['w']])
                sv = parse_stat(row[UNIT_COLS['sv']])
                inv = parse_stat(row[UNIT_COLS['inv']])
                fnp = parse_stat(row[UNIT_COLS['fnp']])
                modifiers = clean_value(row[UNIT_COLS['modifiers']])

                if t is not None and sv is not None:
                    # Parse keywords from modifiers if present
                    keywords = []
                    if modifiers:
                        keywords = parse_abilities(modifiers)

                    unit = Unit(
                        faction_id=faction.id,
                        name=unit_name,
                        toughness=t,
                        save=sv,
                        wounds=w or 1,
                        invulnerable_save=inv,
                        feel_no_pain=fnp,
                        keywords=keywords
                    )
                    db.session.add(unit)
                    db.session.flush()
                    unit_map[unit_name] = unit
                    units_added += 1

            # Parse ranged weapon
            weapon_name = clean_value(row[RANGED_COLS['name']]) or clean_value(row[RANGED_COLS['name2']])
            if weapon_name and weapon_name.lower() not in ('weapon', 'name'):
                a = parse_dice(row[RANGED_COLS['a']])
                bs = parse_stat(row[RANGED_COLS['bs']])
                s = parse_stat(row[RANGED_COLS['s']])
                ap = parse_stat(row[RANGED_COLS['ap']], 0)
                d = parse_dice(row[RANGED_COLS['d']])
                abilities = parse_abilities(row[RANGED_COLS['abilities']])

                if bs is not None and s is not None:
                    # Assign to most recent unit or first unit
                    target_unit = list(unit_map.values())[-1] if unit_map else None

                    if target_unit:
                        weapon = RangedWeapon(
                            unit_id=target_unit.id,
                            name=weapon_name,
                            attacks=a,
                            ballistic_skill=bs,
                            strength=s,
                            armor_penetration=abs(ap) if ap else 0,
                            damage=d,
                            range_inches=24,
                            abilities=abilities
                        )
                        db.session.add(weapon)
                        ranged_added += 1

            # Parse melee weapon
            melee_name = clean_value(row[MELEE_COLS['name']])
            if melee_name and melee_name.lower() not in ('weapon', 'name'):
                a = parse_dice(row[MELEE_COLS['a']])
                ws = parse_stat(row[MELEE_COLS['ws']])
                s = parse_stat(row[MELEE_COLS['s']])
                ap = parse_stat(row[MELEE_COLS['ap']], 0)
                d = parse_dice(row[MELEE_COLS['d']])
                abilities = parse_abilities(row[MELEE_COLS['abilities']])

                if ws is not None and s is not None:
                    target_unit = list(unit_map.values())[-1] if unit_map else None

                    if target_unit:
                        weapon = MeleeWeapon(
                            unit_id=target_unit.id,
                            name=melee_name,
                            attacks=a,
                            weapon_skill=ws,
                            strength=s,
                            armor_penetration=abs(ap) if ap else 0,
                            damage=d,
                            abilities=abilities
                        )
                        db.session.add(weapon)
                        melee_added += 1

            # Parse faction rule
            rule_name = clean_value(row[RULE_COLS['name']])
            if rule_name and rule_name.lower() not in ('rule', 'name', 'example rule (edit or delete)'):
                applies_to = clean_value(row[RULE_COLS['applies_to']]) or 'Attacker'
                phase = clean_value(row[RULE_COLS['phase']]) or 'Both'
                modifier = clean_value(row[RULE_COLS['modifier']])

                if modifier:
                    effect = {'raw': modifier}
                    # Try to parse modifier
                    if 'Hit' in modifier and '+' in modifier:
                        effect['hit_modifier'] = 1
                    elif 'Wound' in modifier and '+' in modifier:
                        effect['wound_modifier'] = 1

                    rule = FactionRule(
                        faction_id=faction.id,
                        name=rule_name,
                        description=modifier,
                        effect=effect
                    )
                    db.session.add(rule)
                    rules_added += 1

        db.session.commit()
        print(f"    Units: {units_added}, Ranged: {ranged_added}, Melee: {melee_added}, Rules: {rules_added}")


def main():
    print("\n" + "="*60)
    print("40K Combat Simulator - Excel Migration")
    print("="*60)

    excel_path = Path(__file__).parent.parent / '40KCombatSimulator.xlsx'

    if not excel_path.exists():
        print(f"\nExcel file not found: {excel_path}")
        return

    print(f"\nFound Excel file: {excel_path}")

    app = create_app()

    with app.app_context():
        db.create_all()
        print("Database initialized.")

    print("\nLoading workbook...")
    wb = load_workbook(excel_path, data_only=True)

    print("Migrating faction data...")
    for sheet_name in FACTION_SHEETS:
        migrate_faction(wb, sheet_name, app)

    wb.close()

    # Print summary
    with app.app_context():
        print("\n" + "-"*40)
        print("Migration Summary:")
        print(f"  Factions: {Faction.query.count()}")
        print(f"  Units: {Unit.query.count()}")
        print(f"  Ranged Weapons: {RangedWeapon.query.count()}")
        print(f"  Melee Weapons: {MeleeWeapon.query.count()}")
        print(f"  Faction Rules: {FactionRule.query.count()}")
        print("-"*40)

    print("\nMigration complete!")
    print(f"Database: {Path(__file__).parent.parent / 'data' / 'combat_sim.db'}")
    print("\nRun 'python run.py' to start the application.\n")


if __name__ == '__main__':
    main()
