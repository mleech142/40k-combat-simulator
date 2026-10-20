"""
Importer for the Wahapedia data export (https://wahapedia.ru/wh40k11ed/the-rules/data-export).

Wahapedia publishes the game's rules as CSV files linked by ID. This reads the ones listed
in FILES from a folder (downloaded by wahapedia_download.py) and returns stratagems and
rules (detachment rules and army rules) with their text, plus how the maths reads them.
Nothing here is shipped: each user downloads their own copy.

- **The files** are '|'-separated, one header row, every row ending in '|', and text fields
  can hold line breaks without quoting, so read_table() splits on '|' and counts fields
  rather than using the csv module.
- **Text** is the website's HTML. It is converted to the markup BSData text uses (`**bold**`,
  `^^keyword^^`, bullets; see Core.formatRuleText), so it is shown the same way.
- **Effects** come from rules_text.parse_effects: a stratagem's EFFECT, a rule's whole text.
  A record gets one side (Attacker or Target, the one most of its effects are for) and one
  phase (the stratagem's own when it is the Shooting or Fight phase, else the effects'),
  and its modifiers comma-separated, as a faction rule written in the app. Stratagems used
  outside the Shooting and Fight phases with no effects get no phase: they are listed on
  the Data tab but not offered on the pages.
- **Stratagems** leave out the core abilities the export lists with them (types like
  "Movement Ability": Normal Move, Advance Move...). Core stratagems are those of type "Core
  Stratagem" (the export also has an older "Core – ..." set and mission packs' cards, such as
  Boarding Actions and Challenger), the newest of each name.
- **Army rules** are the abilities a datasheet lists as its faction's ("Faction" in
  Datasheets_abilities.csv), leaving out any also listed as core abilities.
- **Factions and detachments** are Wahapedia's own (Space Marine chapters are all in "Space
  Marines"); link() matches them to the BSData ones by name.
"""

import html
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .rules_text import parse_effects, tidy_text

FILES = ['Last_update.csv', 'Factions.csv', 'Detachments.csv', 'Stratagems.csv', 'Detachment_abilities.csv',
         'Abilities.csv', 'Datasheets_abilities.csv']

# A stratagem's phase as the pages use it (others are outside the Shooting and Fight phases)
STRATAGEM_PHASES = {'shooting phase': 'Shooting', 'fight phase': 'Fighting', 'shooting or fight phase': 'Both',
                    'any phase': 'Both'}
SIDES = ('Attacker', 'Target')


class WahapediaError(Exception):
    """The folder is missing a file or holds one this importer doesn't understand."""


@dataclass
class WahapediaResult:
    data: dict  # factions, detachments, stratagems, rules (Wahapedia's IDs; see import_folder)
    report: list = field(default_factory=list)  # [{'kind', 'faction', 'unit', 'detail'}] like BSData's
    source: dict = field(default_factory=dict)  # {'last_update'}


# ---------------------------------------------------------------------------
# Reading the files
# ---------------------------------------------------------------------------

def read_table(path):
    """Rows of one export file as dicts (see the module docstring for the format)."""
    try:
        text = Path(path).read_text(encoding='utf-8-sig')
    except (OSError, UnicodeDecodeError) as err:
        raise WahapediaError(f'Could not read {Path(path).name}: {err}') from err
    head, _, body = text.partition('\n')
    names = head.rstrip('\r').split('|')
    if names[-1] == '':
        names = names[:-1]
    if not names or not names[0]:
        raise WahapediaError(f'{Path(path).name} has no header row')
    if body.strip() and not body.rstrip().endswith('|'):  # Every row ends with '|'
        raise WahapediaError(f'{Path(path).name} ends part-way through a row')
    rows, cells = [], []
    for cell in body.split('|'):
        if len(cells) == len(names):  # This cell starts with the line break that ended the row
            rows.append(dict(zip(names, cells)))
            cells = []
            cell = cell.lstrip('\r\n')
        cells.append(cell)
    if len(cells) == len(names):
        rows.append(dict(zip(names, cells)))
    elif cells and any(c.strip() for c in cells):
        raise WahapediaError(f'{Path(path).name} ends part-way through a row')
    return rows


def markup(text):
    """Wahapedia's HTML as BSData-style markup: **bold**, ^^keyword^^, bullets, line breaks."""
    text = re.sub(r'\s*\n\s*', ' ', text or '')  # Line breaks in the HTML source aren't line breaks on the page
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.I)
    text = re.sub(r'<li[^>]*>', '\n■ ', text, flags=re.I)
    text = re.sub(r'</?(?:ul|ol|div|p)[^>]*>', '\n', text, flags=re.I)
    text = re.sub(r'<span class="kwb[^"]*">(.*?)</span>', r'^^\1^^', text, flags=re.I | re.S)
    text = re.sub(r'</?b>', '**', text, flags=re.I)
    text = re.sub(r'<[^>]+>', '', text)
    text = tidy_text(html.unescape(text))
    text = re.sub(r'\*\*(\s*)\*\*', r'\1', text)  # Bold closed and reopened
    lines = [line.strip() for line in text.split('\n')]
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


def title_case(name):
    """'THREAT-COGITATION TARGETERS' -> 'Threat-Cogitation Targeters' (names already in mixed case are kept)."""
    if name != name.upper():
        return name
    small = {'of', 'the', 'and', 'in', 'to', 'a', 'an', 'for', 'on', 'by', 'or', 'from', 'with', 'at'}
    words = re.sub(r"[A-Za-z]+(?:'[A-Za-z]+)?", lambda m: m.group(0).capitalize(), name.lower()).split(' ')
    return ' '.join(w.lower() if i and w.lower() in small else w[:1].upper() + w[1:] for i, w in enumerate(words))


def effect_part(text):
    """The EFFECT of a stratagem's text (the whole text if it isn't laid out as WHEN/TARGET/EFFECT)."""
    m = re.search(r'\*\*EFFECT:\*\*\s*(.*?)(?=\n+\*\*(?:RESTRICTIONS?|NOTE|COST):\*\*|\Z)', text, re.S | re.I)
    return m.group(1).strip() if m else text


# ---------------------------------------------------------------------------
# What the maths reads
# ---------------------------------------------------------------------------

def interpret(text, phase=None, default_side='Attacker'):
    """
    {'applies_to', 'phase', 'modifier', 'parse'} for a rule's text. `phase` is the stratagem's
    own ('Shooting', 'Fighting', 'Both' or None outside them).
    """
    found = parse_effects(text)
    buffs, skipped = found['buffs'], list(found['skipped'])
    side = default_side
    if buffs:
        counts = Counter(b['applies_to'] for b in buffs)
        side = max(SIDES, key=lambda s: (counts[s], s == default_side))
        for b in buffs:
            if b['applies_to'] != side:
                skipped.append(f"Applies to the {'attacker' if b['applies_to'] == 'Attacker' else 'target'}: {b['modifier']}")
        buffs = [b for b in buffs if b['applies_to'] == side]
        if phase in ('Shooting', 'Fighting'):
            wrong = [b for b in buffs if b['phase'] not in (phase, 'Both')]
            skipped += [f"Not in the {phase} phase: {b['modifier']}" for b in wrong]
            buffs = [b for b in buffs if b not in wrong]
        elif buffs:
            phases = {b['phase'] for b in buffs}
            phase = phases.pop() if len(phases) == 1 else 'Both'
    modifier = ', '.join(b['modifier'] for b in buffs) or None
    status = 'not_modelled' if not buffs else ('partial' if skipped else 'modelled')
    parsed = {'applies_to': side, 'phase': phase, 'modifier': modifier}
    return dict(parsed, parse={'status': status, 'skipped': skipped, 'conditions': found['conditions'], 'parsed': parsed})


# ---------------------------------------------------------------------------
# The folder
# ---------------------------------------------------------------------------

def import_folder(folder):
    """
    WahapediaResult for a folder of export files. Its data:

    factions: [{'id', 'name'}]; detachments: [{'id', 'faction_id', 'name'}];
    stratagems: [{'wahapedia_id', 'faction_id' (None = core), 'detachment_id', 'detachment', 'name', 'cp',
                  'text', 'applies_to', 'phase', 'modifier', 'parse'}];
    rules: [{'wahapedia_id', 'faction_id', 'detachment_id' (None = army rule), 'detachment', 'name', 'text', ...}].
    """
    folder = Path(folder)
    missing = [name for name in FILES if not (folder / name).is_file()]
    if missing:
        raise WahapediaError(f'The folder is missing {", ".join(missing)}')
    tables = {name[:-4]: read_table(folder / name) for name in FILES}
    for name, columns in [('Factions', ['id', 'name']), ('Detachments', ['id', 'faction_id', 'name']),
                          ('Stratagems', ['id', 'faction_id', 'name', 'cp_cost', 'turn', 'phase', 'detachment',
                                          'detachment_id', 'description']),
                          ('Detachment_abilities', ['id', 'faction_id', 'name', 'description', 'detachment', 'detachment_id']),
                          ('Abilities', ['id', 'name', 'faction_id', 'description']),
                          ('Datasheets_abilities', ['ability_id', 'type'])]:
        rows = tables[name]
        if rows and not all(c in rows[0] for c in columns):
            raise WahapediaError(f'{name}.csv is missing columns: {", ".join(c for c in columns if c not in rows[0])}')
    if not tables['Stratagems']:
        raise WahapediaError('Stratagems.csv holds no stratagems')

    factions = [{'id': r['id'], 'name': tidy_text(r['name'])} for r in tables['Factions']]
    detachments = [{'id': r['id'], 'faction_id': r['faction_id'], 'name': tidy_text(r['name'])}
                   for r in tables['Detachments']]

    stratagems, core = [], {}
    for r in tables['Stratagems']:
        kind = (r.get('type') or '').strip()
        if 'ability' in kind.lower():  # Core abilities listed with the stratagems ("Normal Move")
            continue
        if not r['faction_id']:
            if not kind.startswith('Core Stratagem'):  # The older core set, mission packs' cards
                continue
            newest = core.get(name_key(r['name']))
            if newest and newest['id'] > r['id']:
                continue
            core[name_key(r['name'])] = r
        text = markup(r['description'])
        phase = STRATAGEM_PHASES.get((r['phase'] or '').strip().lower())
        side = 'Target' if (r['turn'] or '').lower().startswith('opponent') else 'Attacker'
        stratagems.append(dict(
            wahapedia_id=r['id'], faction_id=r['faction_id'] or None, detachment_id=r['detachment_id'] or None,
            detachment=tidy_text(r['detachment']) or None, name=title_case(tidy_text(r['name'])),
            cp=(r['cp_cost'] or '').strip() or None, text=text, **interpret(effect_part(text), phase, side)))
    kept = {r['id'] for r in core.values()}
    stratagems = [s for s in stratagems if s['faction_id'] or s['wahapedia_id'] in kept]  # An older core of the same name

    rules = []
    for r in tables['Detachment_abilities']:
        text = markup(r['description'])
        rules.append(dict(wahapedia_id=r['id'], faction_id=r['faction_id'] or None, detachment_id=r['detachment_id'] or None,
                          detachment=tidy_text(r['detachment']) or None, name=tidy_text(r['name']), text=text,
                          **interpret(text)))
    types = {}
    for r in tables['Datasheets_abilities']:
        if r['ability_id']:
            types.setdefault(r['ability_id'], set()).add(r['type'])
    core_names = {a['name'] for a in tables['Abilities'] if 'Core' in types.get(a['id'], ())}
    for r in tables['Abilities']:
        if r['faction_id'] and 'Faction' in types.get(r['id'], ()) and r['name'] not in core_names:
            text = markup(r['description'])
            rules.append(dict(wahapedia_id=r['id'], faction_id=r['faction_id'], detachment_id=None, detachment=None,
                              name=tidy_text(r['name']), text=text, **interpret(text)))

    last_update = (tables['Last_update'][0].get('last_update') or '').strip() if tables['Last_update'] else None
    return WahapediaResult(data={'factions': factions, 'detachments': detachments, 'stratagems': stratagems,
                                 'rules': rules}, source={'last_update': last_update or None})


def load_folder(folder):
    """(WahapediaResult or None, status) like data_store.load_bsdata. No files yet isn't an error."""
    folder = Path(folder) if folder else None
    status = {'folder': str(folder) if folder else None, 'loaded': False, 'error': None, 'source': None,
              'report': [], 'counts': {}}
    if not folder or not (folder / 'Stratagems.csv').is_file():
        return None, status
    try:
        result = import_folder(folder)
    except WahapediaError as err:
        status['error'] = str(err)
        return None, status
    status.update(loaded=True, source=result.source)
    return result, status


# ---------------------------------------------------------------------------
# Matching to BSData
# ---------------------------------------------------------------------------

def name_key(name):
    """Names compared loosely: case, apostrophes, hyphens and spaces don't count."""
    return re.sub(r'[^a-z0-9]', '', tidy_text(name).lower())


def link(data, bsdata_factions, bsdata_detachments):
    """
    Wahapedia's stratagems and rules as rows for the reference tables, matched to BSData.

    bsdata_factions: [{'id', 'name', 'bsdata_id'}]; bsdata_detachments: [{'id', 'faction_id',
    'bsdata_id', 'name'}] (one per faction that has it). A Wahapedia detachment is the BSData
    detachments of the same name; a Wahapedia faction is the BSData faction of the same name
    plus those (not named by another Wahapedia faction) that have its detachments, so the
    Space Marine chapters get Space Marines' rules. A record is copied for each BSData faction
    it belongs to; core stratagems have none.

    Returns (rows, report): rows are ('stratagems' | 'faction_rules', stable key, record).
    """
    by_name = {name_key(f['name']): f for f in bsdata_factions}
    detachments_named = {}
    for d in bsdata_detachments:
        detachments_named.setdefault(name_key(d['name']), []).append(d)
    names = {f['id']: f['name'] for f in data['factions']}
    claimed = {by_name[name_key(f['name'])]['id'] for f in data['factions'] if name_key(f['name']) in by_name}

    members = {}  # Wahapedia faction ID -> {BSData faction ID}
    for f in data['factions']:
        own = by_name.get(name_key(f['name']))
        reached = {d['faction_id'] for w in data['detachments'] if w['faction_id'] == f['id']
                   for d in detachments_named.get(name_key(w['name']), [])}
        members[f['id']] = ({own['id']} if own else set()) | (reached - claimed)
    key_of = {f['id']: f['bsdata_id'] for f in bsdata_factions}

    rows, report, unmatched, seen = [], [], set(), set()

    def note(kind, faction, detail):
        report.append({'kind': kind, 'faction': names.get(faction, faction or ''), 'unit': '', 'detail': detail})

    def place(record):
        """[(BSData faction ID, BSData detachment or None)] a record belongs to."""
        faction = record['faction_id']
        if not record['detachment_id']:
            return [(f, None) for f in sorted(members.get(faction, ()))]
        matches = detachments_named.get(name_key(record['detachment'] or ''), [])
        own = [d for d in matches if d['faction_id'] in members.get(faction, ())]
        return [(d['faction_id'], d) for d in (own or matches)]

    for section, kind in (('stratagems', 'stratagem'), ('rules', 'rule')):
        table = 'stratagems' if kind == 'stratagem' else 'faction_rules'
        for record in data[section]:
            columns = {k: record[k] for k in ('name', 'text', 'applies_to', 'phase', 'modifier', 'parse')}
            if kind == 'stratagem':
                columns['cp'] = record['cp']
            base = dict(columns, source='wahapedia', wahapedia_id=record['wahapedia_id'])
            if kind == 'stratagem' and not record['faction_id']:
                rows.append((table, f'wahapedia:{kind}:{record["wahapedia_id"]}', dict(base, faction_id=None)))
                continue
            places = place(record)
            if not places:
                if record['detachment_id'] and record['detachment_id'] not in unmatched:
                    unmatched.add(record['detachment_id'])
                    note('wahapedia_detachment', record['faction_id'], f'Detachment "{record["detachment"]}" is not in BSData')
                elif not record['detachment_id'] and not members.get(record['faction_id']) \
                        and record['faction_id'] not in unmatched:
                    unmatched.add(record['faction_id'])
                    note('wahapedia_faction', record['faction_id'], 'No BSData faction of this name or with its detachments')
                continue
            for faction_id, detachment in places:
                key = f'wahapedia:{kind}:{record["wahapedia_id"]}/{key_of[faction_id]}'
                if key in seen:  # The faction has two detachments of that name
                    continue
                seen.add(key)
                rows.append((table, key, dict(
                    base, faction_id=faction_id, detachment_key=detachment['bsdata_id'] if detachment else None,
                    detachment_name=detachment['name'] if detachment else None)))
    return rows, report
