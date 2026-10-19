"""
Importer for BSData catalogues (https://github.com/BSData/wh40k-11e).

BSData publishes BattleScribe data as JSON: one game system file (`Warhammer 40,000.json`)
and one catalogue per faction or library, linked to each other by ID. `import_folder()`
reads a folder of those files and returns the reference sections of the data JSON
(factions, units, ranged and melee weapons, in the format described in data_store.py)
plus an import report. Every record also carries its BSData `bsdata_id`.

How BattleScribe data maps onto the app:

- **Factions** are the non-library catalogues. A faction's units are those it defines, plus
  root entries it links or imports (`importRootEntries`) that carry one of its own
  "Faction: X" categories (see home_categories), so Blood Angels get the Space Marines
  units but not allied Agents of the Imperium or Imperial Knights. Library root entries no
  faction takes join the faction that took most of that library, or else form a faction
  named after it (Unaligned Forces). Catalogues left with no units are skipped.
- **Points** are those of a lone unit: the surcharge for repeating a datasheet in an army
  never applies.
- **A unit** is a root entry of type unit or model. The importer auto-selects its minimum
  configuration (every entry's minimum, group defaults filling group minimums), which gives
  the default model count and loadout, then adds models one at a time up to the maximum
  to price every legal unit size.
- **Stats** come from the `Unit` profile of the most numerous model; units whose models
  have different stat lines are reported.
- **Points** are the sum of `pts` costs over the selection, after the cost modifiers whose
  conditions can be evaluated for a lone unit. Modifiers that can't be are skipped and
  reported.
- **Weapons** are the `Ranged Weapons` / `Melee Weapons` profiles, one record per faction
  and name/stat line. "➤ Plasma gun - standard" becomes "Plasma gun - standard", the app's
  "Weapon - Profile" variant naming.
- **Defensive abilities** used by the maths: InSv from the profile, Feel No Pain and
  Stealth from core rule links, and a few unconditional ability texts (see DEFENSIVE_TEXT).
- **Rule text** keeps BSData's markup for display (`**bold**`, `*italic*`, `^^keyword^^`, bullets;
  see Core.formatRuleText) but with its whitespace tidied (tidy_text). Everything parsed from
  it (Feel No Pain, Leader lists) reads the plain words (plain_text).
- **Detachments** are the options of a faction's root "Detachment" entry, each with its rule
  text, taken from the faction's own catalogue and those it takes units from (so not the
  Agents of the Imperium's detachments in every Imperium army). **Army rules** are the rules
  defined in the faction's catalogue. Both are text only: the maths uses the rules and
  stratagems written in the app data.
"""

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .simulation import check_keywords

IMPORTER_VERSION = 3  # Bump when the output changes, so cached imports are redone

PTS = 'pts'
SUPPORTED_BATTLESCRIBE_VERSIONS = ('2.',)  # Major version 2 (files are currently 2.03)
WEAPON_TYPES = {'Ranged Weapons': 'ranged', 'Melee Weapons': 'melee'}
CHILD_KEYS = ('selectionEntries', 'selectionEntryGroups', 'entryLinks')

# Unconditional ability texts the maths can use, as (pattern, unit modifier or FNP)
DEFENSIVE_TEXT = [
    (re.compile(r'^(?:this model|models in this unit|this unit) ha(?:s|ve) the feel no pain (\d)\+ ability\.?$', re.I),
     'fnp'),
    (re.compile(r'^each time an attack is allocated to (?:this model|a model in this unit), subtract 1 from the '
                r'damage characteristic of that attack\.?$', re.I),
     'Damage Modifier -1'),
]


class BSDataError(Exception):
    """The folder is missing the game system or holds a file this importer doesn't understand."""


@dataclass
class ImportResult:
    data: dict  # factions, units, ranged_weapons, melee_weapons (data JSON format)
    report: list = field(default_factory=list)  # [{'kind', 'faction', 'unit', 'detail'}]
    source: dict = field(default_factory=dict)  # Game system and catalogue revisions


# ---------------------------------------------------------------------------
# Reading the folder
# ---------------------------------------------------------------------------

class Catalogue:
    """One BSData file, with an index of every element in it that has an ID."""

    def __init__(self, path, root):
        self.path = Path(path)
        self.root = root
        self.id = root['id']
        self.name = root['name']
        self.is_game_system = root.get('type') == 'gameSystem'
        self.is_library = bool(root.get('library'))
        self.index = {}
        self._index(root)
        self.scope = []  # Catalogues searched to resolve links: self, linked catalogues, game system

    def _index(self, node):
        if isinstance(node, dict):
            if 'id' in node and node is not self.root:
                self.index.setdefault(node['id'], node)
            for value in node.values():
                self._index(value)
        elif isinstance(node, list):
            for value in node:
                self._index(value)

    def links(self):
        return self.root.get('catalogueLinks', [])

    def find(self, target_id):
        """(element, catalogue it is defined in) for an ID visible from this catalogue."""
        for cat in self.scope:
            if target_id in cat.index:
                return cat.index[target_id], cat
        return None, None


def read_folder(folder):
    """Read every BSData .json file in `folder`: (game system, {id: catalogue})."""
    folder = Path(folder)
    if not folder.is_dir():
        raise BSDataError(f'{folder} is not a folder')

    game_system, catalogues = None, {}
    for path in sorted(folder.glob('*.json')):
        try:
            with open(path, encoding='utf-8') as f:
                content = json.load(f)
        except (OSError, ValueError) as e:
            raise BSDataError(f'{path.name}: not readable JSON ({e})') from e
        root = (content.get('catalogue') or content.get('gameSystem')) if isinstance(content, dict) else None
        if not isinstance(root, dict) or 'id' not in root or 'name' not in root:
            raise BSDataError(f'{path.name}: not a BattleScribe catalogue or game system')
        version = str(root.get('battleScribeVersion', ''))
        if not version.startswith(SUPPORTED_BATTLESCRIBE_VERSIONS):
            raise BSDataError(f'{path.name}: BattleScribe version {version or "?"} is not supported')
        cat = Catalogue(path, root)
        if cat.is_game_system:
            if game_system is not None:
                raise BSDataError(f'{path.name}: a second game system file')
            game_system = cat
        else:
            catalogues[cat.id] = cat

    if game_system is None:
        raise BSDataError(f'No game system file (e.g. "Warhammer 40,000.json") in {folder}')
    for cat in catalogues.values():
        if cat.root.get('gameSystemId') != game_system.id:
            raise BSDataError(f'{cat.path.name}: belongs to a different game system')
        cat.scope = _scope(cat, catalogues) + [game_system]
    game_system.scope = [game_system]
    return game_system, catalogues


def _scope(cat, catalogues):
    """The catalogue and everything it links to, transitively, in link order."""
    order, stack = [], [cat]
    while stack:
        current = stack.pop(0)
        if current in order:
            continue
        order.append(current)
        stack.extend(catalogues[link['targetId']] for link in current.links() if link['targetId'] in catalogues)
    return order


# ---------------------------------------------------------------------------
# Entries, links and constraints
# ---------------------------------------------------------------------------

@dataclass
class Entry:
    """A selection entry or group as seen from where it is used (an entry link merged with its target)."""
    node: dict  # The target element
    cat: Catalogue  # Catalogue the target is defined in (resolves its own links)
    link: dict = None  # The entry link, if reached through one

    @property
    def id(self):
        return self.node['id']

    @property
    def ids(self):
        return {self.node['id']} | ({self.link['id']} if self.link and 'id' in self.link else set())

    @property
    def name(self):
        return (self.link or {}).get('name') or self.node.get('name', '')

    @property
    def kind(self):
        """'unit', 'model', 'upgrade' or 'group'."""
        return self.node.get('type', 'group')

    @property
    def hidden(self):
        return bool(self.node.get('hidden') or (self.link or {}).get('hidden'))

    def merged(self, key):
        return list((self.link or {}).get(key, [])) + list(self.node.get(key, []))

    def bound(self, kind):
        """Effective min/max selections under the parent (largest min, smallest max), or None."""
        values = [c['value'] for c in self.merged('constraints')
                  if c.get('type') == kind and c.get('field') == 'selections' and c.get('scope') == 'parent']
        if not values:
            return None
        return max(values) if kind == 'min' else min(values)

    def categories(self):
        return {c.get('targetId') for c in self.merged('categoryLinks')}

    def category_names(self):
        seen = []
        for c in self.merged('categoryLinks'):
            if c.get('name') and c['name'] not in seen:
                seen.append(c['name'])
        return seen

    def pts(self):
        return sum(c.get('value') or 0 for c in self.node.get('costs', []) if c.get('name') == PTS)

    def children(self, report):
        """Child entries and groups, links resolved; hidden ones left out."""
        out = []
        for key in CHILD_KEYS:
            for child in self.node.get(key, []):
                entry = resolve_link(child, self.cat, report) if key == 'entryLinks' else Entry(child, self.cat)
                if entry is not None and not entry.hidden:
                    out.append(entry)
        return out


def resolve_link(link, cat, report):
    target, target_cat = cat.find(link.get('targetId'))
    if target is None:
        report.add('missing_link', None, None, f'{cat.name}: link "{link.get("name")}" points at nothing')
        return None
    return Entry(target, target_cat, link)


# ---------------------------------------------------------------------------
# The selection: a unit's chosen entries, used for model counts, loadouts and points
# ---------------------------------------------------------------------------

@dataclass
class Selection:
    entry: Entry
    count: int = 1
    parent: 'Selection' = None
    children: list = field(default_factory=list)

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def total(self, predicate, direct_only=False):
        """
        Selections matching `predicate` below this one, multiplied by the counts above them.
        Groups aren't selections: what is inside one counts as a direct child.
        """
        total = 0
        for child in self.children:
            if predicate(child):
                total += child.count
            if child.children and (not direct_only or child.entry.kind == 'group'):
                total += child.count * child.total(predicate, direct_only)
        return total

    def models(self):
        """Models in the unit (the unit itself when it is a single model)."""
        count = self.total(lambda s: s.entry.kind == 'model')
        return count or (1 if self.entry.kind in ('model', 'unit') and self.parent is None else 0)

    def multiplier(self):
        """Copies of this selection's parent in the whole unit (its own count not included)."""
        m, node = 1, self.parent
        while node is not None:
            m *= node.count
            node = node.parent
        return m


def auto_select(entry, report, parent=None, count=1):
    """The minimum configuration below `entry`: entry minimums, then group defaults up to group minimums."""
    sel = Selection(entry, count, parent)
    for child in entry.children(report):
        if child.kind == 'group':
            _select_group(sel, child, report)
        else:
            low = child.bound('min') or 0
            if low > 0:
                sel.children.append(auto_select(child, report, sel, low))
    return sel


def _select_group(sel, group, report):
    """Select a group's members with minimums, then fill the group minimum with its default."""
    group_sel = Selection(group, 1, sel)
    members = group.children(report)
    room = group.bound('max')
    for member in _default_first(group, members):
        if member.kind == 'group':
            _select_group(group_sel, member, report)
            continue
        low = member.bound('min') or 0
        if room is not None:  # Required members never overfill the group (the default goes first)
            low = min(low, room)
            room -= low
        if low > 0:
            group_sel.children.append(auto_select(member, report, group_sel, low))
    need = (group.bound('min') or 0) - sum(s.count for s in group_sel.children if s.entry.kind != 'group')
    for member in _default_first(group, members):
        if need <= 0:
            break
        if member.kind == 'group':
            continue
        existing = next((s for s in group_sel.children if s.entry is member), None)
        have = existing.count if existing else 0
        room = need if member.bound('max') is None else max(0, min(need, member.bound('max') - have))
        if room:
            if existing:
                existing.count += room
            else:
                group_sel.children.append(auto_select(member, report, group_sel, room))
            need -= room
    if group_sel.children:
        sel.children.append(group_sel)


def _default_first(group, members):
    default = group.node.get('defaultSelectionEntryId')
    return sorted(members, key=lambda m: 0 if default and default in m.ids else 1)


def grow(sel, report):
    """Add one model to the unit, preferring group defaults. False when the unit is at its maximum."""
    for node in sel.walk():
        if node.entry.kind != 'group':
            continue
        group_max = node.entry.bound('max')
        in_group = sum(s.count for s in node.children if s.entry.kind == 'model')
        if group_max is not None and in_group >= group_max:
            continue
        members = [m for m in node.entry.children(report) if m.kind == 'model']
        for member in _default_first(node.entry, members):
            existing = next((s for s in node.children if s.entry.node is member.node), None)
            have = existing.count if existing else 0
            limit = member.bound('max')
            if limit is None and group_max is None:
                continue  # Unbounded: never grow
            if limit is not None and have >= limit:
                continue
            if existing:
                existing.count += 1
            else:
                node.children.append(auto_select(member, report, node, 1))
            return True
    for node in list(sel.walk()):
        if node.entry.kind == 'model' and node is not sel and node.parent.entry.kind != 'group':
            limit = node.entry.bound('max')
            if limit is not None and node.count < limit:
                node.count += 1
                return True
    return False


# ---------------------------------------------------------------------------
# Points: costs and the cost modifiers that can be evaluated for one unit on its own
# ---------------------------------------------------------------------------

class Unevaluable(Exception):
    pass


def points(sel, faction_cat):
    """Total points of the selection, and whether any cost modifier had to be skipped."""
    skipped = False
    total = 0
    for node in sel.walk():
        if node.entry.kind == 'group':
            continue
        cost = node.entry.pts()
        for modifier in node.entry.merged('modifiers') + _group_modifiers(node.entry):
            if not _is_pts_modifier(modifier, node.entry):
                continue
            try:
                if not _conditions_hold(modifier, node, sel, faction_cat):
                    continue
                times = _repeats(modifier, node, sel)
            except Unevaluable:
                skipped = True
                continue
            cost = _apply(cost, modifier, times)
        total += cost * node.count * node.multiplier()
    return total, skipped


def _group_modifiers(entry):
    """Modifiers inside modifierGroups (their own conditions are folded into each modifier)."""
    out = []
    for group in entry.merged('modifierGroups'):
        for modifier in group.get('modifiers', []):
            combined = dict(modifier)
            combined['conditions'] = group.get('conditions', []) + modifier.get('conditions', [])
            combined['conditionGroups'] = group.get('conditionGroups', []) + modifier.get('conditionGroups', [])
            combined['localConditionGroups'] = (group.get('localConditionGroups', [])
                                                + modifier.get('localConditionGroups', []))
            out.append(combined)
    return out


def _is_pts_modifier(modifier, entry):
    type_ids = {c.get('typeId') for c in entry.node.get('costs', []) if c.get('name') == PTS}
    return modifier.get('field') in type_ids or modifier.get('field') == PTS_TYPE_ID


PTS_TYPE_ID = '51b2-306e-1021-d207'  # The game system's "pts" cost type


def _apply(cost, modifier, times=1):
    value = modifier.get('value') or 0
    kind = modifier.get('type')
    if kind == 'set':
        return value
    if kind == 'increment':
        return cost + value * times
    if kind == 'decrement':
        return cost - value * times
    if kind == 'multiply':
        return cost * value
    raise Unevaluable(kind)


def _repeats(modifier, node, root):
    repeats = modifier.get('repeats') or []
    if not repeats:
        return 1
    times = 0
    for r in repeats:
        count = _count(r, node, root)
        per = r.get('value') or 1
        steps = math.ceil(count / per) if r.get('roundUp') else count // per
        times += steps * (r.get('repeats') or 1)
    return times


def _conditions_hold(modifier, node, root, faction_cat):
    checks = [_condition(c, node, root, faction_cat) for c in modifier.get('conditions', [])]
    checks += [_group_holds(g, node, root, faction_cat) for g in modifier.get('conditionGroups', [])]
    checks += [_local_group(g, node, root) for g in modifier.get('localConditionGroups', [])]
    return all(checks)


def _group_holds(group, node, root, faction_cat):
    checks = [_condition(c, node, root, faction_cat) for c in group.get('conditions', [])]
    checks += [_group_holds(g, node, root, faction_cat) for g in group.get('conditionGroups', [])]
    checks += [_local_group(g, node, root) for g in group.get('localConditionGroups', [])]
    if not checks:
        return True
    return any(checks) if group.get('type') == 'or' else all(checks)


def _local_group(group, node, root):
    """
    "At least N matching selections in scope", e.g. the surcharge for a second copy of a
    datasheet (scope: the unit's force). A lone unit has no other units, so outside the unit
    the count is 0; counts inside the unit aren't modelled.
    """
    outside = group.get('scope') in ('force', 'roster') or (group.get('scope') == 'parent' and node is root)
    if not outside or group.get('field') != 'selections' or group.get('type') not in COMPARE:
        raise Unevaluable('localConditionGroups')
    return COMPARE[group['type']](0, group.get('value') or 0)


COMPARE = {
    'atLeast': lambda a, b: a >= b,
    'greaterThan': lambda a, b: a > b,
    'atMost': lambda a, b: a <= b,
    'lessThan': lambda a, b: a < b,
    'equalTo': lambda a, b: a == b,
    'notEqualTo': lambda a, b: a != b,
}


def _condition(c, node, root, faction_cat):
    kind = c.get('type')
    if kind in ('instanceOf', 'notInstanceOf'):
        result = _instance_of(c, node, root, faction_cat)
        return result if kind == 'instanceOf' else not result
    if kind not in COMPARE:
        raise Unevaluable(kind)
    if c.get('percentValue'):
        raise Unevaluable('percentValue')
    return COMPARE[kind](_count(c, node, root), c.get('value') or 0)


def _instance_of(c, node, root, faction_cat):
    scope, child = c.get('scope'), c.get('childId')
    if scope in ('force', 'roster'):
        return False  # A lone unit has no force or roster entries (e.g. "Crusade Force")
    if scope == 'primary-catalogue':
        return child == faction_cat.id
    target = _scope_node(scope, node, root)
    return child in target.entry.ids or child in target.entry.categories()


def _count(c, node, root):
    """Value of a condition's field over its scope: selection counts, or a cost such as pts."""
    if c.get('field') != 'selections':
        raise Unevaluable(c.get('field'))
    scope = c.get('scope')
    if scope in ('force', 'roster', 'primary-catalogue', 'root-entry', 'unit'):
        target = root  # A roster holding just this unit
    else:
        target = _scope_node(scope, node, root)
    child = c.get('childId')
    groups = [n for n in target.walk() if n.entry.kind == 'group' and child in n.entry.ids]
    if groups:  # Selections in a group, e.g. the models in "Intercessors"
        return sum(g.total(lambda s: s.entry.kind != 'group', direct_only=True) * g.multiplier() for g in groups)
    return target.total(_matcher(child), direct_only=not c.get('includeChildSelections'))


def _scope_node(scope, node, root):
    if scope == 'self':
        return node
    if scope == 'parent':
        parent = node.parent or root
        while parent.entry.kind == 'group' and parent.parent is not None:
            parent = parent.parent
        return parent
    current = node
    while current is not None:
        if scope in current.entry.ids:
            return current
        current = current.parent
    if scope in ('unit', 'root-entry', 'model-or-unit'):
        return root
    raise Unevaluable(f'scope {scope}')


def _matcher(child):
    if child in (None, 'any'):
        return lambda s: s.entry.kind != 'group'
    if child in ('model', 'unit', 'upgrade'):
        return lambda s: s.entry.kind == child
    return lambda s: child in s.entry.ids or child in s.entry.categories()


# ---------------------------------------------------------------------------
# Profiles, rules and abilities
# ---------------------------------------------------------------------------

def profiles(entry, report):
    """Profiles on an entry: its own, linked ones and those in info groups."""
    out = list(entry.merged('profiles'))
    for group in entry.merged('infoGroups'):
        out.extend(group.get('profiles', []))
    for link in entry.merged('infoLinks'):
        if link.get('type') in ('profile', 'infoGroup'):
            target, _ = entry.cat.find(link.get('targetId'))
            if target is None:
                report.add('missing_link', None, None, f'{entry.cat.name}: profile link "{link.get("name")}" points at nothing')
            elif link['type'] == 'profile':
                out.append(target)
            else:
                out.extend(target.get('profiles', []))
    return [p for p in out if not p.get('hidden')]


def rule_links(entry):
    """Core rules linked to an entry, with unconditional name appends: ['Feel No Pain 5+', 'Deep Strike']."""
    names = []
    for link in entry.merged('infoLinks'):
        if link.get('type') != 'rule' or link.get('hidden'):
            continue
        name = link.get('name', '')
        for modifier in link.get('modifiers', []):
            if (modifier.get('field') == 'name' and modifier.get('type') == 'append'
                    and not modifier.get('conditions') and not modifier.get('conditionGroups')):
                name = f'{name} {modifier.get("value")}'.strip()
        names.append(name)
    for rule in entry.merged('rules'):
        if not rule.get('hidden'):
            names.append(rule.get('name', ''))
    return names


def characteristics(profile):
    return {c.get('name'): (c.get('$text') or '').strip() for c in profile.get('characteristics', [])}


def ability_text(profile):
    chars = characteristics(profile)
    return tidy_text(chars.get('Description') or next(iter(chars.values()), ''))


# Characters BSData's text uses for layout that read as ordinary ones
TIDY_CHARACTERS = str.maketrans({'\u00a0': ' ', '\u2011': '-', '\u2010': '-', '\u2013': '-', '\u2019': "'"})


def tidy_text(text):
    """BSData text with non-breaking spaces and hyphens made ordinary, repeated spaces collapsed
    and trailing spaces removed; its markup is kept."""
    lines = [re.sub(r' {2,}', ' ', line).rstrip() for line in (text or '').translate(TIDY_CHARACTERS).split('\n')]
    return '\n'.join(lines).strip()


# BSData's text markup: **bold**, *italic*, ^^keyword^^, <ins>changed</ins>, footnote daggers
MARKUP = re.compile(r'\*+|\^\^|</?ins>|[†‡]')


def plain_text(text):
    """The words of BSData text without its markup, for parsing (bullets and line breaks kept)."""
    return re.sub(r' {2,}', ' ', MARKUP.sub('', tidy_text(text)))


def _int(text):
    """'5"' -> 5, '3+' -> 3, '-2' -> -2; None when not a number ('N/A', '-', '*')."""
    m = re.match(r'^\s*(-?\d+)', text or '')
    return int(m.group(1)) if m else None


def _plus(text):
    """'4+' -> 4; '' or '-' -> None."""
    return _int(text) if text and '+' in text else None


def weapon_record(profile, kind):
    chars = characteristics(profile)
    name = re.sub(r'^[➤>\s]+', '', profile.get('name', '')).strip()
    keywords = chars.get('Keywords') or ''
    abilities = [k.strip() for k in keywords.split(',') if k.strip() and k.strip() not in ('-', '–')]
    skill = _plus(chars.get('BS') if kind == 'ranged' else chars.get('WS'))
    record = {
        'bsdata_id': profile['id'],
        'name': name,
        'attacks': chars.get('A') or '0',
        'strength': _int(chars.get('S')),
        'armor_penetration': abs(_int(chars.get('AP')) or 0),
        'damage': chars.get('D') or '0',
        'abilities': abilities,
    }
    if kind == 'ranged':
        record['ballistic_skill'] = skill
        record['range_inches'] = _int(chars.get('Range'))
    else:
        record['weapon_skill'] = skill
    return record


def _stat_key(record):
    return tuple((k, json.dumps(v)) for k, v in sorted(record.items()) if k != 'bsdata_id')


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------

class Report:
    def __init__(self):
        self.items = []
        self._seen = set()

    def add(self, kind, faction, unit, detail):
        key = (kind, faction, unit, detail)
        if key not in self._seen:
            self._seen.add(key)
            self.items.append({'kind': kind, 'faction': faction, 'unit': unit, 'detail': detail})


def build_unit(entry, faction_cat, report, known_keywords):
    """A unit record (without IDs) and its weapon profiles, or None if it has no usable stat line."""
    faction = faction_name(faction_cat)
    sel = auto_select(entry, report)
    default_models = sel.models()

    # Stat line: the Unit profile of the most numerous model
    stat_lines = Counter()
    unit_profile = next((p for p in profiles(entry, report) if p.get('typeName') == 'Unit'), None)
    for node in sel.walk():
        if node.entry.kind != 'model' and node is not sel:
            continue
        own = next((p for p in profiles(node.entry, report) if p.get('typeName') == 'Unit'), None)
        profile = own or unit_profile
        if profile is not None:
            weight = (node.count * node.multiplier()) if node is not sel else (1 if node.entry.kind == 'model' else 0)
            stat_lines[json.dumps(characteristics(profile), sort_keys=True)] += weight
    if not stat_lines and unit_profile is not None:
        stat_lines[json.dumps(characteristics(unit_profile), sort_keys=True)] += 1
    if not stat_lines:
        report.add('skipped_unit', faction, entry.name, 'no Unit profile')
        return None
    if len(stat_lines) > 1:
        report.add('mixed_profile', faction, entry.name,
                   f'{len(stat_lines)} different stat lines; using the most numerous model\'s')
    stats = json.loads(stat_lines.most_common(1)[0][0])
    toughness, save, wounds = _int(stats.get('T')), _plus(stats.get('Sv')), _int(stats.get('W'))
    if None in (toughness, save, wounds):
        report.add('skipped_unit', faction, entry.name, f'unreadable T/Sv/W: {stats}')
        return None

    # Abilities, rules and the defensive values the maths uses
    ability_lines, rules, modifiers, fnp = [], [], [], None
    sources = [entry] + [n.entry for n in sel.walk() if n.entry.kind in ('model', 'upgrade') and n.entry is not entry]
    seen_abilities = set()
    for source in sources:
        for profile in profiles(source, report):
            if profile.get('typeName') in ('Abilities',) and profile['name'] not in seen_abilities:
                seen_abilities.add(profile['name'])
                text = ability_text(profile)
                ability_lines.append(f'{profile["name"]}: {text}')
                for pattern, effect in DEFENSIVE_TEXT:
                    m = pattern.match(plain_text(text))
                    if m and effect == 'fnp':
                        fnp = min(fnp or 7, int(m.group(1)))
                    elif m and effect not in modifiers:
                        modifiers.append(effect)
        for rule in rule_links(source):
            if rule not in rules:
                rules.append(rule)
    for rule in rules:
        m = re.match(r'^Feel No Pain (\d)\+', rule)
        if m:
            fnp = min(fnp or 7, int(m.group(1)))
        if rule == 'Stealth' and 'Stealth' not in modifiers:
            modifiers.append('Stealth')

    can_lead = []
    for line in ability_lines:
        if line.startswith('Leader:'):
            can_lead.extend(_leader_targets(line))

    # Loadout at the default size: weapon profiles carried by each selected entry
    loadout = {'ranged': Counter(), 'melee': Counter()}
    weapons = {}
    for node in sel.walk():
        if node.entry.kind == 'group':
            continue
        carriers = node.count * node.multiplier() if node is not sel else 1
        for profile in profiles(node.entry, report):
            kind = WEAPON_TYPES.get(profile.get('typeName'))
            if kind:
                record = weapon_record(profile, kind)
                key = (kind, _stat_key(record))
                weapons.setdefault(key, record)
                loadout[kind][key] += carriers

    options = {'ranged': [], 'melee': []}
    for profile in _all_weapon_profiles(entry, report):
        kind = WEAPON_TYPES[profile['typeName']]
        record = weapon_record(profile, kind)
        key = (kind, _stat_key(record))
        weapons.setdefault(key, record)
        if key not in loadout[kind] and key not in options[kind]:
            options[kind].append(key)

    for key, record in weapons.items():  # Reported once per faction and weapon, not per unit
        if record['strength'] is None:
            report.add('weapon_stats', faction, None, f'{record["name"]}: unreadable strength, skipped')
        if known_keywords is not None and record['abilities']:
            unknown = check_keywords(', '.join(record['abilities']), known_keywords)['unknown']
            for keyword in unknown:
                report.add('unknown_keyword', faction, None, f'{record["name"]}: "{keyword}"')
    weapons = {k: r for k, r in weapons.items() if r['strength'] is not None}

    # Points for every legal size, from the minimum configuration up
    sizes, skipped = [], False
    current = sel
    while True:
        cost, was_skipped = points(current, faction_cat)
        skipped = skipped or was_skipped
        models = current.models()
        if not sizes or models > sizes[-1]['models']:
            sizes.append({'models': models, 'points': cost})
        if not grow(current, report) or current.models() > 100:
            break
    if skipped:
        report.add('points', faction, entry.name, 'some cost modifiers could not be evaluated; base costs used')

    name = entry.name
    unit = {
        'bsdata_id': entry.id,
        'name': name,
        'legends': '[Legends]' in name,
        'toughness': toughness,
        'save': save,
        'wounds': wounds,
        'invulnerable_save': _plus(stats.get('InSv')),
        'feel_no_pain': fnp,
        'move': (stats.get('M') or '').replace('"', '') if re.search(r'\d', stats.get('M') or '') else None,
        'oc': _int(stats.get('OC')),
        'leadership': _plus(stats.get('LD')),
        'points_cost': sizes[0]['points'],
        'default_models': default_models,
        'points_by_size': sizes,
        'keywords': [n for n in entry.category_names() if not n.startswith('Faction:')],
        'modifiers': modifiers,
        'can_lead': can_lead,
        'abilities': '\n\n'.join(ability_lines + [r for r in rules if r != 'Leader']),
        '_loadout': {kind: [(key, qty) for key, qty in loadout[kind].items() if key in weapons] for kind in loadout},
        '_options': {kind: [key for key in options[kind] if key in weapons] for kind in options},
    }
    return unit, weapons


def _all_weapon_profiles(entry, report, seen=None):
    """Every weapon profile reachable below an entry (the unit's wargear options)."""
    seen = seen if seen is not None else set()
    out = []
    if entry.id in seen:
        return out
    seen.add(entry.id)
    for profile in profiles(entry, report):
        if profile.get('typeName') in WEAPON_TYPES:
            out.append(profile)
    for child in entry.children(report):
        out.extend(_all_weapon_profiles(child, report, seen))
    return out


def _leader_targets(line):
    """Unit names from 'Leader: This model can be attached to the following unit(s): X, Y'."""
    m = re.search(r'attached to the following units?:\s*(.+)', plain_text(line), re.I | re.S)
    if not m:
        return []
    text = m.group(1).split('\n\n')[0]
    names = []
    for part in re.split(r'[\n,■•▪]+', text):
        part = part.strip(' .')
        if part and not part.lower().startswith(('can ', 'this ', 'if ', 'note')):
            names.append(part)
    return names


# ---------------------------------------------------------------------------
# Factions
# ---------------------------------------------------------------------------

def root_entries(cat, report):
    """Unit and model root entries of a catalogue (not those it imports)."""
    out = []
    for link in cat.root.get('entryLinks', []):
        entry = resolve_link(link, cat, report)
        if entry is not None and not entry.hidden and entry.kind in ('unit', 'model'):
            out.append(entry)
    for node in cat.root.get('selectionEntries', []):
        entry = Entry(node, cat)
        if not entry.hidden and entry.kind in ('unit', 'model'):
            out.append(entry)
    return out


def faction_categories(entry):
    return {n for n in entry.category_names() if n.startswith('Faction:')}


def home_categories(cat, own, imported, catalogues):
    """
    The catalogue's own 'Faction: X' categories: those on at least half of the units it
    defines itself; when it defines none, of its root entries from libraries (Aeldari,
    Astra Militarum); when those are all allies too (Chaos Knights links Chaos Space Marines
    units), of the imported library whose name best matches its own. Units from other
    armies' catalogues are allies.
    """
    basis = [e for e in own if e.cat is cat] or [e for e in own if e.cat.is_library]
    if not basis:
        libraries = {e.cat for e in imported if e.cat.is_library}
        best = max(libraries, key=lambda lib: _name_match(cat, lib, catalogues), default=None)
        basis = [e for e in imported if e.cat is best]
    counts = Counter(c for e in basis for c in faction_categories(e))
    return {c for c, n in counts.items() if n * 2 >= len(basis)}


def _name_match(cat, library, catalogues):
    """Words shared by two catalogue names, rarer words counting more ('Daemons' over 'Chaos')."""
    def words(c):
        return set(re.findall(r"[a-z']+", faction_name(c).lower())) - {'library'}
    frequency = Counter(w for c in catalogues.values() for w in words(c))
    return sum(1 / frequency[w] for w in words(cat) & words(library))


def faction_members(catalogues, report):
    """[(faction catalogue, [unit entries])] following the rules in the module docstring."""
    own = {cid: root_entries(cat, report) for cid, cat in catalogues.items()}
    claimed = set()
    factions = []
    for cid, cat in catalogues.items():
        if cat.is_library:
            continue
        imported = [e for link in cat.links() if link.get('importRootEntries') and link['targetId'] in catalogues
                    for e in own[link['targetId']]]
        candidates = own[cid] + imported
        home = home_categories(cat, own[cid], imported, catalogues)
        # Units it defines itself, plus linked and imported ones that belong to its army
        members = _unique([e for e in candidates if e.cat is cat or faction_categories(e) & home])
        if not members:
            report.add('skipped_faction', faction_name(cat), None, 'no units of its own')
            continue
        claimed.update(id(e.node) for e in members)
        factions.append((cat, members))
    # Libraries' root entries no faction took: they join the faction that took the most of
    # that library, or else form a faction of their own (Unaligned Forces' fortifications)
    for cid, cat in catalogues.items():
        if not cat.is_library:
            continue
        rest = _unique([e for e in own[cid] if id(e.node) not in claimed])
        if not rest:
            continue
        taken = Counter()
        for index, (_, members) in enumerate(factions):
            taken[index] = sum(1 for e in members if e.cat is cat)
        best, count = taken.most_common(1)[0] if taken else (None, 0)
        if count:
            factions[best][1].extend(rest)
        else:
            factions.append((cat, rest))
    return sorted(factions, key=lambda f: f[0].name.lower())


def _unique(entries):
    seen, out = set(), []
    for e in entries:
        if id(e.node) not in seen:
            seen.add(id(e.node))
            out.append(e)
    return out


def faction_name(cat):
    """'Imperium - Adeptus Astartes - Blood Angels' -> 'Blood Angels'; 'Library - Titans' -> 'Titans'."""
    name = re.sub(r'\s*-?\s*Library$', '', cat.name)
    return name.rsplit(' - ', 1)[-1]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def import_folder(folder, known_keywords=None):
    """
    Read a BSData folder into the data JSON's reference sections (with integer IDs and BSData IDs).

    `known_keywords`: keyword names to check weapon keywords against (unknown ones are
    reported); None skips the check.
    """
    game_system, catalogues = read_folder(folder)
    report = Report()
    data = {'factions': [], 'units': [], 'ranged_weapons': [], 'melee_weapons': [], 'detachments': []}

    for faction_id, (cat, members) in enumerate(faction_members(catalogues, report), start=1):
        name = faction_name(cat)
        data['factions'].append({'id': faction_id, 'bsdata_id': cat.id, 'name': name,
                                 'army_rules': [_rule_text(r) for r in cat.root.get('rules', []) + cat.root.get('sharedRules', [])
                                                if not r.get('hidden')]})
        for detachment in detachments(cat, members, catalogues, report):
            data['detachments'].append(dict(detachment, id=len(data['detachments']) + 1, faction_id=faction_id))
        weapon_ids = {}  # (kind, stat key) -> weapon record id within this faction
        for entry in sorted(members, key=lambda e: e.name.lower()):
            built = build_unit(entry, cat, report, known_keywords)
            if built is None:
                continue
            unit, weapons = built
            for key, record in weapons.items():
                kind = key[0]
                if key not in weapon_ids:
                    table = data[f'{kind}_weapons']
                    record = dict(record, id=len(table) + 1, faction_id=faction_id)
                    table.append(record)
                    weapon_ids[key] = record
                elif record['bsdata_id'] < weapon_ids[key]['bsdata_id']:
                    weapon_ids[key]['bsdata_id'] = record['bsdata_id']  # Lowest ID: stable whatever the order
            loadout, options = unit.pop('_loadout'), unit.pop('_options')
            unit.update(id=len(data['units']) + 1, faction_id=faction_id)
            for kind in ('ranged', 'melee'):
                unit[f'{kind}_weapons'] = [{'weapon_id': weapon_ids[key]['id'], 'qty': qty} for key, qty in loadout[kind]]
                unit[f'{kind}_options'] = [weapon_ids[key]['id'] for key in options[kind]]
            data['units'].append(unit)
        _check_leaders(data, faction_id, name, report)

    source = {
        'game_system': {'name': game_system.name, 'revision': game_system.root.get('revision')},
        'catalogues': {cat.name: cat.root.get('revision') for cat in catalogues.values()},
    }
    return ImportResult(data=data, report=report.items, source=source)


def _rule_text(rule):
    return {'name': rule.get('name', ''), 'text': tidy_text(rule.get('description'))}


def detachments(cat, members, catalogues, report):
    """[{'bsdata_id', 'name', 'rules': [{'name', 'text'}]}] for a faction (see the module docstring)."""
    sources = {cat} | {e.cat for e in members}
    roots = [(cat, node) for node in cat.root.get('entryLinks', []) + cat.root.get('selectionEntries', [])]
    for link in cat.links():
        if link.get('importRootEntries') and link['targetId'] in catalogues:
            linked = catalogues[link['targetId']]
            roots += [(linked, node) for node in linked.root.get('entryLinks', []) + linked.root.get('selectionEntries', [])]

    found, seen = [], set()
    for owner, node in roots:
        entry = resolve_link(node, owner, report) if 'targetId' in node else Entry(node, owner)
        if entry is None or entry.name not in ('Detachment', 'Detachments') or entry.cat not in sources:
            continue
        for group in entry.children(report):
            if group.kind != 'group':
                continue
            for option in group.children(report):
                if option.kind == 'group' or option.id in seen:
                    continue
                seen.add(option.id)
                rules = [_rule_text(r) for r in option.merged('rules') if not r.get('hidden')]
                for info in option.merged('infoLinks'):
                    if info.get('type') == 'rule':
                        target, _ = option.cat.find(info.get('targetId'))
                        if target is not None:
                            rules.append(_rule_text(target))
                rules += [{'name': p['name'], 'text': ability_text(p)} for p in profiles(option, report)
                          if p.get('typeName') == 'Abilities']
                found.append({'bsdata_id': option.id, 'name': option.name, 'rules': rules})
    return found


def _check_leaders(data, faction_id, faction, report):
    """Match Leader targets to the faction's unit names (case-insensitively); report the rest."""
    names = {u['name'].lower(): u['name'] for u in data['units'] if u['faction_id'] == faction_id}
    for unit in data['units']:
        if unit['faction_id'] != faction_id:
            continue
        matched = []
        for target in unit['can_lead']:
            canonical = names.get(target.lower())
            if canonical:
                matched.append(canonical)
            else:
                report.add('leader', faction, unit['name'], f'can lead "{target}", which is not in this faction')
        unit['can_lead'] = matched
