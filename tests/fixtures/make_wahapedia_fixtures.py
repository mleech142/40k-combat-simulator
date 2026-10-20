"""
Writes the made-up Wahapedia export used by tests/test_wahapedia.py to tests/fixtures/wahapedia:
invented rules in the format of the real files, for the factions and detachments of the BSData
fixtures (tests/fixtures/bsdata). Run it after changing a fixture:

    python tests/fixtures/make_wahapedia_fixtures.py

The real format: '|'-separated, a header row, every row ending in '|', UTF-8 with a byte order
mark, HTML in text fields, and line breaks inside fields without quoting (the Bastion's legend).
"""
import sys
from pathlib import Path

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / 'wahapedia'


def strat(when, target, effect):
    return f'<b>WHEN:</b> {when}<br><br><b>TARGET:</b> {target}<br><br><b>EFFECT:</b> {effect}'


ONE_UNIT = 'One <span class="kwb">STARFARERS</span> unit from your army.'

TABLES = {
    'Last_update': (['last_update'], [['2026-10-01 12:00:00']]),
    'Factions': (['id', 'name', 'link'], [
        ['SF', 'Starfarers', 'https://example.invalid/starfarers'],
        ['AL', 'Allies', 'https://example.invalid/allies'],
        ['GL', 'Ghost Legion', 'https://example.invalid/ghost-legion'],  # Not in the BSData fixtures
    ]),
    'Detachments': (['id', 'faction_id', 'name', 'legend', 'type', 'dp', 'force_disposition'], [
        ['001', 'SF', 'Void Vanguard', '', '', '1', ''],
        ['002', 'SF', 'Star Bastion', '', '', '1', ''],
        ['003', 'SF', 'Lost Fleet', '', '', '1', ''],  # Not in the BSData fixtures
        ['004', 'AL', 'Allied Task Force', '', '', '1', ''],
    ]),
    'Stratagems': (['faction_id', 'name', 'id', 'type', 'cp_cost', 'legend', 'turn', 'phase', 'detachment',
                    'detachment_id', 'description'], [
        ['SF', 'VOID STRIKE', '101', 'Void Vanguard – Battle Tactic Stratagem', '1', '', 'Your turn', 'Shooting phase',
         'Void Vanguard', '001', strat('Your Shooting phase.', ONE_UNIT,
                                       'Until the end of the phase, ranged weapons equipped by models in your unit have the '
                                       '[LETHAL HITS] ability.')],
        ['SF', 'BASTION OF STARS', '102', 'Star Bastion – Battle Tactic Stratagem', '1', 'The walls hold.\nThey always hold.',
         'Opponent’s turn', 'Shooting or Fight phase', 'Star Bastion', '002',
         strat('Your opponent’s Shooting phase or the Fight phase, just after an enemy unit has selected its targets.',
               ONE_UNIT, 'Until the end of the phase, each time an attack targets your unit, subtract 1 from the Wound roll.')],
        ['SF', 'REDEPLOY', '103', 'Void Vanguard – Strategic Ploy Stratagem', '2', '', 'Your turn', 'Movement phase',
         'Void Vanguard', '001', strat('Your Movement phase.', ONE_UNIT,
                                       'Remove your unit from the battlefield and place it into Strategic Reserves.')],
        ['SF', 'LOST SIGNAL', '104', 'Lost Fleet – Battle Tactic Stratagem', '1', '', 'Your turn', 'Shooting phase',
         'Lost Fleet', '003', strat('Your Shooting phase.', ONE_UNIT, "Your unit's ranged attacks have [PRECISION].")],
        ['AL', 'COMBINED ARMS', '105', 'Allied Task Force – Battle Tactic Stratagem', '1', '', 'Your turn', 'Fight phase',
         'Allied Task Force', '004', strat('Your Fight phase.', 'One unit from your army.',
                                           'If your unit is within range of an objective marker, your unit’s melee attacks '
                                           'have [LANCE] and +1 AP.')],
        ['', 'COMMAND RE-ROLL', '201', 'Core Stratagem', '1', '', 'Either player’s turn', 'Any phase', '', '',
         strat('Any phase, just after you have made a Hit roll.', 'That unit or model from your army.',
               'You re-roll that roll, test or saving throw.')],
        ['', 'SMOKESCREEN', '202', 'Core Stratagem', '1', '', 'Opponent’s turn', 'Shooting phase', '', '',
         strat('Your opponent’s Shooting phase, just after an enemy unit has selected its targets.',
               'One <span class="kwb">SMOKE</span> unit from your army.',
               'Until the end of the phase, models in your unit have the Stealth ability.<ul><li>Smoke rises.</li></ul>')],
        ['', 'NORMAL MOVE', '203', 'Movement Ability', '', '', 'Your turn', 'Movement phase', '', '',
         'Move each model up to its Move characteristic.'],
        # Left out: an older core set, a mission pack's card, and an older stratagem of the same name
        ['', 'GO TO GROUND', '204', 'Core – Wargear Stratagem', '1', '', 'Opponent’s turn', 'Shooting phase', '', '',
         strat('Your opponent’s Shooting phase.', 'One unit from your army.', 'Your unit has 6+ InSv.')],
        ['', 'ALL IN', '205', 'Challenger – Strategic Ploy Stratagem', '1', '', 'Your turn', 'Command phase', '', '',
         strat('Your Command phase.', 'Your army.', 'Gain 1CP.')],
        ['', 'SMOKESCREEN', '199', 'Core Stratagem', '2', '', 'Opponent’s turn', 'Shooting phase', '', '',
         strat('Your opponent’s Shooting phase.', 'One unit from your army.', 'Your unit has 6+ InSv.')],
    ]),
    'Detachment_abilities': (['id', 'faction_id', 'name', 'legend', 'description', 'detachment', 'detachment_id'], [
        ['301', 'SF', 'Vanguard Doctrine', '', '<b>Doctrine:</b> Each time a model in your unit makes a melee attack, '
         'add 1 to the Hit roll.', 'Void Vanguard', '001'],
        ['302', 'SF', 'Lost Doctrine', '', 'Models in your unit have the Stealth ability.', 'Lost Fleet', '003'],
    ]),
    'Abilities': (['id', 'name', 'legend', 'faction_id', 'description'], [
        ['401', 'Star Oath', '', 'SF', 'Each time a model in your unit makes an attack, re-roll a Hit roll of 1.'],
        ['402', 'Super-heavy Walker', '', 'SF', 'This model can move over other models.'],  # Also a core ability
        ['403', 'Deep Strike', '', '', 'This unit can be set up in Reserves.'],
        ['404', 'Ghostly', '', 'GL', 'Models in your unit have a 4+ invulnerable save.'],
        ['405', 'Star Lore', '', 'SF', 'Not an army rule: only on a datasheet.'],
    ]),
    'Datasheets_abilities': (['datasheet_id', 'line', 'ability_id', 'model', 'name', 'description', 'type', 'parameter'], [
        ['9001', '1', '401', '', '', '', 'Faction', ''],
        ['9001', '2', '402', '', '', '', 'Faction', ''],
        ['9002', '1', '402', '', '', '', 'Core', ''],
        ['9002', '2', '403', '', '', '', 'Core', ''],
        ['9003', '1', '404', '', '', '', 'Faction', ''],
        ['9003', '2', '405', '', '', '', 'Datasheet', ''],
    ]),
}


def write(name, columns, rows):
    lines = ['|'.join(columns) + '|'] + ['|'.join(row) + '|' for row in rows]
    (OUT / f'{name}.csv').write_text('﻿' + '\n'.join(lines) + '\n', encoding='utf-8', newline='\n')


if __name__ == '__main__':
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (columns, rows) in TABLES.items():
        assert all(len(row) == len(columns) for row in rows), name
        write(name, columns, rows)
    print(f'Wrote {len(TABLES)} files to {OUT}')
